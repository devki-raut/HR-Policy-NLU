import json
from pathlib import Path
import tempfile
import unittest
from test_api import pdf
from hr_policy.store import update_pdf, rebuild_pdf_index, chunk_markdown
from hr_policy.engine import PolicyIndex

class RagTests(unittest.TestCase):
    def test_page_markers_and_hard_chunk_limit(self):
        chunks = chunk_markdown('## Page 5\n### 1. Entitlement\n'+'word '*401, 'test.pdf', max_words=180)
        self.assertEqual(len(chunks),3)
        self.assertTrue(all(c['page']==5 and len(c['text'].split())<=180 for c in chunks))

    def test_failed_bulk_rebuild_preserves_library(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); index=root/'index.json'
            update_pdf('existing.pdf',pdf(),index=index)
            original=index.read_bytes()
            (root/'a.pdf').write_bytes(pdf())
            (root/'b.pdf').write_bytes(b'broken')
            with self.assertRaises(ValueError):
                rebuild_pdf_index(root,index,mode='replace')
            self.assertEqual(index.read_bytes(),original)

    def test_empty_rebuild_preserves_library(self):
        with tempfile.TemporaryDirectory() as folder:
            index=Path(folder)/'index.json'
            update_pdf('existing.pdf',pdf(),index=index)
            old=index.read_bytes()
            with self.assertRaises(ValueError):
                rebuild_pdf_index(folder,index,mode='replace')
            self.assertEqual(old,index.read_bytes())

    def test_upload_does_not_write_shared_source_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            index=Path(folder)/'index.json'
            result=update_pdf('isolated-test.pdf',pdf(),index=index)
            document=json.loads(index.read_text())['documents']['isolated-test.pdf']
            self.assertTrue((index.parent/document['pdf_path']).exists())
            self.assertFalse(Path('policies/pdfs/isolated-test.pdf').exists())

    def test_document_route_never_leaks_another_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            index=Path(folder)/'index.json'
            update_pdf('unrelated.pdf',pdf(),index=index)
            result=PolicyIndex(index).respond('bereavement leave days',intent='policy_bereavement_entitlement')
            self.assertEqual(result['status'],'no_evidence')

    def test_registered_policies_have_six_intents_and_real_sections(self):
        from collections import Counter
        from hr_policy.engine import intent_mapping
        mapping = intent_mapping()
        self.assertEqual(set(Counter(v['source'] for v in mapping.values()).values()), {6})
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'index.json'
            filenames={v['source'] for v in mapping.values()}
            if not all((Path('policies/pdfs')/name).exists() for name in filenames):
                self.skipTest('Company PDFs are not distributed with the repository')
            rebuild_pdf_index('policies/pdfs', target, 'replace', filenames)
            index=PolicyIndex(target)
            for intent, route in mapping.items():
                with self.subTest(intent=intent):
                    result=index.respond(route['examples'][0], intent=intent)
                    self.assertEqual(result['status'],'answered')
                    self.assertTrue(all(s['source']==route['source'] for s in result['sources']))
                    self.assertTrue(all(s['page'] > 0 for s in result['sources']))

    def test_semantic_results_still_obey_category_filter(self):
        from unittest.mock import patch
        from hr_policy.engine import ingest
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'index.json'
            ingest('policies/sample',target)
            index=PolicyIndex(target)
            index.embeddings=[[1.0,0.0] for _ in index.chunks]
            with patch.object(index,'embed',return_value=[[1.0,0.0]]):
                matches=index.retrieve('unusual phrasing',category='expenses')
            self.assertTrue(matches)
            self.assertTrue(all(chunk.category=='expenses' for _,chunk in matches))

    def test_generation_adapter_calls_backend_and_validates_quotes(self):
        import httpx
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from scripts.local_model_service import app
        original=httpx.AsyncClient
        quote='Employees receive five days of paid leave.'
        def handler(request):
            self.assertTrue(request.url.path.endswith('/chat/completions'))
            body=json.loads(request.content)
            self.assertEqual(body['temperature'],0)
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'evidence':[quote]})}}]})
        with patch('scripts.local_model_service.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            with TestClient(app) as client:
                result=client.post('/generate',json={'question':'How much leave?', 'excerpt':quote})
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['evidence'],[quote])

    def test_generation_health_fails_when_model_is_unavailable(self):
        import httpx
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from scripts.local_model_service import app
        original=httpx.AsyncClient
        with patch('scripts.local_model_service.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(lambda r:httpx.Response(503)),**kw)):
            with TestClient(app) as client:
                self.assertEqual(client.get('/health').status_code,503)

    def test_multiple_documents_clarify_even_with_confident_intent(self):
        from hr_policy.engine import ingest
        with tempfile.TemporaryDirectory() as folder:
            index=Path(folder)/'index.json'
            ingest('policies/sample',index)
            result=PolicyIndex(index).respond('Compare maternity and bereavement leave',intent='policy_bereavement_entitlement')
            self.assertEqual(result['status'],'clarify')
            self.assertEqual(result['sources'],[])

    def test_broad_bereavement_policy_question_answers_instead_of_clarifying(self):
        from hr_policy.engine import ingest
        with tempfile.TemporaryDirectory() as folder:
            index=Path(folder)/'index.json'
            ingest('policies/sample',index)
            result=PolicyIndex(index).respond('What is the bereavement leave policy?', intent='ask_leave')
            self.assertEqual(result['status'], 'answered')
            self.assertTrue(result['answer'])
            self.assertTrue(result['sources'])

    def test_local_model_host_uses_vllm_openai_server(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('local_model_host', Path('scripts/local_model_host.py'))
        self.assertIsNotNone(spec)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cmd = mod.build_backend_command('HuggingFaceTB/SmolLM2-360M-Instruct','127.0.0.1',9001)
        self.assertIn('vllm.entrypoints.openai.api_server', ' '.join(cmd))
        self.assertIn('--model', cmd)
        self.assertIn('--port', cmd)
        self.assertEqual(cmd[cmd.index('--port') + 1], '9001')
        self.assertNotIn('--device', cmd)

    def test_local_model_host_sets_cpu_target_via_env(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('local_model_host', Path('scripts/local_model_host.py'))
        self.assertIsNotNone(spec)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with __import__('unittest').mock.patch.dict('os.environ', {'MODEL_DEVICE': 'cpu'}, clear=False):
            cmd = mod.build_backend_command('HuggingFaceTB/SmolLM2-360M-Instruct','127.0.0.1',9001)
            self.assertNotIn('--device', cmd)
            self.assertEqual(mod.main.__code__.co_argcount, 0)
