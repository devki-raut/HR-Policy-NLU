"""Authenticated web ticket drafts and delegated mailbox sending."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from teams_app.web_package.auth import request_identity
from teams_app.bot_package.ticket_mail import MailError, owner, create_draft, send_draft

router = APIRouter(prefix='/tickets')


class DraftRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=6000)


class SendRequest(BaseModel):
    graph_token: str = Field(min_length=1, max_length=16384)


async def signed_in(request):
    identity = await request_identity(request)
    if not identity:
        raise HTTPException(401, 'Sign in with Microsoft before raising a request.')
    return identity, owner(identity, 'web')


@router.post('/drafts')
async def new_draft(body: DraftRequest, request: Request):
    _, identity_owner = await signed_in(request)
    if not body.subject.strip() or not body.body.strip():
        raise HTTPException(422, 'Subject and body cannot be blank.')
    return {'request_id': create_draft(identity_owner, body.subject, body.body)}


@router.post('/drafts/{request_id}/send')
async def send(request_id: str, body: SendRequest, request: Request):
    identity, identity_owner = await signed_in(request)
    try:
        await send_draft(request_id, identity_owner, body.graph_token, identity['aad_object_id'])
    except MailError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'status': 'accepted', 'message': 'Outlook accepted your email to helpdesk@emergys.com for sending. Check Sent Items.'}
