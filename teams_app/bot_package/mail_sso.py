"""Teams Bot Framework SSO for an explicitly approved, owner-bound mail draft."""
import os
from botbuilder.core import CardFactory, MessageFactory
from botbuilder.schema import OAuthCard, CardAction, ActionTypes, InvokeResponse
from botframework.connector.token_api.models import TokenExchangeRequest

from .ticket_mail import (MailError, owner, pending_auth, auth_request, claim_auth,
                          send_draft, get_draft)


def connection_name():
    return os.getenv('TEAMS_MAIL_OAUTH_CONNECTION', '').strip()


async def begin(context, identity, request_id):
    connection=connection_name()
    if not connection:
        raise MailError('Teams mailbox SSO is not configured. An administrator must set TEAMS_MAIL_OAUTH_CONNECTION for the Azure Bot OAuth connection with delegated User.Read and Mail.Send consent.')
    if context.activity.channel_id != 'msteams' or context.activity.conversation.conversation_type != 'personal':
        raise MailError('Send this request in your personal Teams chat with EmployeeAssist.')
    identity_owner=owner(identity,context.activity.conversation.id)
    token=await context.adapter.get_user_token(context,connection)
    if token and token.token:
        await send_draft(request_id,identity_owner,token.token,identity['aad_object_id'])
        row=get_draft(request_id,identity_owner)
        await context.send_activity(f"Outlook accepted your email to {row['to_list']} for sending. Check Sent Items.")
        return
    resource=await context.adapter.get_sign_in_resource_from_user(context,connection,context.activity.from_property.id)
    exchange=getattr(resource,'token_exchange_resource',None)
    if not exchange or not getattr(exchange,'id',None) or not getattr(exchange,'uri',None):
        raise MailError('The Azure Bot OAuth connection is missing its SSO token-exchange resource. Configure its Token Exchange URL to match the Teams manifest webApplicationInfo.resource. Your draft is retained.')
    pending_auth(identity_owner,request_id,connection,exchange.id)
    card=OAuthCard(connection_name=connection,
        text='Completing Teams single sign-on. If Microsoft asks for consent, approve mailbox access once. Your submitted request will continue automatically.',
        token_exchange_resource=exchange,token_post_resource=getattr(resource,'token_post_resource',None),
        buttons=[CardAction(type=ActionTypes.signin,title='Authorize mailbox',value=resource.sign_in_link)])
    await context.send_activity(MessageFactory.attachment(CardFactory.oauth_card(card)))


async def resume(context, identity, token, expected=None):
    identity_owner=owner(identity,context.activity.conversation.id)
    request=claim_auth(identity_owner, expected['draft_id'] if expected else None, expected['exchange_id'] if expected else None)
    if not request:return False
    try:
        await send_draft(request['draft_id'],identity_owner,token,identity['aad_object_id'])
        row=get_draft(request['draft_id'],identity_owner)
        await context.send_activity(f"Outlook accepted your email to {row['to_list']} for sending. Check Sent Items.")
    except MailError as exc:
        await context.send_activity(str(exc))
    return True


async def token_exchange(context, identity):
    if context.activity.channel_id != 'msteams':return InvokeResponse(status=403)
    values=context.activity.value
    if not isinstance(values,dict):return InvokeResponse(status=400)
    connection=connection_name();exchange_id=values.get('id');token=values.get('token')
    body={'id':exchange_id if isinstance(exchange_id,str) else '', 'connectionName':connection}
    if (not connection or values.get('connectionName')!=connection or not isinstance(exchange_id,str)
            or not 1<=len(exchange_id)<=256 or not isinstance(token,str) or not 1<=len(token)<=32768):
        return InvokeResponse(status=400,body={**body,'failureDetail':'Invalid token-exchange request.'})
    identity_owner=owner(identity,context.activity.conversation.id)
    pending=auth_request(identity_owner)
    if not pending:
        # Duplicate Teams invokes after completion must acknowledge without resending.
        return InvokeResponse(status=200,body=body)
    if pending['exchange_id']!=exchange_id or pending['connection']!=connection:
        return InvokeResponse(status=400,body={**body,'failureDetail':'The SSO request does not match the approved draft.'})
    try:
        result=await context.adapter.exchange_token(context,connection,context.activity.from_property.id,TokenExchangeRequest(token=token))
    except Exception:
        result=None
    if not result or not result.token:
        return InvokeResponse(status=412,body={**body,'failureDetail':'Teams SSO token exchange requires consent or configuration review.'})
    await resume(context,identity,result.token,pending)
    return InvokeResponse(status=200,body=body)


async def token_event(context, identity, magic_code=None):
    connection=connection_name()
    values=context.activity.value or {}
    if isinstance(values,dict) and values.get('connectionName') not in {None,connection}:return
    identity_owner=owner(identity,context.activity.conversation.id)
    pending=auth_request(identity_owner)
    if not connection or not pending or pending['connection']!=connection:return
    result=await context.adapter.get_user_token(context,connection,magic_code=magic_code)
    if result and result.token:
        await resume(context,identity,result.token,pending)
    else:
        await context.send_activity('Microsoft has not completed mailbox consent. Your draft is retained; complete consent and submit it again if needed.')
