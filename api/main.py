"""Unified API: policy administration and Rasa-backed chat."""
import hmac
import json
import os
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional in local runtime
    load_dotenv = None

from hr_policy.store import MAX_BYTES, index_path, update_pdf
from hr_policy.engine import intent_mapping

app = FastAPI(title='HR Policy API', version='1.0.0', description='Upload company policies and chat through Rasa. Set HR_API_KEY to protect API routes.')


ROOT_DIR = Path(__file__).resolve().parents[1]
if load_dotenv is not None:
    load_dotenv(ROOT_DIR / '.env', override=False)

PDF_UPLOAD_DIR = ROOT_DIR / 'policies' / 'pdfs'
PDF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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


async def _enrich_policy_chunk(payload, question):
    # Only expose evidence actually selected by the Rasa action. Never retrieve again.
    sources = []
    for message in payload.get('messages', []):
        custom = message.get('custom', {})
        if isinstance(custom, dict):
            sources.extend(custom.get('sources', []))
            if custom.get('intent'):
                payload['intent'] = custom['intent']
            if custom.get('status'):
                payload['status'] = custom['status']
    payload['sources'] = sources
    payload['chunk'] = sources[0] if sources else None
    return payload


@app.get('/policies/pdfs/{filename}', dependencies=[Depends(authorize)])
def download_policy(filename: str):
    path = index_path()
    try:
        metadata = json.loads(path.read_text()).get('documents', {}).get(filename)
        if not metadata:
            raise HTTPException(404, 'No active PDF with this filename')
        target = (path.parent / metadata['pdf_path']).resolve()
        if not target.is_relative_to((path.parent / 'documents').resolve()):
            raise HTTPException(404, 'Invalid document location')
        return FileResponse(target, media_type='application/pdf', filename=filename)
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, 'Policy document unavailable') from exc


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
    result = await rasa_chat(body.sender, body.message)
    return await _enrich_policy_chunk(result, body.message)


@app.post('/chat/stream', dependencies=[Depends(authorize)])
async def chat_stream(body: ChatRequest):
    result = await rasa_chat(body.sender, body.message)
    result = await _enrich_policy_chunk(result, body.message)
    answer = result['answer']

    def stream():
        for i in range(0, len(answer), 80):
            yield f"data: {json.dumps({'sender': body.sender, 'text': answer[i:i + 80], 'done': False})}\n\n"
        yield f"data: {json.dumps({'sender': body.sender, 'answer': answer, 'messages': result['messages'], 'chunk': result.get('chunk'), 'done': True})}\n\n"

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


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
        result = await _enrich_policy_chunk(result, message)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, {'error': exc.detail, 'policy_update': update}) from exc
    return {**result, 'policy_update': update}


@app.get('/policies/intents', dependencies=[Depends(authorize)])
def policy_intents():
    """Reviewed intent -> PDF -> section mappings, not model predictions."""
    return intent_mapping()


class IntentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.post('/nlu/parse', dependencies=[Depends(authorize)])
async def parse_intent(body: IntentRequest):
    if not body.message.strip():
        raise HTTPException(422, 'message cannot be blank')
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(os.getenv('RASA_URL','http://127.0.0.1:5005').rstrip('/')+'/model/parse', json={'text':body.message})
            response.raise_for_status()
        parsed = response.json()
        name = parsed.get('intent',{}).get('name')
        return {'intent':parsed.get('intent'), 'intent_ranking':parsed.get('intent_ranking',[]), 'policy_mapping':intent_mapping().get(name)}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, 'Rasa NLU unavailable; run Rasa with --enable-api on its private interface') from exc
