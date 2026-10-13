import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from teams_app.bot_package import ticket_mail as mail


class SendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = patch.object(mail, 'ROOT', Path(self.temp.name))
        self.root.start()
        self.owner = 'tenant:user:conversation'
        self.key = mail.create_draft(self.owner, 'Initial subject', 'Initial body')
        self.posts = []
        self.real_client = httpx.AsyncClient

    def tearDown(self):
        self.root.stop()
        self.temp.cleanup()

    def client(self, handler):
        return patch.object(mail.httpx, 'AsyncClient', side_effect=lambda **kwargs: self.real_client(transport=httpx.MockTransport(handler), **kwargs))

    def success(self, request):
        if request.method == 'GET': return httpx.Response(200, json={'id': 'user'})
        self.posts.append(json.loads(request.content))
        return httpx.Response(202)

    async def test_edits_recipient_and_repeat_click(self):
        mail.update_draft(self.key, self.owner, 'Edited subject', 'Edited body')
        with self.client(self.success):
            await mail.send_draft(self.key, self.owner, 'mock-token', 'user')
            with self.assertRaises(mail.MailError):
                await mail.send_draft(self.key, self.owner, 'mock-token', 'user')
        self.assertEqual(len(self.posts), 1)
        message = self.posts[0]['message']
        self.assertEqual(message['subject'], 'Edited subject')
        self.assertEqual(message['body']['content'], 'Edited body')
        self.assertEqual(message['toRecipients'][0]['emailAddress']['address'], 'helpdesk@emergys.com')
        self.assertTrue(self.posts[0]['saveToSentItems'])

    async def test_other_user_cannot_send(self):
        with self.assertRaises(mail.MailError):
            mail.update_draft(self.key, 'another-owner', 'Subject', 'Body')
        with self.client(self.success), self.assertRaises(mail.MailError):
            await mail.send_draft(self.key, 'another-owner', 'mock-token', 'user')
        self.assertFalse(self.posts)

    async def test_temporary_test_recipient(self):
        with self.client(self.success):
            await mail.send_draft(self.key, self.owner, 'mock-token', 'user', recipient='ojask354@gmail.com')
        self.assertEqual(self.posts[0]['message']['toRecipients'][0]['emailAddress']['address'], 'ojask354@gmail.com')
        events = [json.loads(line) for line in (Path(self.temp.name) / 'artifacts/logs/ticket_mail.log').read_text().splitlines()]
        self.assertEqual([event['event'] for event in events], ['send_attempt', 'send_accepted'])
        self.assertEqual(events[-1]['http_status'], 202)
        self.assertNotIn('mock-token', json.dumps(events))

    async def test_wrong_graph_account(self):
        with self.client(lambda request: httpx.Response(200, json={'id': 'someone-else'})), self.assertRaises(mail.MailError):
            await mail.send_draft(self.key, self.owner, 'mock-token', 'user')
        self.assertFalse(self.posts)

    async def test_cancel_prevents_send(self):
        mail.cancel_draft(self.key, self.owner)
        with self.client(self.success), self.assertRaises(mail.MailError):
            await mail.send_draft(self.key, self.owner, 'mock-token', 'user')
        self.assertFalse(self.posts)

    async def test_timeout_is_not_automatically_retried(self):
        def handler(request):
            if request.method == 'GET': return httpx.Response(200, json={'id': 'user'})
            self.posts.append(request)
            raise httpx.ReadTimeout('mock timeout', request=request)
        with self.client(handler):
            for _ in range(2):
                with self.assertRaises(mail.MailError):
                    await mail.send_draft(self.key, self.owner, 'mock-token', 'user')
        self.assertEqual(len(self.posts), 1)

    async def test_unconfigured_connection_never_sends(self):
        from types import SimpleNamespace
        from teams_app.bot_package.bot_handler import TeamsBot
        context = SimpleNamespace(activity=SimpleNamespace(conversation=SimpleNamespace(id='conversation')), send_activity=AsyncMock())
        identity = {'tenant_id': 'tenant', 'aad_object_id': 'user'}
        with patch('teams_app.bot_package.bot_handler._teams_identity', new=AsyncMock(return_value=identity)), patch.dict('os.environ', {'TEAMS_MAIL_OAUTH_CONNECTION': ''}), patch('teams_app.bot_package.bot_handler.send_draft', new=AsyncMock()) as send:
            await TeamsBot()._raise_ticket(context, {'request_id': self.key, 'ticket_subject': 'Edited subject', 'ticket_body': 'Edited body'})
            send.assert_not_awaited()
            self.assertIn('Direct sending is not configured', context.send_activity.call_args.args[0])

    def test_inline_card(self):
        from teams_app.bot_package.bot_handler import _ticket_card
        outlook_url = 'https://outlook.office.com/mail/deeplink/compose?to=helpdesk%40emergys.com&subject=Access&body=Dear%20Team%2C'
        card = _ticket_card({'to': 'helpdesk@emergys.com', 'subject': 'Access', 'body': 'Dear Team,', 'outlook_url': outlook_url}, self.key)
        self.assertEqual([a['title'] for a in card['actions']], ['Raise a request', 'Open in Outlook', 'Cancel'])
        self.assertEqual(card['actions'][1]['type'], 'Action.OpenUrl')
        self.assertEqual(card['actions'][1]['url'], outlook_url)
        self.assertEqual([i['id'] for i in card['body'] if i['type'] == 'Input.Text'], ['ticket_subject', 'ticket_body'])
        self.assertEqual(card['actions'][0]['data']['request_id'], self.key)


if __name__ == '__main__': unittest.main()
