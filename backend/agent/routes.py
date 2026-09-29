"""Session-local agent API. Run one server worker; restarting clears conversations."""
import asyncio
import json
import re
from collections import OrderedDict
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from backend.agent.graph import AgentRun, execute_run
from backend.agent.limits import Limits
from backend.agent.model import configuration
from backend.agent.jev import configuration as jev_configuration
from backend.agent.schemas import RunRequest
from backend.blocks.read_csv import resolve_csv

router = APIRouter(prefix='/agent')
RUNS = OrderedDict()
MAX_SESSIONS = 32
RUN_TASKS = set()


@router.get('/config')
def config():
    return {**configuration(), 'jev': jev_configuration(), 'limits': Limits.configured().public()}


@router.post('/runs')
async def start(req: RunRequest):
    if not req.query.strip():
        raise HTTPException(400, 'Describe what you want to build.')
    if not configuration()['configured']:
        raise HTTPException(503, 'Set OPENAI_API_KEY and AGENT_MODEL in backend/.env to use agent mode.')
    if req.run_id:
        run = RUNS.get(req.run_id)
        if not run:
            raise HTTPException(404, 'This session expired or the server restarted. Start a new request.')
        if run.busy or run.status != 'needs_input':
            raise HTTPException(409, 'Only a paused clarification can resume. Start a new request for a new task.')
        if req.upload_path:
            raise HTTPException(400, 'Start a new request to use another CSV.')
        if 'use_jev' in req.model_fields_set and req.use_jev != run.use_jev:
            raise HTTPException(409, 'The Jev setting is fixed for this run. Start a new chat to change it.')
        run.conversation.append({'role': 'user', 'content': req.query})
        run.status, run.reason, run.message = 'running', '', ''
        run.final = None
    else:
        if req.upload_path:
            if not re.fullmatch(r'inputs/uploads/[a-f0-9]{32}\.csv', req.upload_path):
                raise HTTPException(400, 'Use the CSV upload control to attach a file.')
            try:
                resolve_csv(req.upload_path)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if len(RUNS) >= MAX_SESSIONS:
            # Preserve active and paused sessions; only finished sessions are evictable.
            removable = next((key for key, item in RUNS.items()
                              if not item.busy and item.status != 'needs_input'), None)
            if removable is None:
                raise HTTPException(429, 'Too many active sessions. Stop an existing run first.')
            del RUNS[removable]
        run = AgentRun(req.query, req.upload_path, use_jev=req.use_jev)
        RUNS[run.id] = run
    run.busy = True

    async def stream():
        run.loop = asyncio.get_running_loop()
        run.sink = asyncio.Queue()
        worker = asyncio.create_task(execute_run(run))
        RUN_TASKS.add(worker)
        worker.add_done_callback(RUN_TASKS.discard)
        completed = False
        try:
            while True:
                try:
                    event = await asyncio.wait_for(run.sink.get(), timeout=10)
                except asyncio.TimeoutError:
                    if worker.done():
                        # execute_run always produces a terminal event, including errors.
                        event = run.final or {'event': 'complete', 'status': 'failed', 'message': 'Execution ended.'}
                    else:
                        event = {'event': 'heartbeat'}
                yield json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n'
                if event['event'] == 'complete':
                    completed = True
                    break
        finally:
            if not completed and not worker.done():
                run.context.cancelled.set()
            # Retain worker so cancellation can export the last completed snapshot.
            worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    return StreamingResponse(stream(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/runs/{run_id}/cancel')
async def cancel(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, 'Run not found.')
    run.context.cancelled.set()
    if not run.busy and run.status == 'needs_input':
        run.status, run.reason = 'cancelled', 'cancelled'
        run.message = 'Paused run cancelled.'
        if run.final:
            run.final.update(status=run.status, reason=run.reason, message=run.message)
    return {'status': 'cancelling' if run.busy else run.status}


@router.get('/runs/{run_id}')
async def status(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, 'Run not found.')
    return run.final or {'run_id': run.id, 'status': run.status, 'budgets': run.budget()}
