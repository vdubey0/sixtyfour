"""HTTP entry points used by the editor.

The browser sends a graph; create_flow validates it and process_flow streams
execution events back. Tool implementations stay in blocks/, not in these routes.
"""
import json
import re
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from backend.blocks.catalog import CATALOG
from backend.blocks.sixtyfour import ROOT, setting
from backend.schemas import FinalRequest, ExecuteResponse
from backend.engine.build_flow import create_flow
from backend.engine.execution import process_flow

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://localhost:5174', 'http://127.0.0.1:5173', 'http://127.0.0.1:5174'],
                   allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


@app.get('/blocks')
def blocks():
    """Send UI definitions and key availability, never the actual API key."""
    key = setting('SIXTYFOUR_API_KEY')
    return {'blocks': CATALOG, 'api_configured': bool(key and key != 'replace_with_your_key')}


@app.post('/execute_stream')
def execute_stream(req: FinalRequest):
    """Validate the whole graph before starting work, then stream one JSON event per line."""
    order = create_flow(req.blocks, req.connections)
    # Validation errors use the same event format as errors during execution.
    if isinstance(order, ExecuteResponse):
        return StreamingResponse(iter([json.dumps({'event': 'error', 'blockId': None, 'message': order.error}) + '\n']), media_type='application/x-ndjson')
    return StreamingResponse(process_flow(order), media_type='application/x-ndjson', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/uploads')
async def upload(request: Request):
    """Store uploaded bytes and return a project-relative path for Read CSV."""
    # Raw CSV upload avoids adding a multipart dependency to the existing stack.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 10 * 1024 * 1024:
            raise HTTPException(413, 'CSV uploads are limited to 10 MB')
    if not body:
        raise HTTPException(400, 'CSV is empty')
    try:
        body.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(400, 'Upload a UTF-8 CSV')
    # A generated filename avoids collisions; CSV headers are checked by Read CSV.
    path = ROOT / 'inputs' / 'uploads' / (uuid.uuid4().hex + '.csv')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {'path': str(path.relative_to(ROOT))}


@app.get('/downloads/{run_id}/{filename}')
def download(run_id: str, filename: str):
    """Serve only CSV files inside the requested run's output directory."""
    if not re.fullmatch(r'[a-f0-9]{32}', run_id) or '/' in filename or '\\' in filename:
        raise HTTPException(404, 'File not found')
    directory = (ROOT / 'outputs' / 'runs' / run_id).resolve()
    path = (directory / filename).resolve()
    # Resolve first so path traversal or a symlink cannot escape this run directory.
    if path.parent != directory or path.suffix.lower() != '.csv' or not path.is_file():
        raise HTTPException(404, 'File not found')
    return FileResponse(path, media_type='text/csv', filename=filename[4:] if re.match(r'^\d{3}-', filename) else filename)
