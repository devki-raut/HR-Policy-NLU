"""Unified API: policy administration and Rasa-backed chat."""
import hmac
import json
import os
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from hr_policy.store import MAX_BYTES, index_path, update_pdf

app = FastAPI(title='HR Policy API', version='1.0.0', description='Upload company policies and chat through Rasa. Set HR_API_KEY to protect API routes.')


STATIC = Path(__file__).resolve().parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')

@app.middleware('http')
async def ui_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    # Scope the tab CSP to UI assets so Swagger/ReDoc retain their own scripts.
    if request.url.path == '/' or request.url.path.startswith('/static/'):
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://res.cdn.office.net; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors https://teams.microsoft.com https://*.teams.microsoft.com https://*.cloud.microsoft; base-uri 'self'; form-action 'self'"
    return response

@app.get('/', include_in_schema=False)
def home():
    return FileResponse(STATIC / 'index.html')


def authorize(x_api_key: str = Header(default='')):
    expected = os.getenv('HR_API_KEY', '')
    if expected and not hmac.compare_digest(expected, x_api_key):
        raise HTTPException(401, 'Invalid API key')


class ChatRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


async def rasa_chat(sender, message):
    if not sender.strip() or not message.strip():
        raise HTTPException(422, 'sender and message cannot be blank')
    url = os.getenv('RASA_URL', 'http://127.0.0.1:5005').rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f'{url}/webhooks/rest/webhook', json={'sender': sender, 'message': message})
            response.raise_for_status()
            messages = response.json()
        if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
            raise ValueError('Invalid Rasa response')
        return {'sender': sender, 'answer': '\n\n'.join(m['text'] for m in messages if isinstance(m.get('text'), str)), 'messages': messages}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, 'Rasa response timed out') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, 'Rasa is unavailable or returned an invalid response. Start make serve and make actions.') from exc


async def upload_policy(file, mode):
    try:
        content = await file.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise HTTPException(413, 'PDF exceeds 20 MiB')
        return await run_in_threadpool(update_pdf, file.filename, content, mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


@app.get('/health')
def health():
    return {'status': 'ok', 'policy_index_exists': index_path().exists()}


@app.get('/policies', dependencies=[Depends(authorize)])
def policies():
    path = index_path()
    if not path.exists():
        return {'library': 'empty', 'documents': [], 'total_chunks': 0}
    try:
        payload = json.loads(path.read_text())
        counts = {}
        for chunk in payload['chunks']:
            counts[chunk['source']] = counts.get(chunk['source'], 0) + 1
        return {'library': payload.get('library', 'legacy/demo'), 'documents': [{'filename': name, 'chunks': count} for name, count in sorted(counts.items())], 'total_chunks': len(payload['chunks'])}
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(503, 'Policy index unavailable') from exc


@app.post('/policies/upload', dependencies=[Depends(authorize)])
async def upload(file: UploadFile = File(...), mode: str = Form('upsert', regex='^(upsert|replace)$')):
    return await upload_policy(file, mode)


@app.post('/chat', dependencies=[Depends(authorize)])
async def chat(body: ChatRequest):
    return await rasa_chat(body.sender, body.message)


@app.post('/ask', dependencies=[Depends(authorize)])
async def ask(sender: str = Form(..., min_length=1, max_length=128),
              message: str = Form(..., min_length=1, max_length=4000),
              file: UploadFile | None = File(None),
              mode: str = Form('upsert', regex='^(upsert|replace)$')):
    """Optionally publish a PDF, then ask Rasa in one multipart request.

    Upload commits before chat. A downstream chat failure does not undo publication.
    """
    if not sender.strip() or not message.strip():
        raise HTTPException(422, 'sender and message cannot be blank')
    update = await upload_policy(file, mode) if file is not None else None
    try:
        result = await rasa_chat(sender, message)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, {'error': exc.detail, 'policy_update': update}) from exc
    return {**result, 'policy_update': update}
