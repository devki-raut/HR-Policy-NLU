import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from botframework.connector.token_api.models import SignInUrlResponse, TokenExchangeResource

from teams_app.bot_package import mail_sso as sso, ticket_mail as mail
from teams_app.web_package import mail_sso as web_sso

class TeamsSSOTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=patch.object(mail,'ROOT',Path(self.temp.name));self.root.start()
        self.env=patch.dict(os.environ,{'TEAMS_MAIL_OAUTH_CONNECTION':'mail-sso'});self.env.start()
        self.identity={'aad_object_id':'user','tenant_id':'tenant'}
        self.owner='tenant:user:conversation';self.key=mail.create_draft(self.owner,'Internet issue','The internet is unavailable.','support@example.com','manager@example.com')
        self.context=SimpleNamespace(activity=SimpleNamespace(channel_id='msteams',conversation=SimpleNamespace(id='conversation',conversation_type='personal'),from_property=SimpleNamespace(id='teams-user'),value=None),send_activity=AsyncMock(),adapter=SimpleNamespace(
            get_user_token=AsyncMock(return_value=None),
            get_sign_in_resource_from_user=AsyncMock(return_value=SignInUrlResponse(sign_in_link='https://token.botframework.com/consent',token_exchange_resource=TokenExchangeResource(id='exchange-id',uri='api://app'))),
            exchange_token=AsyncMock(return_value=SimpleNamespace(token='delegated-graph-token'))))
    def tearDown(self):self.env.stop();self.root.stop();self.temp.cleanup()
    def exchange(self,**changes):
        self.context.activity.value={'id':'exchange-id','connectionName':'mail-sso','token':'teams-sso-token',**changes}

    async def test_oauth_card_includes_token_exchange_resource(self):
        await sso.begin(self.context,self.identity,self.key)
        card=self.context.send_activity.await_args.args[0].attachments[0].content.serialize()
        self.assertEqual(card['tokenExchangeResource']['id'],'exchange-id')
        self.assertEqual(mail.auth_request(self.owner)['draft_id'],self.key)
        self.assertNotIn('teams-sso-token',json.dumps(mail.auth_request(self.owner)))

    async def test_sso_exchange_automatically_continues_approved_send_once(self):
        await sso.begin(self.context,self.identity,self.key);self.exchange()
        with patch.object(sso,'send_draft',AsyncMock()) as send:
            response=await sso.token_exchange(self.context,self.identity)
            await sso.token_exchange(self.context,self.identity)
            self.assertEqual(response.status,200)
            send.assert_awaited_once_with(self.key,self.owner,'delegated-graph-token','user')
        self.assertEqual(self.context.adapter.exchange_token.await_count,1)
        self.assertNotIn('click Raise',str(self.context.send_activity.await_args))

    async def test_existing_sso_token_does_not_show_sign_in_card(self):
        self.context.adapter.get_user_token.return_value=SimpleNamespace(token='existing-token')
        with patch.object(sso,'send_draft',AsyncMock()) as send:
            await sso.begin(self.context,self.identity,self.key)
            send.assert_awaited_once_with(self.key,self.owner,'existing-token','user')
        self.context.adapter.get_sign_in_resource_from_user.assert_not_awaited()

    async def test_connection_and_exchange_id_are_bound_to_approval(self):
        await sso.begin(self.context,self.identity,self.key)
        with patch.object(sso,'send_draft',AsyncMock()) as send:
            for overrides in [{'connectionName':'other-connection'},{'id':'other-exchange'}]:
                self.exchange(**overrides)
                self.assertEqual((await sso.token_exchange(self.context,self.identity)).status,400)
            send.assert_not_awaited()

    async def test_failed_token_exchange_returns_412_without_sending(self):
        await sso.begin(self.context,self.identity,self.key);self.exchange()
        self.context.adapter.exchange_token.return_value=None
        with patch.object(sso,'send_draft',AsyncMock()) as send:
            self.assertEqual((await sso.token_exchange(self.context,self.identity)).status,412)
            send.assert_not_awaited()
        self.assertIsNotNone(mail.auth_request(self.owner))

    async def test_cancel_edit_and_expiry_invalidate_pending_send(self):
        for operation in ['cancel','edit','expire']:
            self.key=mail.create_draft(self.owner,'New issue','Details')
            await sso.begin(self.context,self.identity,self.key);self.exchange()
            if operation=='cancel':mail.cancel_draft(self.key,self.owner)
            if operation=='edit':mail.update_draft(self.key,self.owner,'Other issue','Edited details')
            if operation=='expire':
                with mail._db() as db:db.execute('UPDATE pending_auth SET expires=0')
            with patch.object(sso,'send_draft',AsyncMock()) as send:
                await sso.token_exchange(self.context,self.identity)
                send.assert_not_awaited()

    async def test_auth_event_resumes_without_another_raise_click(self):
        await sso.begin(self.context,self.identity,self.key)
        self.context.activity.value={'connectionName':'mail-sso'}
        self.context.adapter.get_user_token.return_value=SimpleNamespace(token='event-token')
        with patch.object(sso,'send_draft',AsyncMock()) as send:
            await sso.token_event(self.context,self.identity)
            await sso.token_event(self.context,self.identity)
            send.assert_awaited_once_with(self.key,self.owner,'event-token','user')

    async def test_other_owner_cannot_resume_and_missing_resource_fails_clearly(self):
        await sso.begin(self.context,self.identity,self.key);self.exchange()
        with patch.object(sso,'send_draft',AsyncMock()) as send:
            await sso.token_exchange(self.context,{'tenant_id':'tenant','aad_object_id':'other-user'})
            send.assert_not_awaited()
        self.context.adapter.get_sign_in_resource_from_user.return_value=SignInUrlResponse(sign_in_link='https://example.com')
        with self.assertRaisesRegex(mail.MailError,'missing its SSO token-exchange resource'):
            await sso.begin(self.context,self.identity,self.key)

    async def test_old_sso_response_cannot_claim_a_newer_approval(self):
        await sso.begin(self.context,self.identity,self.key)
        old=mail.auth_request(self.owner)
        new_key=mail.create_draft(self.owner,'A different request','New body')
        mail.pending_auth(self.owner,new_key,'mail-sso','new-exchange')
        with patch.object(sso,'send_draft',AsyncMock()) as send:
            self.assertFalse(await sso.resume(self.context,self.identity,'old-token',old))
            send.assert_not_awaited()
        self.assertEqual(mail.auth_request(self.owner)['draft_id'],new_key)

    async def test_open_outlook_includes_current_to_cc_subject_body_without_sending(self):
        from teams_app.bot_package.bot_handler import TeamsBot
        values={'command':'ticket_open_outlook','request_id':self.key,'ticket_to':'new@example.com','ticket_cc':'cc@example.com','ticket_subject':'Edited + subject','ticket_body':'Edited body\nWith detail'}
        with patch('teams_app.bot_package.bot_handler._teams_identity',AsyncMock(return_value=self.identity)),patch.object(sso,'begin',AsyncMock()) as begin:
            await TeamsBot()._raise_ticket(self.context,values)
            begin.assert_not_awaited()
        from urllib.parse import urlparse,parse_qs
        card=self.context.send_activity.await_args.args[0].attachments[0].content
        query=parse_qs(urlparse(card['actions'][0]['url']).query)
        self.assertEqual(query['to'],['new@example.com']);self.assertEqual(query['cc'],['cc@example.com'])
        self.assertEqual(query['subject'],['Edited + subject']);self.assertEqual(query['body'],['Edited body\nWith detail'])
        self.assertEqual(mail.get_draft(self.key,self.owner)['status'],'draft')

class WebSSOTests(unittest.IsolatedAsyncioTestCase):
    async def test_obo_exchanges_verified_api_token_for_delegated_graph_token(self):
        tenant='11111111-1111-1111-1111-111111111111';requests=[];client=httpx.AsyncClient
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'access_token':'graph-token'})
        with patch.dict(os.environ,{'ENTRA_CLIENT_ID':'app','ENTRA_CLIENT_SECRET':'secret'}),patch.object(web_sso.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(respond),**kw)):
            self.assertEqual(await web_sso.graph_token('verified-api-token',{'tenant_id':tenant}),'graph-token')
        from urllib.parse import parse_qs
        payload=parse_qs(requests[0].content.decode())
        self.assertEqual(payload['requested_token_use'],['on_behalf_of'])
        self.assertEqual(payload['assertion'],['verified-api-token'])
        self.assertEqual(payload['scope'],['https://graph.microsoft.com/.default'])

    async def test_invalid_tenant_does_not_make_external_request(self):
        with patch.object(web_sso.httpx,'AsyncClient') as http,self.assertRaises(mail.MailError):
            await web_sso.graph_token('token',{'tenant_id':'invalid/tenant'})
        http.assert_not_called()
