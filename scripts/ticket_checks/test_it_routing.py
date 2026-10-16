import json
from pathlib import Path
import unittest
import yaml
from actions.ticket_nlu import support_route

ROOT = Path(__file__).resolve().parents[2]
class ITRoutingTests(unittest.TestCase):
    def test_additional_questions_are_data_driven_and_route_correctly(self):
        from actions.it_support import match_it_faq
        cases = [
            ('My laptop battery is not charging. What should I check?', 'it_device_support', 'approved charger'),
            ('How do I request access to the project shared drive?', 'it_storage_collaboration', 'business reason'),
            ('Citrix says access is denied. What should I check?', 'it_remote_access', 'device registration'),
        ]
        for question, route, phrase in cases:
            with self.subTest(question=question):
                result = match_it_faq(question)
                self.assertEqual(result['routing_intent'], route)
                self.assertEqual(result['match_mode'], 'cosine_similarity')
                self.assertIn(phrase, result['answer'])

    def test_common_troubleshooting_questions_get_specific_checks(self):
        from actions.it_support import match_it_faq
        cases = [
            ('My emails are stuck in the Outbox. What should I check?', 'it_symptom_outbox', 'Outlook is online', 'Outlook on the web'),
            ('I’m connected to Wi-Fi, but websites won’t load. What can I try?', 'it_symptom_wifi_browsing', 'another website', 'another device'),
        ]
        for question, faq_id, first, second in cases:
            with self.subTest(question=question):
                result = match_it_faq(question)
                self.assertEqual(result['match_mode'], 'cosine_similarity')
                self.assertTrue(result['faq_ids'])
                self.assertIn(first, result['answer'])
                self.assertIn(second, result['answer'])
                self.assertNotIn('Choose Create IT ticket', result['answer'])

    def test_reviewed_troubleshooting_questions_are_generated_for_nlu(self):
        from scripts.import_it_report import additional_faq_entries
        entries = json.loads((ROOT/'data/faq_answers.json').read_text())['entries']
        nlu = yaml.safe_load((ROOT/'data/nlu_it.yml').read_text())['nlu']
        by_id = {entry['id']: entry for entry in entries}
        by_route = {entry['intent']: entry['examples'] for entry in nlu}
        for expected in additional_faq_entries():
            faq_id = expected['id']
            entry = by_id[faq_id]
            self.assertIn('- ' + entry['question'], by_route[entry['routing_intent']])

    def test_general_checks_and_distinctive_recorded_fix(self):
        from actions.it_support import match_it_faq
        general = match_it_faq('The office internet is very slow. What can I check?')
        self.assertEqual(general['match_mode'], 'cosine_similarity')
        self.assertIn('compare Wi-Fi with a wired connection', general['answer'])
        self.assertNotIn('Recorded resolution', general['answer'])

        matched = match_it_faq('GEMS unavailable while Outlook worked')
        self.assertEqual(matched['resolution_key'], 'dns')
        self.assertIn('DNS', matched['answer'])
        self.assertIn('not a confirmed diagnosis', matched['answer'])

    def test_it_prediction_survives_generic_issue_guard(self):
        self.assertIsNone(support_route('My VPN is not working', 'it_remote_access'))
        self.assertEqual(support_route('My VPN is not working please create a ticket', 'it_remote_access'), 'contact_support')
    def test_policy_and_contact_routing(self):
        self.assertIsNone(support_route('What is the maternity leave policy?', 'policy_maternity'))
        self.assertEqual(support_route('What is the IT support email address?', 'it_email_access'), 'ask_support_contact')
        self.assertEqual(support_route('What is the remote work policy?', 'report_it_issue'), 'nlu_fallback')
    def test_report_counts_and_separate_faq_routes(self):
        entries=json.loads((ROOT/'data/faq_answers.json').read_text())['entries']
        it=[e for e in entries if e.get('category')=='it_support']
        self.assertEqual(len(it),10)
        self.assertEqual(sum(e.get('incident_count', 0) for e in entries if e.get('category') == 'it_incident'),230)
        hr={e['routing_intent'] for e in entries if not e.get('category', '').startswith('it_')}
        self.assertFalse(hr & {e['routing_intent'] for e in it})

    def test_diet_intent_is_not_overridden_and_cosine_retrieves_gems_fix(self):
        from actions.it_support import match_it_faq
        for text in ['GEMS unavailable while Outlook worked', 'Outlook is working fine but GEMS is not opening', 'GEMS not working; Outlook works', 'Outlook is still accessible, but I cannot access GEMS. What similar fix is recorded?']:
            self.assertIsNone(support_route(text, 'it_application_access'))
            result = match_it_faq(text, 'it_application_access')
            self.assertEqual(result['match_mode'], 'cosine_similarity')
            self.assertTrue(result['faq_ids'])
