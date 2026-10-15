import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from teams_app.bot_package import broadcast as b

class BroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TEAMS_BROADCAST_DB': self.tmp.name + '/state.db', 'TEAMS_BROADCAST_TENANT_ID': 'tenant', 'TEAMS_SUPERADMIN_OBJECT_IDS': 'admin', 'MICROSOFT_APP_ID': 'app'})
        self.env.start()
        self.identity = {'aad_object_id': 'admin', 'tenant_id': 'tenant'}
        self.context = SimpleNamespace(activity=SimpleNamespace(channel_id='msteams', conversation=SimpleNamespace(id='chat', conversation_type='personal')), send_activity=AsyncMock(), adapter=SimpleNamespace(continue_conversation=AsyncMock()))
    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()
    def test_database_path_resolves_from_project(self):
        with patch.dict(os.environ, {'TEAMS_BROADCAST_DB': 'artifacts/teams/broadcast.sqlite3'}):
            self.assertEqual(b.database_path(), b.ROOT / 'artifacts/teams/broadcast.sqlite3')
        with patch.dict(os.environ, {'TEAMS_BROADCAST_DB': ''}):
            self.assertEqual(b.database_path(), b.ROOT / 'artifacts/teams/broadcast.sqlite3')
        with patch.dict(os.environ, {'TEAMS_BROADCAST_DB': self.tmp.name + '/custom.db'}):
            self.assertEqual(str(b.database_path()), self.tmp.name + '/custom.db')

    async def test_nonadmin_and_wrong_tenant_cannot_broadcast(self):
        for identity in ({'aad_object_id':'user','tenant_id':'tenant'}, {'aad_object_id':'admin','tenant_id':'other'}):
            await b.handle(self.context, identity, '/broadcast hello')
        with b.database() as db: self.assertEqual(db.execute('SELECT count(*) FROM broadcasts').fetchone()[0], 0)
        self.context.adapter.continue_conversation.assert_not_awaited()
    async def draft(self):
        with b.database() as db:
            db.execute('INSERT OR REPLACE INTO recipients VALUES (?, ?, ?)', ('tenant', 'user', json.dumps({'channelId':'msteams', 'serviceUrl':'https://example.com', 'conversation':{'id':'target'}})))
        await b.handle(self.context, self.identity, '/broadcast hello everyone')
        with b.database() as db: return db.execute('SELECT id FROM broadcasts ORDER BY created DESC LIMIT 1').fetchone()[0]
    async def test_confirm_once_and_no_send_on_preview(self):
        token = await self.draft()
        self.context.adapter.continue_conversation.assert_not_awaited()
        await b.handle(self.context, self.identity, '/broadcast-confirm ' + token)
        await b.handle(self.context, self.identity, '/broadcast-confirm ' + token)
        self.assertEqual(self.context.adapter.continue_conversation.await_count, 1)
    async def test_cancel_and_expiry(self):
        token = await self.draft()
        await b.handle(self.context, self.identity, '/broadcast-cancel ' + token)
        await b.handle(self.context, self.identity, '/broadcast-confirm ' + token)
        token = await self.draft()
        with b.database() as db: db.execute('UPDATE broadcasts SET created=0 WHERE id=?', (token,))
        await b.handle(self.context, self.identity, '/broadcast-confirm ' + token)
        self.context.adapter.continue_conversation.assert_not_awaited()
    async def test_owner_chat_binding_and_failure_reporting(self):
        token = await self.draft()
        self.context.activity.conversation.id = 'other'
        await b.handle(self.context, self.identity, '/broadcast-confirm ' + token)
        self.context.adapter.continue_conversation.assert_not_awaited()
        self.context.activity.conversation.id = 'chat'
        self.context.adapter.continue_conversation.side_effect = RuntimeError('timeout')
        await b.handle(self.context, self.identity, '/broadcast-confirm ' + token)
        with b.database() as db: self.assertEqual(db.execute('SELECT status FROM deliveries WHERE broadcast=?',(token,)).fetchone()[0], 'failed_or_unknown')
    async def test_group_chat_denied(self):
        self.context.activity.conversation.conversation_type = 'groupChat'
        await b.handle(self.context, self.identity, '/broadcast hello')
        with b.database() as db: self.assertEqual(db.execute('SELECT count(*) FROM broadcasts').fetchone()[0],0)
