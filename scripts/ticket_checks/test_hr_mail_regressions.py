from datetime import date
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from rasa_sdk.executor import CollectingDispatcher
from actions import actions, ticket_templates
from actions.darwinbox_adapters.configuration import configuration_status
from actions.darwinbox_adapters.holiday import Holiday
from actions.darwinbox_adapters.leave import LeaveBalanceAdapter
from actions.tickets import ActionStartTicket
from teams_app import chat_service
from teams_app.bot_package import bot_handler


class HRMailRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_ticket_command_produces_editable_prefilled_mail(self):
        tracker = SimpleNamespace(latest_message={'text': 'Create IT ticket', 'metadata': {'display_name': 'Employee One'}}, get_slot=lambda _: None)
        dispatcher = CollectingDispatcher()
        await ActionStartTicket().run(dispatcher, tracker, {})
        value = next(v['custom'] for v in dispatcher.messages if v.get('custom', {}).get('type') == 'ticket_draft')
        self.assertEqual(value['subject'], 'IT support request')
        self.assertIn('Dear Team,', value['body'])
        self.assertIn('I need assistance', value['body'])
        self.assertTrue(value['body'].endswith('Employee One'))
        card = bot_handler._ticket_card(value, 'draft-id')
        fields = {v['id']: v for v in card['body'] if v['type'] == 'Input.Text'}
        self.assertEqual(fields['ticket_body']['value'], value['body'])
        self.assertNotIn('placeholder', fields['ticket_body'])
        self.assertNotIn('placeholder', fields['ticket_subject'])

    def test_old_blank_draft_is_prefilled_and_known_issue_is_preserved(self):
        value = ticket_templates.prefill_draft({'subject': '', 'body': ''}, {'display_name': 'Employee Two'})
        self.assertIn('Employee Two', value['body'])
        subject, body = ticket_templates.draft('Internet is unavailable', None, {'display_name': 'Employee Two'})
        self.assertIn('Internet is unavailable', subject)
        self.assertIn('Internet is unavailable', body)
        self.assertNotIn('Started:', body)
        self.assertNotIn('None', body)
        card = bot_handler._ticket_card({'body': '', 'subject': ''}, 'draft-id')
        self.assertTrue(next(v for v in card['body'] if v.get('id') == 'ticket_body')['value'])

    async def test_menu_labels_route_to_canonical_rasa_questions(self):
        original = httpx.AsyncClient
        captured = []

        def respond(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'active_loop': {}})
            captured.append(json.loads(request.content)['message'])
            return httpx.Response(200, json=[{'text': 'Result'}])

        with patch.object(chat_service.httpx, 'AsyncClient', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)):
            for question in ['Holiday calendar', 'My leave balance', 'Next holidays']:
                await chat_service.rasa_chat('test-sender', question, {})
        self.assertEqual(captured, ['Show the full holiday list', 'What is my leave balance?', 'What are the next holidays?'])

    async def test_holiday_metadata_without_text_renders_calendar_card(self):
        context = SimpleNamespace(activity=SimpleNamespace(text='Holiday calendar', value=None), send_activity=AsyncMock())
        metadata = {'type': 'holiday_list_metadata', 'status': 'answered', 'mode': 'full_year', 'year': 2026,
                    'holidays': [{'name': 'Diwali', 'date': '2026-11-08', 'day': 'Sunday'}]}
        with patch.object(bot_handler, '_teams_identity', AsyncMock(return_value={'tenant_id': 'tenant', 'aad_object_id': 'user'})), \
                patch.object(bot_handler, 'rasa_chat', AsyncMock(return_value={'messages': [{'custom': metadata}]})) as rasa:
            await bot_handler.TeamsBot().on_message_activity(context)
        self.assertEqual(rasa.await_args.args[1], 'Show the full holiday list')
        card = context.send_activity.await_args.args[0].attachments[0].content
        self.assertIn('Diwali', json.dumps(card))
        self.assertNotIn("couldn't find a policy answer", json.dumps(card))

    async def test_holiday_action_emits_text_and_structured_data(self):
        tracker = SimpleNamespace(sender_id='test', latest_message={'text': 'Holiday calendar', 'intent': {'name': 'ask_holiday_list'}}, get_latest_input_channel=lambda: 'rest')
        dispatcher = CollectingDispatcher()
        holiday = Holiday(name='Diwali', date=date(2026, 11, 8))
        with patch.object(actions, '_employee_for_tracker', return_value=('employee', {}, False)), \
                patch.object(actions, 'fetch_holidays', AsyncMock(return_value=([holiday], {}))), \
                patch.object(actions, 'append_conversation', return_value='event'):
            await actions.ActionHolidayList().run(dispatcher, tracker, {})
        self.assertTrue(any('Diwali' in v.get('text', '') for v in dispatcher.messages))
        self.assertTrue(any(v.get('custom', {}).get('type') == 'holiday_list_metadata' for v in dispatcher.messages))

    def test_independent_action_worker_loads_local_settings_without_override(self):
        import actions as package
        with patch('dotenv.load_dotenv') as load:
            importlib.reload(package)
        load.assert_called_once_with(Path(package.__file__).resolve().parents[1]/'.env', override=False)

    async def test_holiday_mapping_configuration_error_returns_integration_reply(self):
        tracker = SimpleNamespace(sender_id='test', latest_message={'text': 'Holiday calendar', 'intent': {'name': 'ask_holiday_list'}}, get_latest_input_channel=lambda: 'rest')
        dispatcher = CollectingDispatcher()
        with patch.object(actions, '_employee_for_tracker', side_effect=actions.DarwinboxConfigurationError('DARWINBOX_EMPLOYEE_MAP_FILE is unreadable or invalid')), \
                patch.object(actions, 'append_conversation', return_value='event'), \
                patch.object(actions, 'fetch_holidays', AsyncMock()) as fetch, self.assertLogs('actions.actions'):
            await actions.ActionHolidayList().run(dispatcher, tracker, {})
        fetch.assert_not_awaited()
        self.assertTrue(any('Darwinbox holiday' in v.get('text', '') for v in dispatcher.messages))
        custom = next(v['custom'] for v in dispatcher.messages if v.get('custom', {}).get('type') == 'holiday_list_metadata')
        self.assertEqual(custom['integration_status'], 'not_configured')

    def test_config_diagnostics_never_expose_values(self):
        with patch.dict(os.environ, {}, clear=True):
            empty = configuration_status()
        self.assertFalse(empty['leave']['configured'])
        self.assertFalse(empty['holidays']['configured'])
        with patch.dict(os.environ, {'DARWINBOX_BASE_URL': 'https://tenant.example.com', 'DARWINBOX_LEAVE_API_KEY': 'private-api-key',
            'DARWINBOX_LEAVE_USERNAME': 'private-username', 'DARWINBOX_LEAVE_PASSWORD': 'private-password',
            'DARWINBOX_HOLIDAY_API_KEY': 'private-holiday-key'}, clear=True):
            configured = configuration_status()
            self.assertEqual(LeaveBalanceAdapter().url, 'https://tenant.example.com/leavesactionapi/leavebalance')
        self.assertTrue(configured['leave']['configured'])
        self.assertTrue(configured['holidays']['configured'])
        self.assertNotIn('private-', json.dumps(configured))
        self.assertNotIn('tenant.example.com', json.dumps(configured))

    async def test_default_endpoint_payload_and_basic_auth_match_documented_contract(self):
        fake = SimpleNamespace(endpoint=LeaveBalanceAdapter().client.endpoint, request=AsyncMock(return_value={
            'status': 1, 'data': [{'leave_name': 'Casual Leave', 'currently_availabel_balance': 4}]}))
        with patch.dict(os.environ, {'DARWINBOX_BASE_URL': 'https://tenant.example.com', 'DARWINBOX_LEAVE_API_KEY': 'key',
            'DARWINBOX_LEAVE_USERNAME': 'user', 'DARWINBOX_LEAVE_PASSWORD': 'password'}, clear=True):
            values, _ = await LeaveBalanceAdapter(fake).fetch('E001')
        self.assertEqual(values[0].balance, '4')
        args = fake.request.await_args.kwargs
        self.assertEqual(args['url'], 'https://tenant.example.com/leavesactionapi/leavebalance')
        self.assertEqual(args['payload']['employee_nos'], ['E001'])
        self.assertEqual(args['basic_auth'], ('user', 'password'))

    async def test_missing_config_is_logged_with_setting_name(self):
        tracker = SimpleNamespace(sender_id='test', latest_message={'text': 'Show my leaves', 'metadata': {}}, get_latest_input_channel=lambda: 'rest')
        dispatcher = CollectingDispatcher()
        with patch.object(actions, '_employee_for_tracker', return_value=('E001', {}, False)), \
                patch.object(actions, '_personal_log'), patch.object(actions, '_personal_balance_answer', AsyncMock(side_effect=actions.DarwinboxConfigurationError('DARWINBOX_LEAVE_PASSWORD must be configured'))), \
                self.assertLogs('actions.actions') as logs:
            await actions.ActionPersonalLeaveBalance().run(dispatcher, tracker, {})
        self.assertIn('DARWINBOX_LEAVE_PASSWORD', '\n'.join(logs.output))
