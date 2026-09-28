"""Per-run state and incremental NDJSON events; no global DataFrames."""
import asyncio
import json
import queue
import threading
import time
import uuid
import pandas as pd
from backend.blocks.sixtyfour import ROOT, clean_value, setting
from backend.engine.registry import get_block_function
from backend.engine.compatibility import DatasetSchema, validate_input, output_schema


class Cancelled(RuntimeError):
    """Control-flow signal that stops the run rather than marking a row failed."""
    pass


class RunContext:
    """State shared by blocks within one run, never between separate runs.

    snapshots holds previous tables for Merge; downloads accumulates exports;
    metadata is reset for each block. emit sends progress to the HTTP stream.
    """
    def __init__(self, emit, cancelled=None):
        self.run_id = uuid.uuid4().hex
        self.emit = emit
        self.cancelled = cancelled or threading.Event()
        self.block_id = None
        self.block_index = 0
        self.snapshots = {}
        self.schema_snapshots = {}
        self.mode = 'unknown'
        self.columns = set()
        self.downloads = []
        self.metadata = {}

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise Cancelled('Run stopped')

    def wait(self, seconds):
        """Pause polling/retries, but wake immediately when cancellation is signaled."""
        self.cancelled.wait(seconds)
        self.check_cancelled()

    def progress(self, message):
        self.check_cancelled()
        self.emit({'event': 'block_progress', 'blockId': self.block_id, 'message': message})

    def output_path(self, filename):
        """Separate exports by run and step so multiple Save blocks do not collide."""
        directory = ROOT / 'outputs' / 'runs' / self.run_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / ('%03d-' % self.block_index + filename)


def execute(order, context):
    """Pass one table through the ordered blocks and emit their results."""
    df = pd.DataFrame()
    total_failures = 0
    context.emit({'event': 'run_started', 'run_id': context.run_id})
    for index, block in enumerate(order):
        context.check_cancelled()
        context.block_id = block.id
        context.block_index = index
        context.metadata = {}
        context.emit({'event': 'block_running', 'blockId': block.id, 'type': block.type})
        try:
            schema = DatasetSchema(context.mode, set(df.columns))
            validate_input(block.type, block.config, schema)
            predicted = output_schema(block.type, block.config, schema, context.schema_snapshots)
            # Failure counts belong to this step, not the preceding table.
            df.attrs = {}
            df = get_block_function(block.type)(df.copy(), block.config, context)
            if not isinstance(df, pd.DataFrame):
                raise ValueError('Block did not return a table')
            if not df.columns.is_unique:
                raise ValueError('Block returned duplicate column names')
            df = df.reset_index(drop=True)
            # Keep existing identities when valid; sources need IDs assigned here.
            if '_row_id' not in df.columns or not df['_row_id'].is_unique or df['_row_id'].isna().any():
                df['_row_id'] = [block.id + ':' + str(i) for i in range(len(df))]
            # Preserve this step for later Merge blocks before advancing the table.
            context.mode = predicted.mode
            context.columns = set(df.columns)
            context.schema_snapshots[block.id] = DatasetSchema(context.mode, set(context.columns))
            context.snapshots[block.id] = df.copy()
            failed = int(df.attrs.get('failed_rows', 0))
            total_failures += failed
            # Stream a small preview, not the entire table; full data stays on the server.
            context.emit(clean_value({
                'event': 'block_done', 'blockId': block.id, 'type': block.type,
                'row_count': len(df), 'columns': list(df.columns), 'mode': context.mode,
                'preview': df.head(20).to_dict('records'), 'failed_rows': failed,
                'metadata': context.metadata, 'downloads': context.downloads,
            }))
        except Cancelled:
            raise
        except Exception as exc:
            message = str(exc)
            key = setting('SIXTYFOUR_API_KEY')
            if key:
                message = message.replace(key, '[redacted]')
            context.emit({'event': 'error', 'blockId': block.id, 'message': message})
            return
    # Counts are failed row operations across steps, not unique people/companies.
    context.emit({'event': 'complete', 'run_id': context.run_id,
                  'row_count': len(df), 'failed_rows': total_failures,
                  'status': 'partial' if total_failures else 'completed',
                  'downloads': context.downloads})


async def process_flow(order):
    """Bridge synchronous Pandas/HTTP work to an asynchronous browser response.

    A worker thread executes blocks and puts events in a thread-safe queue.
    This generator drains the queue without blocking the server event loop.
    """
    events = queue.Queue()
    context = RunContext(events.put)

    def worker():
        try:
            execute(order, context)
        except Cancelled:
            pass
        finally:
            # Sentinel tells the streaming loop the worker is finished, even after errors.
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    last_event = time.monotonic()
    try:
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                # Long remote jobs still produce traffic so the connection stays active.
                if time.monotonic() - last_event >= 10:
                    yield json.dumps({'event': 'heartbeat'}) + '\n'
                    last_event = time.monotonic()
                await asyncio.sleep(0.1)
                continue
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n'
            last_event = time.monotonic()
    finally:
        # Disconnect stops further row submissions and polling. Already submitted
        # remote jobs may finish and be charged; this is not remote cancellation.
        context.cancelled.set()
