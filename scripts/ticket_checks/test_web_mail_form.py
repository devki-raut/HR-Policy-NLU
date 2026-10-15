import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,AsyncMock
import httpx
from fastapi import HTTPException
from teams_app.web_package import ticket_mail as web
from teams_app.bot_package import ticket_mail as mail

class WebMailFormTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=patch.object(mail,'ROOT',Path(self.temp.name));self.root.start()
        self.identity={'tenant_id':'tenant','aad_object_id':'user'}
        self.request=SimpleNamespace(headers={'Authorization':'Bearer api-token'})
        self.ident=patch.object(web,'request_identity',AsyncMock(return_value=self.identity));self.ident.start()
    def tearDown(self):self.ident.stop();self.root.stop();self.temp.cleanup()
    async def prepare(self):
        result=await web.new_draft(web.DraftRequest(subject='Internet problem',body='The branch internet is unavailable.',to='helpdesk@example.com'),self.request)
        return result['request_id']
    async def test_teams_sso_send_uses_edited_recipients_and_current_fields(self):
        key=await self.prepare()
        await web.edit(key,web.DraftRequest(subject='Updated subject',body='Updated body',to='it@example.com',cc='lead@example.com'),self.request)
        client=httpx.AsyncClient;posts=[]
        def reply(request):
            if request.method=='GET':return httpx.Response(200,json={'id':'user'})
            posts.append(json.loads(request.content));return httpx.Response(202)
        with patch.object(web,'sso_graph_token',AsyncMock(return_value='graph-token')) as obo,patch.object(mail.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(reply),**kw)):
            result=await web.send(key,web.SendRequest(use_teams_sso=True),self.request)
            obo.assert_awaited_once_with('api-token',self.identity)
        self.assertEqual(result['status'],'accepted')
        m=posts[0]['message'];self.assertEqual(m['subject'],'Updated subject');self.assertEqual(m['body']['content'],'Updated body')
        self.assertEqual(m['toRecipients'][0]['emailAddress']['address'],'it@example.com')
        self.assertEqual(m['ccRecipients'][0]['emailAddress']['address'],'lead@example.com')
        with patch.object(web,'sso_graph_token',AsyncMock()) as obo,self.assertRaises(HTTPException):
            await web.send(key,web.SendRequest(use_teams_sso=True),self.request)
        obo.assert_not_awaited()
    async def test_cancel_prevents_authorization_and_sending(self):
        key=await self.prepare();await web.cancel(key,self.request)
        with patch.object(web,'sso_graph_token',AsyncMock()) as obo,self.assertRaises(HTTPException):
            await web.send(key,web.SendRequest(use_teams_sso=True),self.request)
        obo.assert_not_awaited()
    async def test_invalid_cc_rejected_and_other_owner_cannot_edit(self):
        key=await self.prepare()
        with self.assertRaises(HTTPException):
            await web.edit(key,web.DraftRequest(subject='Subject',body='Body',to='it@example.com',cc='invalid'),self.request)
        with patch.object(web,'request_identity',AsyncMock(return_value={'tenant_id':'tenant','aad_object_id':'other'})),self.assertRaises(HTTPException):
            await web.edit(key,web.DraftRequest(subject='Subject',body='Body'),self.request)
        self.assertEqual(mail.get_draft(key,'tenant:user:web')['subject'],'Internet problem')
