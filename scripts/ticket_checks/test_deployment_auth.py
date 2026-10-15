import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from rasa_sdk.executor import CollectingDispatcher
from actions import actions
from teams_app.bot_package import broadcast, bot_handler
from teams_app.web_package import auth, main


class DeploymentAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        import uvloop
        cls.policy = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop_policy(cls.policy)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = {'PYTHON_DOTENV_DISABLED': '1', 'ENTRA_AUTH_REQUIRED': 'true',
               'MICROSOFT_APP_ID': 'app', 'MICROSOFT_TENANT_ID': 'tenant',
               'TEAMS_SUPERADMIN_OBJECT_IDS': 'admin', 'TEAMS_BROADCAST_DB': str(Path(self.temp.name)/'state.db')}
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.identity = {'tenant_id': 'tenant', 'aad_object_id': 'user', 'email': 'employee@example.com'}
        self.result = {'answer': 'Answer', 'messages': [{'text': 'Answer'}]}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='https://example.com')
        self.addAsyncCleanup(self.client.aclose)

    async def test_mandatory_auth_blocks_forged_identity_without_calling_rasa(self):
        with patch.object(main, 'rasa_chat', AsyncMock()) as rasa:
            for route in ['/chat', '/chat/stream']:
                response = await self.client.post(route, json={'sender': 'victim', 'message': 'Show my leaves',
                    'metadata': {'channel': 'msteams', 'aad_object_id': 'victim', 'email': 'victim@example.com'}})
                self.assertEqual(response.status_code, 401)
            rasa.assert_not_awaited()

    async def test_optional_guest_mode_ignores_metadata_and_isolates_sender(self):
        with patch.dict(os.environ, {'ENTRA_AUTH_REQUIRED': 'false'}), patch.object(main, 'rasa_chat', AsyncMock(return_value=self.result.copy())) as rasa:
            response = await self.client.post('/chat', json={'sender': 'user', 'message': 'What is the leave policy?',
                'metadata': {'channel': 'msteams', 'aad_object_id': 'admin', 'email': 'victim@example.com', 'is_guest': False}})
        self.assertEqual(response.status_code, 200)
        sender, _, identity = rasa.await_args.args
        self.assertEqual(sender, 'guest:user')
        self.assertTrue(identity['is_guest'])
        self.assertNotIn('email', identity)
        self.assertNotIn('aad_object_id', identity)

    async def test_signed_in_identity_overrides_spoofed_metadata_and_sender(self):
        with patch.object(auth, 'verify_microsoft_token', AsyncMock(return_value=self.identity)), \
                patch.object(main, 'rasa_chat', AsyncMock(return_value=self.result.copy())) as rasa:
            response = await self.client.post('/chat', headers={'Authorization': 'Bearer valid'},
                json={'sender': 'victim', 'message': 'Show my leaves', 'metadata': {'email': 'victim@example.com', 'channel': 'msteams'}})
        self.assertEqual(response.status_code, 200)
        sender, _, identity = rasa.await_args.args
        self.assertEqual(sender, 'web:tenant:user')
        self.assertEqual(identity['email'], 'employee@example.com')
        self.assertEqual(identity['channel'], 'web_microsoft')
        self.assertFalse(identity['is_guest'])

    async def test_invalid_token_never_falls_back_to_guest(self):
        with patch.dict(os.environ, {'ENTRA_AUTH_REQUIRED': 'false'}), \
                patch.object(auth, 'verify_microsoft_token', AsyncMock(side_effect=HTTPException(401, 'Invalid token'))), \
                patch.object(main, 'rasa_chat', AsyncMock()) as rasa:
            response = await self.client.post('/chat', headers={'Authorization': 'Bearer invalid'}, json={'sender': 'test', 'message': 'Hello'})
        self.assertEqual(response.status_code, 401)
        rasa.assert_not_awaited()

    async def test_native_teams_works_with_mandatory_signin_without_web_token(self):
        context = SimpleNamespace(activity=SimpleNamespace(text='GEMS application is not working', value=None), send_activity=AsyncMock())
        with patch.object(bot_handler, '_teams_identity', AsyncMock(return_value=self.identity.copy())), \
                patch.object(bot_handler, 'rasa_chat', AsyncMock(return_value=self.result)) as rasa, \
                patch.object(bot_handler.httpx, 'AsyncClient') as http:
            await bot_handler.TeamsBot().on_message_activity(context)
        http.assert_not_called()
        self.assertEqual(rasa.await_args.args[0], 'teams:tenant:user')
        self.assertEqual(rasa.await_args.args[2]['email'], 'employee@example.com')
        self.assertEqual(rasa.await_args.args[2]['channel'], 'msteams')
        context.send_activity.assert_awaited_once_with('Answer')

    async def test_native_teams_without_identity_cannot_call_rasa(self):
        context = SimpleNamespace(activity=SimpleNamespace(text='Show my leaves', value=None, conversation=SimpleNamespace(id='chat')), send_activity=AsyncMock())
        with patch.object(bot_handler, '_teams_identity', AsyncMock(return_value={})), patch.object(bot_handler, 'rasa_chat', AsyncMock()) as rasa:
            await bot_handler.TeamsBot().on_message_activity(context)
        rasa.assert_not_awaited()

    async def test_native_teams_shared_service_calls_only_private_rasa(self):
        captured = []
        original_client = httpx.AsyncClient

        def respond(request):
            captured.append(request)
            return httpx.Response(200, json={'active_loop': {}} if request.method == 'GET' else [{'text': 'Your balance'}])

        context = SimpleNamespace(activity=SimpleNamespace(text='Show my leaves', value=None), send_activity=AsyncMock())
        with patch.object(bot_handler, '_teams_identity', AsyncMock(return_value=self.identity.copy())), \
                patch.object(httpx, 'AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
            await bot_handler.TeamsBot().on_message_activity(context)
        import json
        body = json.loads(captured[-1].content)
        self.assertEqual(body['sender'], 'teams:tenant:user')
        self.assertEqual(body['message'], 'What is my leave balance?')
        self.assertEqual(body['metadata']['email'], 'employee@example.com')
        self.assertTrue(all(request.url.host == '127.0.0.1' and request.url.port == 5005 for request in captured))
        context.send_activity.assert_awaited_once_with('Your balance')

    async def publish(self, route='/policies/upload', token=None):
        return await self.client.post(route, headers={'Authorization': 'Bearer valid'} if token else {},
            data={'sender': 'test', 'message': 'Leave policy', 'mode': 'upsert'}, files={'file': ('policy.pdf', b'%PDF-test', 'application/pdf')})

    async def test_upload_and_multipart_ask_require_admin_even_in_guest_mode(self):
        with patch.dict(os.environ, {'ENTRA_AUTH_REQUIRED': 'false'}), patch.object(main, 'upload_policy', AsyncMock()) as publish:
            for route in ['/policies/upload', '/ask']:
                response = await self.publish(route)
                self.assertEqual(response.status_code, 401)
            publish.assert_not_awaited()
        with patch.object(auth, 'verify_microsoft_token', AsyncMock(return_value=self.identity)), patch.object(main, 'upload_policy', AsyncMock()) as publish:
            for route in ['/policies/upload', '/ask']:
                response = await self.publish(route, token=True)
                self.assertEqual(response.status_code, 403)
            publish.assert_not_awaited()

    async def test_superadmin_can_publish_both_routes_and_wrong_tenant_cannot(self):
        admin = {**self.identity, 'aad_object_id': 'admin'}
        with patch.object(auth, 'verify_microsoft_token', AsyncMock(return_value=admin)), \
                patch.object(main, 'upload_policy', AsyncMock(return_value={'updated': True})) as publish, \
                patch.object(main, 'rasa_chat', AsyncMock(return_value=self.result.copy())):
            for route in ['/policies/upload', '/ask']:
                response = await self.publish(route, token=True)
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(publish.await_count, 2)
        with patch.object(auth, 'verify_microsoft_token', AsyncMock(return_value={**admin, 'tenant_id': 'other'})), \
                patch.object(main, 'upload_policy', AsyncMock()) as publish:
            self.assertEqual((await self.publish(token=True)).status_code, 403)
            publish.assert_not_awaited()

    async def test_revoked_delegated_admin_loses_policy_publication(self):
        with broadcast.database() as db:
            db.execute('INSERT INTO superadmins VALUES (?,?,?,?,?,?)', ('tenant', 'user', 'delegated', '', 'admin', 1))
        with patch.object(auth, 'verify_microsoft_token', AsyncMock(return_value=self.identity)), \
                patch.object(main, 'upload_policy', AsyncMock(return_value={'updated': True})) as publish:
            self.assertEqual((await self.publish(token=True)).status_code, 200)
            with broadcast.database() as db:
                db.execute('DELETE FROM superadmins WHERE oid=?', ('user',))
            self.assertEqual((await self.publish(token=True)).status_code, 403)
            self.assertEqual(publish.await_count, 1)

    async def test_multipart_ask_without_upload_still_authenticates_chat(self):
        with patch.object(main, 'rasa_chat', AsyncMock()) as rasa:
            response = await self.client.post('/ask', data={'sender': 'victim', 'message': 'Show my leaves'})
            self.assertEqual(response.status_code, 401)
            rasa.assert_not_awaited()

    async def test_guest_leave_and_missing_employee_mapping_do_not_fetch_balance(self):
        tracker = SimpleNamespace(latest_message={'text': 'Show my leaves', 'metadata': {'is_guest': True}}, sender_id='guest:test')
        dispatcher = CollectingDispatcher()
        with patch.object(actions, '_personal_balance_answer', AsyncMock()) as fetch:
            await actions.ActionPersonalLeaveBalance().run(dispatcher, tracker, {})
            fetch.assert_not_awaited()
        self.assertIn('Sign in', dispatcher.messages[0]['text'])
        tracker.latest_message['metadata'] = {'aad_object_id': 'user', 'tenant_id': 'tenant', 'channel': 'web_microsoft'}
        dispatcher = CollectingDispatcher()
        with patch.object(actions, 'resolve_employee_id', return_value=None), patch.object(actions, '_personal_log'), \
                patch.object(actions, '_personal_balance_answer', AsyncMock()) as fetch:
            await actions.ActionPersonalLeaveBalance().run(dispatcher, tracker, {})
            fetch.assert_not_awaited()
        self.assertIn('map your signed-in account', dispatcher.messages[0]['text'])

    def test_signin_is_required_by_default(self):
        os.environ.pop('ENTRA_AUTH_REQUIRED')
        self.assertTrue(auth.auth_required())
