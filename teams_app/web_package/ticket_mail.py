"""Authenticated, editable web mail drafts with delegated Microsoft sending."""
import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from teams_app.web_package.auth import request_identity
from teams_app.web_package.mail_sso import graph_token as sso_graph_token
from teams_app.bot_package.ticket_mail import (MailError, owner, create_draft, update_draft,
                                              get_draft, send_draft, cancel_draft)

router=APIRouter(prefix='/tickets')


def recipient():
    return os.getenv('TICKET_WEB_TEST_RECIPIENT','').strip() or 'helpdesk@emergys.com'


class DraftRequest(BaseModel):
    subject: str=Field(min_length=1,max_length=180)
    body: str=Field(min_length=1,max_length=6000)
    to: str | None=Field(default=None,max_length=2000)
    cc: str=Field(default='',max_length=2000)


class SendRequest(BaseModel):
    graph_token: str | None=Field(default=None,min_length=1,max_length=16384)
    use_teams_sso: bool=False


async def signed_in(request):
    identity=await request_identity(request)
    if not identity:raise HTTPException(401,'Sign in with Microsoft before raising a request.')
    return identity,owner(identity,'web')


@router.post('/drafts')
async def new_draft(body: DraftRequest,request: Request):
    _,identity_owner=await signed_in(request)
    if not body.subject.strip() or not body.body.strip():raise HTTPException(422,'Subject and body cannot be blank.')
    try:
        key=create_draft(identity_owner,body.subject,body.body,body.to if body.to is not None else recipient(),body.cc)
        update_draft(key,identity_owner,body.subject,body.body)
    except MailError as exc:raise HTTPException(422,str(exc)) from exc
    return {'request_id':key}


@router.put('/drafts/{request_id}')
async def edit(request_id: str,body: DraftRequest,request: Request):
    _,identity_owner=await signed_in(request)
    try:update_draft(request_id,identity_owner,body.subject,body.body,body.to,body.cc)
    except MailError as exc:raise HTTPException(409,str(exc)) from exc
    return {'request_id':request_id}


@router.delete('/drafts/{request_id}')
async def cancel(request_id: str,request: Request):
    _,identity_owner=await signed_in(request)
    try:cancel_draft(request_id,identity_owner)
    except MailError as exc:raise HTTPException(409,str(exc)) from exc
    return {'status':'cancelled'}


@router.post('/drafts/{request_id}/send')
async def send(request_id: str,body: SendRequest,request: Request):
    identity,identity_owner=await signed_in(request)
    try:
        record=get_draft(request_id,identity_owner)
        if record.get('status','draft')!='draft':raise MailError('This request is already submitted, cancelled or awaiting outcome verification. Check Sent Items before retrying.')
        if body.use_teams_sso:
            if body.graph_token:raise MailError('Choose one Microsoft authorization method.')
            parts=request.headers.get('Authorization','').split(maxsplit=1)
            assertion=parts[1] if len(parts)==2 and parts[0].casefold()=='bearer' else ''
            if not assertion:raise MailError('A verified Microsoft API token is required for Teams SSO.')
            token=await sso_graph_token(assertion,identity)
        else:
            token=body.graph_token
            if not token:raise MailError('Microsoft mailbox authorization is required.')
        await send_draft(request_id,identity_owner,token,identity['aad_object_id'])
    except MailError as exc:raise HTTPException(409,str(exc)) from exc
    return {'status':'accepted','message':f"Outlook accepted your email to {record['to_list']} for sending. Check Sent Items."}
