import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from botbuilder.schema import Activity, ChannelAccount, ConversationAccount
from teams_app.bot_package import broadcast as b
from teams_app.bot_package.bot_handler import TeamsBot


class BroadcastAdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'TEAMS_BROADCAST_DB': str(Path(self.temp.name)/'broadcast.db'),
            'TEAMS_BROADCAST_TENANT_ID': 'tenant', 'MICROSOFT_APP_ID': 'app', 'TEAMS_SUPERADMIN_OBJECT_IDS': 'admin'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.admin = {'tenant_id': 'tenant', 'aad_object_id': 'admin'}
        activity = Activity(channel_id='msteams', service_url='https://example.com',
            conversation=ConversationAccount(id='admin-chat', conversation_type='personal'),
            from_property=ChannelAccount(id='admin-teams', aad_object_id='admin'), recipient=ChannelAccount(id='bot'))
        self.context = SimpleNamespace(activity=activity, send_activity=AsyncMock(),
            adapter=SimpleNamespace(continue_conversation=AsyncMock()))

    def register(self, oid='employee', name='Employee Name', email='employee@example.com', verified=True, tenant='tenant'):
        identity = {'tenant_id': tenant, 'aad_object_id': oid, 'display_name': name,
                    'email': email, 'user_principal_name': email, 'profile_verified': verified}
        b.remember(self.context, identity)
        return identity

    async def draft(self, count=1):
        for i in range(count):
            self.register(f'employee-{i:03}', f'Employee {i:03}', f'employee{i}@example.com')
        await b.handle(self.context, self.admin, '/broadcast Meeting tomorrow')
        with b.database() as db:
            return db.execute('SELECT id FROM broadcasts ORDER BY created DESC').fetchone()[0]

    def test_email_bootstrap_requires_verified_profile_and_binds_object_id(self):
        os.environ.update(TEAMS_SUPERADMIN_OBJECT_IDS='', TEAMS_SUPERADMIN_EMAILS=' ADMIN@EXAMPLE.COM ')
        unverified = self.register('forged', email='admin@example.com', verified=False)
        self.assertFalse(b.is_superadmin(unverified))
        wrong_tenant = self.register('outside', email='admin@example.com', tenant='other')
        self.assertFalse(b.is_superadmin(wrong_tenant))
        admin = self.register('seed-admin', email='admin@example.com')
        self.assertTrue(b.is_superadmin(admin))
        self.assertTrue(b.is_superadmin({'tenant_id': 'tenant', 'aad_object_id': 'seed-admin'}))
        reused_email = self.register('different-object', email='admin@example.com')
        self.assertFalse(b.is_superadmin(reused_email))
        os.environ['TEAMS_SUPERADMIN_EMAILS'] = ''
        self.assertFalse(b.is_superadmin(admin))

    async def test_admin_can_grant_list_and_revoke_by_verified_email(self):
        employee = self.register()
        await b.handle(self.context, self.admin, '/superadmin-add EMPLOYEE@EXAMPLE.COM')
        self.assertTrue(b.is_superadmin(employee))
        await b.handle(self.context, employee, '/superadmin-list')
        self.assertIn('Employee Name', self.context.send_activity.await_args.args[0])
        await b.handle(self.context, self.admin, '/superadmin-remove employee@example.com')
        self.assertFalse(b.is_superadmin(employee))
        with b.database() as db:
            actions = [r[0] for r in db.execute('SELECT action FROM admin_audit ORDER BY created')]
        self.assertEqual(actions, ['grant', 'revoke'])

    async def test_untrusted_or_ambiguous_emails_and_nonadmin_cannot_grant(self):
        employee = self.register(verified=False)
        await b.handle(self.context, employee, '/superadmin-add employee@example.com')
        self.assertFalse(b.is_superadmin(employee))
        await b.handle(self.context, self.admin, '/superadmin-add employee@example.com')
        self.assertFalse(b.is_superadmin(employee))
        self.register('one', email='same@example.com')
        self.register('two', email='same@example.com')
        await b.handle(self.context, self.admin, '/superadmin-add same@example.com')
        with b.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM superadmins').fetchone()[0], 0)

    async def test_environment_email_admin_cannot_be_overwritten_or_revoked_in_chat(self):
        os.environ['TEAMS_SUPERADMIN_EMAILS'] = 'seed@example.com'
        seed = self.register('seed', email='seed@example.com')
        await b.handle(self.context, self.admin, '/superadmin-add seed@example.com')
        await b.handle(self.context, self.admin, '/superadmin-remove seed@example.com')
        self.assertTrue(b.is_superadmin(seed))
        with b.database() as db:
            self.assertEqual(db.execute('SELECT source FROM superadmins WHERE oid=?', ('seed',)).fetchone()[0], 'environment_email')

    async def test_small_report_names_are_snapshots_and_include_status(self):
        key = await self.draft(2)
        self.register('employee-000', 'Changed Name', 'changed@example.com')
        with b.database() as db:
            db.execute("UPDATE deliveries SET status='accepted' WHERE broadcast=? AND oid='employee-000'", (key,))
            db.execute("UPDATE deliveries SET status='failed_or_unknown' WHERE broadcast=? AND oid='employee-001'", (key,))
        await b.handle(self.context, self.admin, '/broadcast-status '+key)
        card = self.context.send_activity.await_args.args[0].attachments[0].content
        texts = '\n'.join(row['text'] for row in card['body'])
        self.assertIn('Employee 000 — accepted', texts)
        self.assertIn('Employee 001 — failed_or_unknown', texts)
        self.assertNotIn('Changed Name', texts)

    async def test_large_list_popup_paginates_and_rechecks_owner_and_admin(self):
        key = await self.draft(25)
        await b.handle(self.context, self.admin, '/broadcast-status '+key)
        summary = self.context.send_activity.await_args.args[0].attachments[0].content
        self.assertEqual(summary['actions'][0]['data']['msteams']['type'], 'task/fetch')
        self.assertLess(len(summary['body']), 10)
        self.context.activity.value = {'data': {'action': 'broadcast_recipients', 'broadcast_id': key, 'page': 0}}
        result = await b.recipient_dialog(self.context, self.admin)
        self.assertEqual(result.body['task']['type'], 'continue')
        card = result.body['task']['value']['card']['content']
        self.assertIn('page 1/2', card['body'][0]['text'])
        self.assertEqual(len(card['body']), 22)
        self.context.activity.value = {'data': card['actions'][0]['data']}
        result = await b.recipient_dialog(self.context, self.admin)
        self.assertIn('page 2/2', result.body['task']['value']['card']['content']['body'][0]['text'])
        for identity in [{'tenant_id': 'tenant', 'aad_object_id': 'ordinary'}, {'tenant_id': 'other', 'aad_object_id': 'admin'}]:
            denied = await b.recipient_dialog(self.context, identity)
            self.assertEqual(denied.body['task']['type'], 'message')
        os.environ['TEAMS_SUPERADMIN_OBJECT_IDS'] = 'admin,other-admin'
        denied = await b.recipient_dialog(self.context, {'tenant_id': 'tenant', 'aad_object_id': 'other-admin'})
        self.assertEqual(denied.body['task']['type'], 'message')
        self.context.activity.conversation.id = 'different-chat'
        denied = await b.recipient_dialog(self.context, self.admin)
        self.assertEqual(denied.body['task']['type'], 'message')

    async def test_dialog_dispatch_and_revocation_recheck(self):
        key = await self.draft()
        self.context.activity.value = {'data': {'action': 'broadcast_recipients', 'broadcast_id': key, 'page': 'invalid'}}
        for event in ['task/fetch', 'task/submit']:
            self.context.activity.name = event
            with patch('teams_app.bot_package.bot_handler._teams_identity', AsyncMock(return_value=self.admin)):
                result = await TeamsBot().on_invoke_activity(self.context)
            self.assertEqual(result.body['task']['type'], 'continue')
        os.environ['TEAMS_SUPERADMIN_OBJECT_IDS'] = ''
        denied = await b.recipient_dialog(self.context, self.admin)
        self.assertEqual(denied.body['task']['type'], 'message')

