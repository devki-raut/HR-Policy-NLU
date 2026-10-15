import json
from pathlib import Path
import unittest
from actions.it_support import match_it_faq, current_index
from scripts.import_it_report import report_rows

ROOT=Path(__file__).resolve().parents[2]

class XLSFAQTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries=current_index()[0]
        cls.cases=[e for e in cls.entries if e.get('answer_mode')=='it_incident']

    def test_every_report_incident_has_a_faq_and_source_link(self):
        _, rows=report_rows()
        self.assertEqual({r['Incident ID'] for r in rows},{e['incident_id'] for e in self.cases})
        self.assertEqual(len(self.cases),230)
        for e in self.cases:
            self.assertIn(e['incident_id'],e['answer'])
            self.assertIn('Report_15_Oct_26_092938.xls',e['answer'])
            result=match_it_faq('What is recorded for incident '+e['incident_id']+'?')
            self.assertEqual(result['answer'],e['answer'])
            if e['recorded_resolution']:
                self.assertIn(e['recorded_resolution'],result['answer'])

    def test_missing_solutions_and_closure_notes_do_not_claim_a_fix(self):
        missing=[e for e in self.cases if e['resolution_key']=='missing']
        self.assertEqual(len(missing),52)
        for e in missing:self.assertIn('No solution is recorded',e['answer'])
        for e in self.cases:
            if e['resolution_key']=='closure_only':self.assertIn('without documenting a technical fix',e['answer'])

    def test_curated_faq_evidence_references_existing_source_rows(self):
        ids={e['incident_id'] for e in self.cases}
        for e in self.entries:
            if e.get('answer_mode')=='it_resolution':
                self.assertTrue(e['incident_ids'])
                self.assertFalse(set(e['incident_ids'])-ids)
                self.assertIn('not a confirmed diagnosis',e['answer'])

    def test_unknown_incident_and_conflicting_generic_symptoms(self):
        self.assertEqual(match_it_faq('What happened in incident 999999?')['match_mode'],'unknown_incident')
        result=match_it_faq('Citrix is not working')
        self.assertEqual(result['match_mode'],'ambiguous')
        self.assertIsNone(result['resolution_key'])

    def test_no_hr_policy_entries_in_it_index(self):
        self.assertTrue(all(e['category'].startswith('it_') for e in self.entries))
