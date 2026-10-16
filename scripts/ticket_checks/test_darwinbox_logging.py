import unittest
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import httpx

from actions.darwinbox_adapters import client as darwinbox_client
from actions.darwinbox_adapters.diagnostics import record_darwinbox_event, setting_names
from teams_app import chat_service
from actions import actions as action_module
from rasa_sdk.executor import CollectingDispatcher


class DarwinboxLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def _request(self, handler):
        original = httpx.AsyncClient
        with patch.object(darwinbox_client.httpx, 'AsyncClient', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)):
            with self.assertLogs(darwinbox_client.LOGGER, level='INFO') as logs:
                try:
                    result = await darwinbox_client.DarwinboxClient().request(
                        url='https://private.example.com/holiday?secret=hidden',
                        payload={'api_key': 'private-key', 'employee_no': 'E001'},
                        operation='holiday_list',
                    )
                except darwinbox_client.DarwinboxAPIError:
                    result = None
        output = '\n'.join(logs.output)
        for secret in ('private.example.com', 'secret=hidden', 'private-key', 'E001'):
            self.assertNotIn(secret, output)
        return result, output

    async def test_success_and_http_failure_are_distinguishable(self):
        result, output = await self._request(lambda request: httpx.Response(200, json={'status': 1}))
        self.assertEqual(result, {'status': 1})
        self.assertIn('holiday_list request completed', output)
        result, output = await self._request(lambda request: httpx.Response(401))
        self.assertIsNone(result)
        self.assertIn('holiday_list HTTP status=401', output)

    async def test_connection_failure_and_invalid_json_are_distinguishable(self):
        def failed(request):
            raise httpx.ConnectError('connection failed for private-key')
        result, output = await self._request(failed)
        self.assertIsNone(result)
        self.assertIn('connection error=ConnectError', output)
        result, output = await self._request(lambda request: httpx.Response(200, text='not-json'))
        self.assertIsNone(result)
        self.assertIn('response is not valid JSON', output)

    async def test_diagnostic_file_has_safe_api_and_bot_events(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'darwinbox.log'
            with patch.dict(os.environ, {'DARWINBOX_DIAGNOSTIC_LOG_PATH': str(path)}):
                original = httpx.AsyncClient
                with patch.object(darwinbox_client.httpx, 'AsyncClient', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(401)), **kwargs)):
                    with self.assertRaises(darwinbox_client.DarwinboxAPIError):
                        await darwinbox_client.DarwinboxClient().request(
                            url='https://private.example.com/?secret=hidden',
                            payload={'api_key': 'private-key', 'employee_no': 'E001'},
                            operation='leave_balance',
                        )
                record_darwinbox_event('actions', 'leave_balance', 'configuration_error',
                                       setting_names=setting_names(Exception('DARWINBOX_BASE_URL is not configured: private-key')))
                def respond(request):
                    if request.method == 'GET':
                        return httpx.Response(200, json={'active_loop': {}})
                    return httpx.Response(200, json=[{'custom': {
                        'type': 'leave_balance_metadata', 'integration_status': 'not_configured',
                        'employee_no_requested': 'E001',
                    }}])
                with patch.object(chat_service.httpx, 'AsyncClient', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)):
                    await chat_service.rasa_chat('teams:tenant:user', 'What is my leave balance?', {})
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([event['outcome'] for event in events],
                             ['http_error', 'configuration_error', 'not_configured'])
            self.assertEqual(events[0]['http_status'], 401)
            self.assertEqual(events[-1]['component'], 'bot')
            for secret in ('private.example.com', 'secret=hidden', 'private-key', 'E001', 'teams:tenant:user'):
                self.assertNotIn(secret, path.read_text())

    async def test_action_configuration_error_is_written_without_supervisor_logging(self):
        tracker = SimpleNamespace(sender_id='private-user',
                                  latest_message={'text': 'Show my leaves', 'metadata': {}},
                                  get_latest_input_channel=lambda: 'rest')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'darwinbox.log'
            with patch.dict(os.environ, {'DARWINBOX_DIAGNOSTIC_LOG_PATH': str(path)}), \
                    patch.object(action_module, '_employee_for_tracker', return_value=('E001', {}, False)), \
                    patch.object(action_module, '_personal_log'), \
                    patch.object(action_module, '_personal_balance_answer',
                                 AsyncMock(side_effect=action_module.DarwinboxConfigurationError('DARWINBOX_LEAVE_PASSWORD is not configured'))):
                await action_module.ActionPersonalLeaveBalance().run(CollectingDispatcher(), tracker, {})
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([event['outcome'] for event in events], ['started', 'configuration_error'])
            self.assertEqual(events[-1]['setting_names'], ['DARWINBOX_LEAVE_PASSWORD'])
            self.assertNotIn('private-user', path.read_text())
            self.assertNotIn('E001', path.read_text())


if __name__ == '__main__':
    unittest.main()
