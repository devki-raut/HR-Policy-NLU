import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from teams_app.web_package import ticket_mail as web


class WebMailTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_cannot_create_or_send(self):
        with patch.object(web, 'request_identity', AsyncMock(return_value=None)), patch.object(web, 'send_draft', AsyncMock()) as send:
            with self.assertRaises(HTTPException) as error:
                await web.send('draft', web.SendRequest(graph_token='token'), SimpleNamespace())
            self.assertEqual(error.exception.status_code, 401)
            send.assert_not_awaited()

    async def test_send_binds_verified_identity(self):
        identity = {'tenant_id': 'tenant', 'aad_object_id': 'user'}
        with patch.object(web, 'request_identity', AsyncMock(return_value=identity)), patch.object(web, 'send_draft', AsyncMock()) as send:
            result = await web.send('draft', web.SendRequest(graph_token='token'), SimpleNamespace())
            send.assert_awaited_once_with('draft', 'tenant:user:web', 'token', 'user')
            self.assertEqual(result['status'], 'accepted')

    async def test_mail_failure_is_reported(self):
        identity = {'tenant_id': 'tenant', 'aad_object_id': 'user'}
        with patch.object(web, 'request_identity', AsyncMock(return_value=identity)), patch.object(web, 'send_draft', AsyncMock(side_effect=web.MailError('Check Sent Items'))):
            with self.assertRaises(HTTPException) as error:
                await web.send('draft', web.SendRequest(graph_token='token'), SimpleNamespace())
            self.assertEqual(error.exception.status_code, 409)

    async def test_draft_is_owned_by_signed_in_user(self):
        identity = {'tenant_id': 'tenant', 'aad_object_id': 'user'}
        with patch.object(web, 'request_identity', AsyncMock(return_value=identity)), patch.object(web, 'create_draft', return_value='draft') as create:
            result = await web.new_draft(web.DraftRequest(subject='VPN', body='Please help'), SimpleNamespace())
            create.assert_called_once_with('tenant:user:web', 'VPN', 'Please help')
            self.assertEqual(result['request_id'], 'draft')
