import asyncio
import unittest
from urllib.parse import urlparse, parse_qs, unquote
from rasa_sdk.executor import CollectingDispatcher
from actions.tickets import draft, preview, clear_ticket, ValidateTicketForm, ActionStartTicket, ActionCloseTicket


class TicketTests(unittest.TestCase):
    def test_verified_identity_and_exact_details(self):
        subject, body = draft('VPN fails with error 42.', 'Today', {'display_name': 'Nitin Naidu'})
        self.assertIn('VPN', subject)
        self.assertIn('VPN fails with error 42.', body)
        self.assertTrue(body.endswith('Regards,\nNitin Naidu'))
        self.assertNotIn('Ojas', body)

    def test_support_guard_separates_policy_and_issue(self):
        from actions.ticket_nlu import support_route
        self.assertEqual(support_route("my VPN won't connect anymore", 'nlu_fallback'), 'report_it_issue')
        self.assertEqual(support_route('what is the support email', 'contact_support'), 'ask_support_contact')
        self.assertEqual(support_route('Outlook failed please raise a ticket', 'report_it_issue'), 'contact_support')
        self.assertEqual(support_route('What is the policy when my laptop breaks?', 'report_it_issue'), 'nlu_fallback')
        self.assertIsNone(support_route('Show my leaves', 'ask_personal_leave_balance'))

    def test_access_request_has_no_failure_questions(self):
        class Tracker:
            def get_slot(self, name):
                return 'give organization access to emergys github IT ticket' if name == 'ticket_details' else None
        required = asyncio.run(ValidateTicketForm().required_slots(['ticket_details', 'ticket_error', 'ticket_started'], None, Tracker(), {}))
        self.assertEqual(required, ['ticket_details'])
        body = draft(Tracker().get_slot('ticket_details'), None, {'is_guest': True})[1]
        self.assertNotIn('Started:', body)
        self.assertIn('emergys github', body)

    def test_support_intent_reply_fills_requested_description(self):
        class Tracker:
            latest_message = {'text': 'give organization access to emergys github IT ticket', 'intent': {'name': 'contact_support'}}
            def get_slot(self, name):
                return 'ticket_details' if name == 'requested_slot' else None
        values = asyncio.run(ValidateTicketForm().extract_ticket_details(None, Tracker(), {}))
        self.assertEqual(values['ticket_details'], Tracker.latest_message['text'])

    def test_guest_signature(self):
        self.assertTrue(draft('Outlook fails', 'Not sure', {'is_guest': True})[1].endswith('Ojas Kulkarni'))

    def test_url_preserves_separate_fields(self):
        dispatcher = CollectingDispatcher()
        preview(dispatcher, 'VPN & login?', 'Dear Team,\nA+B fails.\nRegards,\nUser')
        data = dispatcher.messages[0]['custom']
        params = parse_qs(urlparse(data['outlook_url']).query)
        self.assertEqual(params['subject'], [data['subject']])
        self.assertEqual(params['body'], [data['body']])
        self.assertEqual(params['to'], ['helpdesk@emergys.com'])
        # Outlook decodes percent escapes without converting plus signs to spaces.
        query = urlparse(data['outlook_url']).query
        outlook_params = dict(pair.split('=', 1) for pair in query.split('&'))
        self.assertNotIn('+', query)
        self.assertIn('%20', query)
        self.assertEqual(unquote(outlook_params['subject']), data['subject'])
        self.assertEqual(unquote(outlook_params['body']), data['body'])

    def test_empty_initial_slot_is_silent(self):
        dispatcher = CollectingDispatcher()
        self.assertEqual(ValidateTicketForm().validate_ticket_details(None, dispatcher, None, {}), {'ticket_details': None})
        self.assertEqual(dispatcher.messages, [])

    def test_long_description_stays_editable(self):
        dispatcher = CollectingDispatcher()
        preview(dispatcher, 'Long issue', 'Details ' * 220)
        self.assertTrue(dispatcher.messages[0]['custom']['manual_edit_recommended'])

    def test_gibberish_reprompt(self):
        dispatcher = CollectingDispatcher()
        result = ValidateTicketForm().validate_ticket_details('!?', dispatcher, None, {})
        self.assertIsNone(result['ticket_details'])
        self.assertTrue(dispatcher.messages)

    def test_start_preserves_only_confirmed_issue(self):
        class Tracker:
            def __init__(self, pending): self.pending = pending
            def get_slot(self, name):
                return self.pending if name == 'ticket_pending' else 'Outlook fails'
        for pending in (True, False):
            events = asyncio.run(ActionStartTicket().run(CollectingDispatcher(), Tracker(pending), {}))
            details = next(e['value'] for e in events if e.get('name') == 'ticket_details')
            self.assertEqual(details, 'Outlook fails' if pending else None)

    def test_initial_details_and_time_skip_questions(self):
        class Tracker:
            latest_message = {'text': 'Outlook is not working since this morning, please raise a ticket', 'intent': {'name': 'contact_support'}}
            def get_slot(self, name): return None
        form = ValidateTicketForm()
        details = asyncio.run(form.extract_ticket_details(None, Tracker(), {}))['ticket_details']
        class WithDetails(Tracker):
            def get_slot(self, name): return details if name == 'ticket_details' else None
        started = asyncio.run(form.extract_ticket_started(None, WithDetails(), {}))['ticket_started']
        self.assertIn('this morning', started)

    def test_opened_is_silent_and_keeps_draft_editable(self):
        class Tracker:
            latest_message = {'intent': {'name': 'ticket_opened'}}
        dispatcher = CollectingDispatcher()
        events = asyncio.run(ActionCloseTicket().run(dispatcher, Tracker(), {}))
        self.assertFalse(any(message.get('text') for message in dispatcher.messages))
        self.assertFalse(any(event.get('name') in {'ticket_subject', 'ticket_body'} for event in events))

    def test_teams_card_has_mail_buttons_without_done(self):
        from teams_app.bot_package.bot_handler import _ticket_card
        card = _ticket_card({'to': 'helpdesk@emergys.com', 'subject': 'Access request', 'body': 'Dear Team,', 'outlook_url': 'https://outlook.office.com/mail/deeplink/compose'})
        self.assertEqual([action['title'] for action in card['actions']], ['Open in Outlook', 'Edit subject', 'Edit body', 'Cancel'])

    def test_close_clears_form(self):
        events = clear_ticket()
        self.assertTrue(any(e.get('event') == 'active_loop' and e.get('name') is None for e in events))
        self.assertTrue(any(e.get('name') == 'ticket_body' and e.get('value') is None for e in events))


if __name__ == '__main__': unittest.main()
