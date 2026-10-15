import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from actions import conversation_log, identity_store, policy_store
from actions.runtime_paths import PROJECT_ROOT, runtime_path, runtime_root
from actions.darwinbox_adapters.report import directory_path
from actions.darwinbox_adapters.store import employee_store_path
from teams_app.bot_package.broadcast import database_path
from teams_app import web_paths


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_local_defaults_and_relative_override(self):
        self.assertIsNone(runtime_root())
        self.assertEqual(runtime_path('identity/users.json', '/local/users.json'), Path('/local/users.json'))
        os.environ['IDENTITY_STORE_PATH'] = 'artifacts/custom.json'
        self.assertEqual(identity_store._path(), PROJECT_ROOT/'artifacts/custom.json')
        self.assertEqual(web_paths.web_port(), 8610)

    def test_azure_defaults_put_all_writable_locations_in_tmp(self):
        os.environ['WEBSITE_INSTANCE_ID'] = 'azure-instance'
        expected = Path('/tmp/employeeassist')
        for path in [identity_store._path(), conversation_log._log_path(), directory_path(),
                     employee_store_path(), database_path(), policy_store.policy_pdf_dir(),
                     policy_store.policy_markdown_dir()]:
            self.assertTrue(path.is_relative_to(expected), str(path))
        self.assertEqual(web_paths.web_port(), 8608)
        os.environ['TEAMS_BROADCAST_DB'] = 'teams/broadcast.sqlite3'
        self.assertEqual(database_path(), expected/'teams/broadcast.sqlite3')

    def test_configured_root_and_explicit_file_override(self):
        os.environ.update(DATA_ROOT='/tmp/custom-data', WEBSITE_INSTANCE_ID='azure-instance')
        self.assertEqual(runtime_root(), Path('/tmp/custom-data/employeeassist'))
        os.environ['IDENTITY_STORE_PATH'] = '/mounted/identity.json'
        self.assertEqual(identity_store._path(), Path('/mounted/identity.json'))
        os.environ['POLICY_INDEX'] = 'policies/custom.json'
        self.assertEqual(policy_store.index_path(), Path('/tmp/custom-data/employeeassist/policies/custom.json'))
        os.environ['WEB_PORT'] = '9000'
        self.assertEqual(web_paths.web_port(), 9000)

    def test_policy_bootstrap_preserves_runtime_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'bundled'
            source.mkdir()
            (source/'policies.json').write_text('{"chunks": []}')
            (source/'pdfs').mkdir()
            (source/'pdfs/example.pdf').write_bytes(b'example PDF')
            with patch.dict(os.environ, {'DATA_ROOT': str(root/'runtime')}), \
                    patch.object(policy_store, 'DEFAULT_POLICY_ROOT', source), \
                    patch.object(policy_store, 'DEFAULT_INDEX', source/'policies.json'):
                index = policy_store.index_path()
                self.assertEqual(json.loads(index.read_text()), {'chunks': []})
                self.assertEqual((index.parent/'pdfs/example.pdf').read_bytes(), b'example PDF')
                index.write_text('{"runtime": true}')
                (source/'policies.json').write_text('{"bundled": true}')
                self.assertEqual(json.loads(policy_store.index_path().read_text()), {'runtime': True})

    def test_identity_persistence_failure_does_not_reject_verified_identity(self):
        identity = {'tenant_id': 'tenant', 'aad_object_id': 'user', 'email': 'employee@example.com'}
        with patch.object(identity_store.Path, 'mkdir', side_effect=PermissionError), self.assertLogs('actions.identity_store'):
            saved = identity_store.save_verified_identity(identity, 'test')
        self.assertEqual(saved['aad_object_id'], 'user')

    def test_public_prefix_and_jupyter_raw_paths(self):
        from types import SimpleNamespace
        from teams_app.web_package.jupyter import target
        os.environ['PUBLIC_BASE_URL'] = 'https://example.com/employeeassist/'
        self.assertEqual(web_paths.public_path('/privacy-policy'), '/employeeassist/privacy-policy')
        self.assertEqual(web_paths.public_base_url(), 'https://example.com/employeeassist')
        for path in [b'/jupyter/lab', b'/employeeassist/jupyter/lab']:
            connection = SimpleNamespace(scope={'raw_path': path, 'query_string': b'token=abc'}, url=SimpleNamespace(path=path.decode()))
            self.assertEqual(str(target(connection)), 'http://127.0.0.1:8888/employeeassist/jupyter/lab?token=abc')

