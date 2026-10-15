"""Unified API: policy administration and Rasa-backed chat."""
import json
import os
import re
from pathlib import Path
import sys
from urllib.parse import quote
from html import escape

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional in local runtime
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / '.env', override=False)

from actions.policy_store import MAX_BYTES, index_path, policy_pdf_dir, update_pdf
from actions.runtime_paths import runtime_path
from actions.storage import sync_from_cloud
from actions.policy_engine import intent_mapping
from actions.darwinbox_adapters.store import load_store
from botbuilder.schema import Activity
from teams_app.bot_package.adapter import ADAPTER
from teams_app.bot_package.bot_handler import BOT
from teams_app.web_package.auth import auth_required, client_id, request_identity, tenant_id, require_policy_admin
from teams_app.chat_service import rasa_chat, _enrich_policy_chunk, chat_sender
from teams_app.web_paths import PublicPrefixMiddleware, base_path, public_path

BASE_PATH = base_path()
app = FastAPI(title='EmployeeAssist API', version='1.0.0', description='Upload company policies and chat through Rasa. Jupyter uses separate token authentication.', root_path=BASE_PATH)
app.add_middleware(PublicPrefixMiddleware)

try:
    sync_from_cloud()
except Exception as exc:
    if os.getenv('STORAGE_BACKEND', 'local').strip().casefold() not in {'local', ''}:
        raise RuntimeError(f'Policy cloud storage could not be initialized: {exc}') from exc

from teams_app.web_package.jupyter import router as jupyter_router

app.include_router(jupyter_router)
from teams_app.web_package.ticket_mail import router as ticket_mail_router
app.include_router(ticket_mail_router)

STATIC = Path(__file__).resolve().parent / 'static'
APP_PACKAGE = PROJECT_ROOT / 'teams_app' / 'app_package'
POLICY_DOCUMENTS = policy_pdf_dir()
if os.getenv('POLICY_DOCUMENTS_DIR', '').strip():
    POLICY_DOCUMENTS = runtime_path('policies/pdfs', POLICY_DOCUMENTS, 'POLICY_DOCUMENTS_DIR')
POLICY_DOCUMENTS.mkdir(parents=True, exist_ok=True)
app.mount('/static', StaticFiles(directory=STATIC), name='static')
app.mount('/app-package', StaticFiles(directory=APP_PACKAGE), name='app-package')
app.mount(
    '/policy-documents',
    StaticFiles(directory=POLICY_DOCUMENTS),
    name='policy-documents',
)


class UIHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        path = scope.get('path', '/')
        if BASE_PATH and path.startswith(BASE_PATH + '/'):
            path = path[len(BASE_PATH):]
        is_ui = path in {'/', '/privacy-policy', '/terms-n-conditions'} or path.startswith(('/static/', '/app-package/', '/policy-documents/'))

        async def send_with_headers(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['X-Content-Type-Options'] = 'nosniff'
                headers['Cache-Control'] = 'no-store'
                if is_ui:
                    headers['Content-Security-Policy'] = (
                        "default-src 'self'; script-src 'self' https://res.cdn.office.net https://alcdn.msauth.net; "
                        "style-src 'self' 'unsafe-inline'; connect-src 'self' https://login.microsoftonline.com https://*.login.microsoftonline.com https://*.msauth.net https://graph.microsoft.com https://res.cdn.office.net; "
                        "frame-src 'self' https://login.microsoftonline.com https://*.login.microsoftonline.com; "
                        "img-src 'self' data: blob:; frame-ancestors https://teams.microsoft.com https://*.teams.microsoft.com https://*.cloud.microsoft; "
                        "base-uri 'self'; form-action 'self' https://login.microsoftonline.com"
                    )
                    headers['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(UIHeadersMiddleware)

def render_page(path):
    template = path.read_text(encoding='utf-8')
    return HTMLResponse(template.replace('<!-- APP_BASE -->', f'<base href="{escape(BASE_PATH + chr(47), quote=True)}">'))


@app.get('/', include_in_schema=False)
def home():
    return render_page(STATIC / 'index.html')


@app.get('/privacy-policy', include_in_schema=False)
def privacy_policy():
    return render_page(Path(__file__).resolve().parent / 'html' / 'privacy.html')


@app.get('/terms-n-conditions', include_in_schema=False)
def terms_and_conditions():
    return render_page(Path(__file__).resolve().parent / 'html' / 'terms.html')



class ChatRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    metadata: dict = Field(default_factory=dict)


def _guest_identity():
    return {
        'display_name': 'Guest',
        'channel': 'web_guest',
        'is_guest': True,
    }


async def _chat_context(request: Request, requested_sender: str, metadata: dict | None = None):
    # Public callers cannot assert Teams identities through JSON metadata.
    identity = await request_identity(request)
    identity = {**identity, 'channel': 'web_microsoft', 'is_guest': False} if identity else _guest_identity()
    return identity, chat_sender(identity, requested_sender)


@app.get('/auth/config', include_in_schema=False)
def auth_config():
    app_id = client_id()
    return {
        'enabled': bool(app_id),
        'ticket_recipient': os.getenv('TICKET_WEB_TEST_RECIPIENT', '').strip() or 'helpdesk@emergys.com',
        'required': auth_required(),
        'client_id': app_id,
        'tenant_id': tenant_id(),
        'redirect_uri': os.getenv('ENTRA_REDIRECT_URI', '').strip() or None,
        'scope': (os.getenv('ENTRA_API_RESOURCE', f'api://dockerpoc-webapp01.azurewebsites.net/botid-{app_id}').rstrip('/') + '/access_as_user') if app_id else None,
    }


@app.get('/auth/me', include_in_schema=False)
async def auth_me(request: Request):
    identity = await request_identity(request)
    if not identity:
        raise HTTPException(401, 'Microsoft sign-in is required')
    return identity


@app.get('/policies/pdfs/{filename}')
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
    try:
        employee_store = load_store()
        directory = {
            'configured': bool(
                (
                    os.getenv('DARWINBOX_BASE_URL', '').strip()
                    or os.getenv('DARWINBOX_EMPLOYEE_URL', '').strip()
                )
                and (
                    os.getenv('DARWINBOX_EMPLOYEE_DATASET_KEY', '').strip()
                    or os.getenv('DARWINBOX_DATASET_KEY', '').strip()
                )
                and all(os.getenv(name, '').strip() for name in (
                    'DARWINBOX_UID', 'DARWINBOX_ADMIN_EMAIL', 'DARWINBOX_SECRET_KEY'
                ))
            ),
            'employee_count': len(employee_store.get('employees') or {}),
            'last_sync': employee_store.get('synced_at'),
        }
    except RuntimeError:
        directory = {'configured': True, 'status': 'unreadable'}
    report_username = os.getenv('DARWINBOX_LEAVE_USERNAME', '').strip()
    report_password = os.getenv('DARWINBOX_LEAVE_PASSWORD', '').strip()
    return {
        'status': 'ok',
        'service': 'web-and-teams-bot',
        'policy_index_exists': index_path().exists(),
        'darwinbox_directory': directory,
        'darwinbox_employee_report': {
            'configured': bool(
                (
                    os.getenv('DARWINBOX_REPORT_URL', '').strip()
                    or os.getenv('DARWINBOX_BASE_URL', '').strip()
                )
                and os.getenv('DARWINBOX_REPORT_API_KEY', '').strip()
                and os.getenv('DARWINBOX_REPORT_ID', '').strip()
                and report_username and report_password
            ),
            'schedule': (
                f"{os.getenv('DARWINBOX_REPORT_SYNC_HOUR', '16')}:"
                f"{int(os.getenv('DARWINBOX_REPORT_SYNC_MINUTE', '30')):02d} Asia/Kolkata"
            ),
            'authorization': 'basic_auth_and_api_key',
        },
        'darwinbox_leave': {
            'configured': bool(
                os.getenv('DARWINBOX_LEAVE_API_KEY', '').strip()
                and os.getenv('DARWINBOX_BASE_URL', '').strip()
            )
        },
        'darwinbox_holidays': {
            'configured': bool(
                os.getenv('DARWINBOX_BASE_URL', '').strip()
                and os.getenv('DARWINBOX_HOLIDAY_API_KEY', '').strip()
                and os.getenv('DARWINBOX_LEAVE_USERNAME', '').strip()
                and os.getenv('DARWINBOX_LEAVE_PASSWORD', '').strip()
            ),
            'authorization': 'basic_auth_and_api_key',
        },
    }


@app.post('/api/messages', include_in_schema=False)
async def bot_messages(request: Request):
    if request.headers.get('content-type', '').split(';', 1)[0] != 'application/json':
        raise HTTPException(415, 'Expected application/json')
    activity = Activity().deserialize(await request.json())
    invoke_response = await ADAPTER.process_activity(
        activity,
        request.headers.get('authorization', ''),
        BOT.on_turn,
    )
    if invoke_response:
        return JSONResponse(invoke_response.body, status_code=invoke_response.status)
    return Response(status_code=201)



@app.get('/policies')
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


@app.post('/policies/upload')
async def upload(request: Request, file: UploadFile = File(...), mode: str = Form('upsert', pattern='^(upsert|replace)$')):
    await require_policy_admin(request)
    return await upload_policy(file, mode)


@app.post('/chat')
async def chat(body: ChatRequest, request: Request):
    identity, sender = await _chat_context(request, body.sender, body.metadata)
    result = await rasa_chat(sender, body.message, identity)
    result['user'] = identity
    return await _enrich_policy_chunk(result, body.message)


@app.post('/chat/stream')
async def chat_stream(body: ChatRequest, request: Request):
    identity, sender = await _chat_context(request, body.sender, body.metadata)
    result = await rasa_chat(sender, body.message, identity)
    result = await _enrich_policy_chunk(result, body.message)
    answer = result['answer']

    def stream():
        for i in range(0, len(answer), 80):
            yield f"data: {json.dumps({'sender': body.sender, 'text': answer[i:i + 80], 'done': False})}\n\n"
        yield f"data: {json.dumps({'sender': body.sender, 'answer': answer, 'messages': result['messages'], 'chunk': result.get('chunk'), 'done': True})}\n\n"

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/ask')
async def ask(request: Request, sender: str = Form(..., min_length=1, max_length=128),
              message: str = Form(..., min_length=1, max_length=4000),
              file: UploadFile | None = File(None),
              mode: str = Form('upsert', pattern='^(upsert|replace)$')):
    """Optionally publish a PDF, then ask Rasa in one multipart request.

    Upload commits before chat. A downstream chat failure does not undo publication.
    """
    if not sender.strip() or not message.strip():
        raise HTTPException(422, 'sender and message cannot be blank')
    identity, sender = await _chat_context(request, sender)
    if file is not None:
        await require_policy_admin(request)
    update = await upload_policy(file, mode) if file is not None else None
    try:
        result = await rasa_chat(sender, message, identity)
        result = await _enrich_policy_chunk(result, message)
    except HTTPException as exc:
        raise HTTPException(exc.status_code, {'error': exc.detail, 'policy_update': update}) from exc
    return {**result, 'policy_update': update}


@app.get('/policies/intents')
def policy_intents():
    """Reviewed intent -> PDF -> section mappings, not model predictions."""
    return intent_mapping()


class IntentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.post('/nlu/parse')
async def parse_intent(body: IntentRequest, request: Request):
    await request_identity(request)
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
