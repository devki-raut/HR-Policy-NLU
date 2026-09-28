import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile
import httpx
from fastapi.testclient import TestClient
from PIL import Image
from teams_app.package import build
from api.main import app

class TeamsTests(unittest.TestCase):
    def test_package(self):
        config = {'app_id': 'b805420a-2048-49ae-a26c-6a0cdd50b822', 'base_url': 'https://hr.example.org', 'developer_name': 'Example', 'website_url': 'https://example.org', 'privacy_url': 'https://example.org/privacy', 'terms_url': 'https://example.org/terms'}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'app.zip'
            manifest = build(config, path)
            self.assertEqual(manifest['staticTabs'][0]['scopes'], ['personal'])
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {'manifest.json', 'color.png', 'outline.png'})
                for file, size in [('color.png', 192), ('outline.png', 32)]:
                    with Image.open(io.BytesIO(archive.read(file))) as image:
                        self.assertEqual(image.size, (size, size))
                        image.verify()
            with self.assertRaises(ValueError):
                build({**config, 'base_url': 'http://insecure.example.org'}, path)

    def test_preview_headers_and_api_routes(self):
        with TestClient(app) as client:
            result = client.get('/')
            self.assertEqual(result.status_code, 200)
            self.assertIn('HR Policy Assistant', result.text)
            self.assertIn('https://*.cloud.microsoft', result.headers['content-security-policy'])
            self.assertEqual(client.post('/policies/upload').status_code, 422)
            self.assertNotIn('content-security-policy', client.get('/docs').headers)
            self.assertEqual(client.get('/static/app.js').status_code, 200)
            self.assertEqual(client.post('/chat', json={'sender': 'x', 'message': ''}).status_code, 422)

    def test_chat_proxy(self):
        original = httpx.AsyncClient
        def handler(request):
            self.assertEqual(request.url.path, '/webhooks/rest/webhook')
            return httpx.Response(200, json=[{'text': 'Policy answer with citation'}])
        with patch('api.main.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            with TestClient(app) as client:
                result = client.post('/chat', json={'sender': 'test', 'message': 'annual leave days'})
                self.assertEqual(result.json()['answer'], 'Policy answer with citation')

    def test_upstream_failure(self):
        original = httpx.AsyncClient
        with patch('api.main.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda req: httpx.Response(503)), **kw)):
            with TestClient(app) as client:
                self.assertEqual(client.post('/chat', json={'sender': 'test', 'message': 'annual leave days'}).status_code, 502)

    def test_ui_does_not_bypass_api_key(self):
        with patch.dict('os.environ', {'HR_API_KEY': 'secret'}):
            with TestClient(app) as client:
                self.assertNotIn('secret', client.get('/').text)
                self.assertEqual(client.post('/chat', json={'sender': 'x', 'message': 'leave days'}).status_code, 401)
