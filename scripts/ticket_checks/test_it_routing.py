import json
from pathlib import Path
import unittest
import yaml
from actions.ticket_nlu import support_route

ROOT = Path(__file__).resolve().parents[2]
class ITRoutingTests(unittest.TestCase):
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

    def test_gems_failure_with_working_outlook_overrides_email_prediction(self):
        from actions.it_support import specific_it_answer
        entries = json.loads((ROOT/'data/faq_answers.json').read_text())['entries']
        for text in ['GEMS unavailable while Outlook worked', 'Outlook is working fine but GEMS is not opening', 'GEMS not working; Outlook works', 'Outlook is still accessible, but I cannot access GEMS. What similar fix is recorded?']:
            self.assertEqual(support_route(text, 'it_email_access'), 'it_application_access')
            answer = specific_it_answer(text, entries)
            self.assertIn('346506', answer)
            self.assertIn('DNS', answer)
            self.assertIn('not a confirmed diagnosis', answer)
        for text in ['GEMS and Outlook are not working', 'GEMS works but Outlook is unavailable', 'GEMS is displaying an access error', 'Outlook is not working but GEMS is unavailable']:
            self.assertIsNone(specific_it_answer(text, entries))
