"""Exchange a verified Teams/web API token for delegated Graph access (OBO)."""
import os
import uuid
import httpx
from teams_app.web_package.auth import client_id
from teams_app.bot_package.ticket_mail import MailError


async def graph_token(assertion, identity):
    try:
        tenant=str(uuid.UUID(identity['tenant_id']))
    except (ValueError,KeyError,TypeError) as exc:
        raise MailError('The signed-in Microsoft tenant could not be verified.') from exc
    app_id=client_id()
    secret=os.getenv('ENTRA_CLIENT_SECRET','').strip()
    if not secret and app_id==os.getenv('MICROSOFT_APP_ID','').strip():
        secret=os.getenv('MICROSOFT_APP_PASSWORD','').strip()
    if not app_id or not secret:
        raise MailError('Teams mailbox SSO requires the Entra app client credentials and delegated User.Read/Mail.Send consent.')
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response=await http.post(f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', data={
                'client_id':app_id,'client_secret':secret,'grant_type':'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'requested_token_use':'on_behalf_of','assertion':assertion,'scope':'https://graph.microsoft.com/.default',
            })
        payload=response.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise MailError('Teams mailbox SSO is temporarily unavailable. Your draft is retained.') from exc
    if response.status_code!=200 or not isinstance(payload.get('access_token'),str):
        # Never surface raw identity-provider errors or assertions to logs/UI.
        raise MailError('Teams mailbox SSO could not obtain delegated mail access. Ask your administrator to grant User.Read and Mail.Send consent and check the exposed API scope and client credentials. Your draft is retained.')
    return payload['access_token']
