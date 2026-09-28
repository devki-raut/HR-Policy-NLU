from pathlib import Path
import tempfile
import unittest
from hr_policy.engine import PolicyIndex, clean_pages, ingest

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
