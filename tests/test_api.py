import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from api.main import app
from hr_policy.engine import ingest, PolicyIndex
from hr_policy.store import update_pdf


def pdf(text='Employees receive 25 days of annual leave per year.'):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 50 700 Td ({text}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class APITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.index = Path(temp.name) / 'policies.json'
        env = patch.dict(os.environ, {'POLICY_INDEX': str(self.index), 'HR_API_KEY': 'test-key'})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app, headers={'X-API-Key': 'test-key'})
        self.addCleanup(self.client.close)
        ingest('policies/sample', self.index)

    def upload(self, name='leave.pdf', content=None, mode='upsert'):
        return self.client.post('/policies/upload', files={'file': (name, pdf() if content is None else content, 'application/pdf')}, data={'mode': mode})

    def test_first_upload_overrides_demo_and_retrieval(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['demo_removed'])
        answer = PolicyIndex(self.index).answer('annual leave days', 'leave')
        self.assertIn('25 days', answer)
        self.assertNotIn('DEMO', answer)
        self.assertEqual(self.client.get('/policies').json()['documents'], [{'filename': 'leave.pdf', 'chunks': 1}])

    def test_upsert_and_replace(self):
        self.upload()
        self.upload('remote.pdf', pdf('Remote work is allowed three days each week.'))
        self.upload(content=pdf('Employees receive 30 days of annual leave.'))
        self.assertEqual(len(self.client.get('/policies').json()['documents']), 2)
        self.assertIn('30 days', PolicyIndex(self.index).answer('annual leave days'))
        self.upload('new.pdf', mode='replace')
        self.assertEqual(len(self.client.get('/policies').json()['documents']), 1)

    def test_failed_upload_preserves_index(self):
        original = self.index.read_bytes()
        for name, content in [('bad.pdf', b'not a pdf'), ('../evil.pdf', pdf()), ('bad.txt', pdf()), ('broken.pdf', b'%PDF- broken')]:
            with self.subTest(name=name):
                self.assertEqual(self.upload(name, content).status_code, 422)
                self.assertEqual(self.index.read_bytes(), original)

    def test_authentication(self):
        self.assertEqual(self.client.get('/policies', headers={'X-API-Key': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.get('/health', headers={'X-API-Key': ''}).status_code, 200)

    def test_chat_and_combined(self):
        response = {'sender': 'user', 'answer': 'answer', 'messages': [{'text': 'answer', 'custom': {'sources': [{'source':'leave.pdf', 'pdf_url':'/policies/pdfs/leave.pdf'}]}}]}
        with patch('api.main.rasa_chat', AsyncMock(return_value=response)) as chat:
            payload = self.client.post('/chat', json={'sender': 'user', 'message': 'annual leave days'}).json()
            self.assertEqual(payload['answer'], 'answer')
            self.assertIn('chunk', payload)
            self.assertIn('pdf_url', payload['chunk'])
            self.assertIn('/policies/pdfs/', payload['chunk']['pdf_url'])
            result = self.client.post('/ask', data={'sender': 'user', 'message': 'annual leave days'}, files={'file': ('leave.pdf', pdf(), 'application/pdf')})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()['policy_update']['demo_removed'])
            chat.assert_awaited_with('user', 'annual leave days')

    def test_chat_error_reports_committed_upload(self):
        from fastapi import HTTPException
        with patch('api.main.rasa_chat', AsyncMock(side_effect=HTTPException(502, 'offline'))):
            result = self.client.post('/ask', data={'sender': 'user', 'message': 'annual leave days'}, files={'file': ('leave.pdf', pdf(), 'application/pdf')})
            self.assertEqual(result.status_code, 502)
            self.assertEqual(result.json()['detail']['policy_update']['filename'], 'leave.pdf')

    def test_concurrent_imports_preserve_both_documents(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(update_pdf, name, pdf(), index=self.index) for name in ['a.pdf', 'b.pdf']]
            for future in futures:
                future.result()
        self.assertEqual({c['source'] for c in json.loads(self.index.read_text())['chunks']}, {'a.pdf', 'b.pdf'})

    def test_request_validation(self):
        self.assertEqual(self.upload(mode='bad').status_code, 422)
        self.assertEqual(self.client.post('/chat', json={'sender': 'x', 'message': ''}).status_code, 422)

    def test_policy_pdf_download_requires_auth_and_active_document(self):
        self.upload('demo.pdf')
        response = self.client.get('/policies/pdfs/demo.pdf')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/pdf', response.headers.get('content-type', ''))
        self.assertEqual(self.client.get('/policies/pdfs/demo.pdf', headers={'X-API-Key':'wrong'}).status_code, 401)
        self.upload('replacement.pdf', mode='replace')
        self.assertEqual(self.client.get('/policies/pdfs/demo.pdf').status_code, 404)

    def test_no_unrelated_evidence_added_to_greeting(self):
        with patch('api.main.rasa_chat', AsyncMock(return_value={'sender':'x','answer':'Hello','messages':[{'text':'Hello'}]})):
            result = self.client.post('/chat', json={'sender':'x','message':'hello'}).json()
        self.assertIsNone(result['chunk'])
        self.assertEqual(result['sources'], [])

if __name__ == '__main__':
    unittest.main()

class IntentEndpointTests(unittest.TestCase):
    def test_rasa_intent_forwarding(self):
        import httpx
        original=httpx.AsyncClient
        def handle(request):
            self.assertEqual(request.url.path,'/model/parse')
            self.assertEqual(json.loads(request.content)['text'],'bereavement days')
            return httpx.Response(200,json={'intent':{'name':'policy_bereavement_entitlement','confidence':.95},'intent_ranking':[]})
        with patch.dict(os.environ,{'HR_API_KEY':''}), patch('api.main.httpx.AsyncClient',side_effect=lambda **kw: original(transport=httpx.MockTransport(handle),**kw)):
            with TestClient(app) as client:
                result=client.post('/nlu/parse',json={'message':'bereavement days'})
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['policy_mapping']['sections'],['Leave Entitlement'])
