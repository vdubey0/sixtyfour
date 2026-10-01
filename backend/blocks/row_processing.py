"""Shared row scheduling, result merging, evidence, and failure handling.

Tool modules supply request/response callbacks; this module knows no endpoints.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from backend.engine.compatibility import validate_row
from backend.blocks.sixtyfour import APIError, SixtyfourClient, clean_value, cell, missing


# Distinguish intentional skips from malformed null API responses.
SKIP_ROW = object()


def mapped_row(row, mapping):
    """Build provider input using API-field → table-column mappings.

    Without a mapping, send nonempty public columns; underscore-prefixed columns
    are internal evidence/lineage and must not become lookup identifiers.
    """
    if mapping:
        return {key: clean_value(row[column]) for key, column in mapping.items() if not missing(row[column])}
    return {k: clean_value(v) for k, v in row.items() if not k.startswith('_') and not missing(v)}


def process_rows(df, config, context, kind, request, parse_response=None,
                 required_columns=(), expand_rows=None, output_columns=None):
    # request submits a row or returns SKIP_ROW; parse_response extracts fields.
    # expand_rows optionally replaces one input row with several output rows.
    operations = (request, parse_response, required_columns, expand_rows, output_columns)
    if len(df) <= 1 or config.get('max_workers', 32) == 1:
        return _process_rows_serial(df, config, context, kind, *operations)
    stopped = threading.Event()

    # Each worker shares the run ID/progress channel but can observe a fatal error
    # in a sibling worker. Existing remote jobs are not cancelled at the provider.
    class RowContext:
        def __init__(self, number):
            self.number = number
            self.block_id = context.block_id
            self.mode = getattr(context, 'mode', 'unknown')

        def check_cancelled(self):
            context.check_cancelled()
            if stopped.is_set():
                # Import at execution time to avoid a registry import cycle.
                from backend.engine.execution import Cancelled
                raise Cancelled('Block stopped after a fatal error')

        def wait(self, seconds):
            stopped.wait(seconds)
            self.check_cancelled()

        def progress(self, message):
            self.check_cancelled()
            if not message.startswith('Processed '):
                context.progress('Row %s: %s' % (self.number, message))

    # Futures finish out of order; index slots restore the original input order.
    frames = [None] * len(df)
    failures = 0
    with ThreadPoolExecutor(max_workers=config.get('max_workers', 32)) as pool:
        futures = {pool.submit(_process_rows_serial, df.iloc[i:i+1].copy(), config, RowContext(i+1), kind, *operations): i for i in range(len(df))}
        try:
            for completed, future in enumerate(as_completed(futures), 1):
                frame = future.result()
                frames[futures[future]] = frame
                failures += frame.attrs.get('failed_rows', 0)
                context.progress('Processed %s/%s rows (%s failed)' % (completed, len(df), failures))
        except Exception:
            stopped.set()
            for future in futures:
                future.cancel()
            raise
    result = pd.concat(frames, ignore_index=True, sort=False)
    result.attrs['failed_rows'] = failures
    return result


def _process_rows_serial(df, config, context, kind, request, parse_response,
                         required_columns, expand_rows, output_columns):
    mapping = config.get('input_mapping', {})
    # Validate columns even for empty input, but avoid creating an API client then.
    needed = list(mapping.values()) + list(required_columns)
    absent = set(needed) - set(df.columns)
    if absent:
        raise ValueError('Missing columns: ' + ', '.join(sorted(absent)))
    client = SixtyfourClient(context) if len(df) else None
    output = []
    failures = 0

    def remember(row):
        # Keep evidence and earlier failures even when a later API block replaces
        # the current-step metadata columns. The full history is exported to CSV.
        try:
            history = json.loads(row.get('_history') or '[]')
        except (ValueError, TypeError):
            history = []
        if not isinstance(history, list):
            history = []
        history.append({'block_id': context.block_id, 'tool': kind,
                        'status': row.get('_status'), 'error': row.get('_error'),
                        'response': row.get('_raw_response')})
        row['_history'] = cell(history)

    for number, (_, original) in enumerate(df.iterrows(), 1):
        context.check_cancelled()
        row = {k: clean_value(v) for k, v in original.items()}
        # Reset current-step evidence; _history retains evidence from previous steps.
        row['_error'] = None
        row['_raw_response'] = None
        for name in ('references', 'notes', 'confidence_score', 'field_confidence'):
            row['_' + name] = None
        try:
            lead = mapped_row(row, mapping)
            validate_row(kind, row, lead, config, getattr(context, 'mode', 'unknown'))
            result = request(client, row, lead, config)
            if result is SKIP_ROW:
                row['_status'] = 'skipped'
                remember(row)
                output.append(row)
                continue
            row['_raw_response'] = cell(result)
            if not isinstance(result, dict):
                raise APIError('Expected an object response; raw result retained')
            for name in ('references', 'notes', 'confidence_score', 'field_confidence'):
                row['_' + name] = cell(result.get(name))
            # Expansion is all-or-nothing for a row: the callback builds its complete
            # list before any of its output is appended to the table.
            if expand_rows is not None:
                expanded_rows = expand_rows(row, result)
                for expanded in expanded_rows:
                    remember(expanded)
                output.extend(expanded_rows)
                if not expanded_rows:
                    context.progress('No people found for row ' + str(number))
            else:
                values = parse_response(result)
                for key, value in values.items():
                    if key.startswith('_'):
                        continue
                    # Default enrichment fills gaps; replacing known values is opt-in.
                    if key not in row or missing(row[key]) or config.get('overwrite'):
                        row[key] = cell(value)
                row['_status'] = 'completed'
                remember(row)
                output.append(row)
        except (APIError, ValueError) as exc:
            # Authentication/credit failures stop the run even when row failures continue.
            if isinstance(exc, APIError) and exc.status in (401, 402, 403):
                raise
            if not config.get('continue_on_error', True):
                raise
            failures += 1
            row['_status'] = 'failed'
            row['_error'] = str(exc)
            if expand_rows is not None:
                row['_parent_row_id'] = row['_row_id']
                row['_entity_mode'] = 'companies'
            remember(row)
            output.append(row)
        context.progress('Processed %s/%s rows (%s failed)' % (number, len(df), failures))
    # Keep a usable schema for empty results, including requested output fields.
    columns = list(df.columns)
    if output_columns is not None:
        columns = output_columns
    elif 'struct' in config:
        columns += [k for k in config['struct'] if k not in columns]
    result_df = pd.DataFrame(output) if output else pd.DataFrame(columns=columns)
    for name in columns:
        if name not in result_df.columns:
            result_df[name] = None
    result_df.attrs['failed_rows'] = failures
    return result_df


def require_identifiers(lead):
    """Avoid a lookup when mapping produced no usable input fields."""
    if not lead:
        raise ValueError('No usable identifiers in this row')


def structured_values(result):
    """Require the structured_data object returned by research endpoints."""
    if not isinstance(result.get('structured_data'), dict):
        raise APIError('Enrichment response is missing structured_data')
    return dict(result['structured_data'])


def provider_values(result):
    """Preserve open-ended provider fields without inventing a response schema."""
    if isinstance(result.get('structured_data'), dict):
        return dict(result['structured_data'])
    return {k: v for k, v in result.items()
            if k not in ('references', 'notes', 'confidence_score', 'field_confidence')}
