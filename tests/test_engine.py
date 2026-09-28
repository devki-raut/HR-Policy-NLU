import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hr_policy.engine import PolicyIndex, clean_pages, ingest
from hr_policy.store import update_pdf

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.index = Path(self.tmp.name) / 'index.json'
        self.chunks = ingest('policies/sample', self.index)
        self.engine = PolicyIndex(self.index)

    def test_cited_annual_leave(self):
        answer = self.engine.answer('How many days of annual leave do I get?', 'leave')
        self.assertIn('20 days', answer)
        self.assertIn('Source: employee-handbook.md — Annual leave, page 1', answer)
        self.assertIn('DEMO POLICY ONLY', answer)

    def test_supported_topics(self):
        for question, category, expected in [
            ('How many sick leave days?', 'leave', '10 days'),
            ('How many remote work days?', 'remote_work', 'two days'),
            ('When do health insurance benefits begin?', 'benefits', 'first day'),
            ('When submit travel expenses?', 'expenses', '30 days'),
            ('How to report workplace harassment?', 'conduct', 'confidentially'),
            ('How long is the probation period?', 'onboarding', '90 days'),
        ]:
            with self.subTest(category=category):
                self.assertIn(expected, self.engine.answer(question, category))

    def test_generic_annual_leave_question_routes_to_general_entitlement(self):
        index = Path(self.tmp.name) / 'annual_leave_conflict.json'
        index.write_text(json.dumps({
            'version': 1,
            'chunks': [
                {
                    'id': 'annual-1',
                    'source': 'general_leave.pdf',
                    'page': 1,
                    'section': 'Eligibility / Scope',
                    'category': 'leave',
                    'text': 'Permanent employees receive 18 annual leave days each year.'
                },
                {
                    'id': 'paternity-1',
                    'source': 'parental_leave.pdf',
                    'page': 2,
                    'section': '4.2 Paternity Leave Entitlement',
                    'category': 'leave',
                    'text': 'New fathers receive 10 days of paternity leave.'
                }
            ]
        }), encoding='utf-8')
        answer = PolicyIndex(index).answer('How many days of annual leave do I get?', intent='ask_leave')
        self.assertNotIn('Which policy section do you mean', answer)
        self.assertIn('18 annual leave days', answer)
        self.assertIn('Source:', answer)

    def test_bereavement_and_funeral_questions_resolve_to_leave(self):
        pdf_path = Path('policies/pdfs/Bereavement Leave Policy_Emergys Solutions.pdf')
        self.assertTrue(pdf_path.exists(), f'Missing policy PDF: {pdf_path}')
        payload = pdf_path.read_bytes()
        index = Path(self.tmp.name) / 'bereavement.json'
        update_pdf(pdf_path.name, payload, index=index)
        answer = PolicyIndex(index).answer('How much paid time off is available for a funeral?', intent='policy_bereavement_entitlement')
        self.assertIn('5 consecutive working days', answer)
        self.assertIn('Source:', answer)

    def test_same_document_pages_are_not_treated_as_ambiguous(self):
        pdf_path = Path('policies/pdfs/Bereavement Leave Policy_Emergys Solutions.pdf')
        self.assertTrue(pdf_path.exists(), f'Missing policy PDF: {pdf_path}')
        index = Path(self.tmp.name) / 'bereavement_ambiguous.json'
        update_pdf(pdf_path.name, pdf_path.read_bytes(), index=index)
        answer = PolicyIndex(index).answer('What is the bereavement leave policy?')
        self.assertNotIn('Which policy section do you mean', answer)
        self.assertIn('bereavement', answer.lower())

    def test_short_generated_answer_with_citation(self):
        answer = self.engine.answer('How many days of annual leave do I get?', 'leave', concise=True)
        self.assertIn('20 days', answer)
        self.assertIn('Source:', answer)
        self.assertIn('manager', answer)

    @patch('hr_policy.engine.generate_policy_answer')
    def test_generation_path_uses_policy_excerpt_and_citation(self, generate):
        generate.return_value = 'Employees receive 20 days of annual leave per year. Source: employee-handbook.md — Annual leave, page 1 [chunk abc123]'
        answer = self.engine.answer('How many days of annual leave do I get?', 'leave', generate=True)
        self.assertIn('20 days', answer)
        self.assertIn('Source:', answer)
        generate.assert_called_once()

    @patch('hr_policy.engine.httpx.post')
    def test_generation_rejects_invented_evidence(self, post):
        post.return_value.json.return_value = {'evidence':['Employees receive 999 days off.']}
        with patch.dict('os.environ', {'POLICY_GENERATION_URL':'http://localhost:9000/generate'}):
            answer = self.engine.answer('annual leave days', 'leave', generate=True)
        self.assertIn('20 days', answer)
        self.assertNotIn('999', answer)

    @patch('hr_policy.engine.httpx.post')
    def test_generation_accepts_exact_evidence(self, post):
        quote = 'Employees receive 20 days of annual leave per year.'
        post.return_value.json.return_value = {'evidence':[quote]}
        with patch.dict('os.environ', {'POLICY_GENERATION_URL':'http://localhost:9000/generate'}):
            answer = self.engine.answer('annual leave days', 'leave', generate=True)
        self.assertIn(quote, answer)
        self.assertIn('Source:', answer)

    def test_unknown_and_short_questions(self):
        self.assertIn('couldn’t find', self.engine.answer('quantum banana spaceship'))
        self.assertIn('add details', self.engine.answer('leave'))

    def test_multi_topic(self):
        self.assertIn('one topic', self.engine.answer('leave and remote work'))

    def test_wrong_category_does_not_leak(self):
        self.assertNotIn('Source:', self.engine.answer('annual leave allowance', 'expenses'))

    def test_stable_ids(self):
        self.assertEqual(self.chunks, ingest('policies/sample', self.index))

    def test_headers_and_page_numbers(self):
        result = clean_pages(['Handbook\nPolicy A\nPage 1', 'Handbook\nPolicy B\nPage 2'])
        self.assertEqual(result, ['Policy A', 'Policy B'])

    def test_no_documents(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                ingest(folder, self.index)

    def test_chunk_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'long.md').write_text('## Leave\n' + 'word ' * 401)
            chunks = ingest(folder, self.index)
            self.assertEqual(len(chunks), 3)
            self.assertTrue(all(len(c.text.split()) <= 180 for c in chunks))

if __name__ == '__main__':
    unittest.main()
