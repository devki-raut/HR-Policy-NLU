"""Template-based IT drafts. Never sends mail or reads employee records."""
import re
from urllib.parse import quote, urlencode
from rasa_sdk import Action, FormValidationAction
from rasa_sdk.events import SlotSet, ActiveLoop, FollowupAction

TICKET_SLOTS = ('ticket_details', 'ticket_error', 'ticket_started', 'ticket_subject', 'ticket_body', 'ticket_edit_value', 'ticket_edit_target')


def clear_ticket():
    return [ActiveLoop(None), SlotSet('ticket_pending', False), SlotSet('requested_slot', None), *[SlotSet(name, None) for name in TICKET_SLOTS]]


def draft(details, started, identity, error=None):
    name = identity.get('display_name') or ('Ojas Kulkarni' if identity.get('is_guest') else identity.get('email') or 'Employee')
    name = ' '.join(str(name).split())
    subject = 'IT support required — ' + ' '.join(details.split())[:90]
    error_line = f'\n\nError or behaviour: {error}' if error else ''
    started_line = f'\n\nStarted: {started}' if started else ''
    body = f'Dear Team,\n\n{details}{error_line}{started_line}\n\nPlease help me resolve this issue.\n\nRegards,\n{name}'
    return subject, body


def preview(dispatcher, subject, body):
    url = 'https://outlook.office.com/mail/deeplink/compose?' + urlencode(
        {'to': 'helpdesk@emergys.com', 'subject': subject, 'body': body},
        quote_via=quote,
    )
    dispatcher.utter_message(text='Review your draft. Outlook uses its selected account as the sender. Opening a draft does not send it.', json_message={
        'type': 'ticket_draft', 'to': 'helpdesk@emergys.com', 'subject': subject, 'body': body,
        'outlook_url': url, 'manual_edit_recommended': len(body.split()) > 200,
    })


class ActionOfferTicket(Action):
    def name(self): return 'action_offer_ticket'
    async def run(self, dispatcher, tracker, domain):
        text = tracker.latest_message.get('text', '')
        dispatcher.utter_message(json_message={'type': 'ticket_state', 'active': True})
        dispatcher.utter_message(text='Would you like to prepare an email to IT helpdesk?', buttons=[{'title': 'Prepare email', 'payload': 'Create IT ticket'}, {'title': 'Cancel', 'payload': 'cancel'}])
        return [SlotSet('ticket_pending', True), SlotSet('ticket_details', text), SlotSet('ticket_started', None)]


class ActionStartTicket(Action):
    def name(self): return 'action_start_ticket'
    async def run(self, dispatcher, tracker, domain):
        dispatcher.utter_message(json_message={'type': 'ticket_state', 'active': True})
        details = tracker.get_slot('ticket_details') if tracker.get_slot('ticket_pending') else None
        return [SlotSet('ticket_pending', False), SlotSet('ticket_details', details), *[SlotSet(name, None) for name in TICKET_SLOTS if name != 'ticket_details']]


class ValidateTicketForm(FormValidationAction):
    def name(self): return 'validate_ticket_form'
    async def required_slots(self, domain_slots, dispatcher, tracker, domain):
        details = tracker.get_slot('ticket_details') or ''
        request = re.search(r'\b(access|permission|license|install|enable|grant|provision)\b', details, re.I)
        failure = re.search(r"cannot|can't|cant|unable|not working|failed|error|locked", details, re.I)
        required = ['ticket_details'] if request and not failure else domain_slots
        if dispatcher is not None:
            next_slot = next((slot for slot in required if not tracker.get_slot(slot)), None)
            dispatcher.utter_message(json_message={'type': 'ticket_state', 'active': True, 'requested_slot': next_slot})
        return required

    async def extract_ticket_details(self, dispatcher, tracker, domain):
        # Preserve issue text supplied in an explicit request instead of asking again.
        if tracker.get_slot('ticket_details'):
            return {}
        text = tracker.latest_message.get('text', '')
        intent = (tracker.latest_message.get('intent') or {}).get('name')
        if intent == 'contact_support' and re.search(r"not working|cannot|can't|cant|failed|fails|error|broken|locked|crash|access|permission|license|install|enable|grant|provision", text, re.I):
            return {'ticket_details': text}
        if tracker.get_slot('requested_slot') == 'ticket_details' and intent not in {'ticket_cancel', 'ticket_opened'}:
            return {'ticket_details': text}
        return {}

    async def extract_ticket_error(self, dispatcher, tracker, domain):
        if tracker.get_slot('ticket_error'):
            return {}
        details = tracker.get_slot('ticket_details') or ''
        match = re.search(r"(?:error(?: message)?|says|shows)\s*[:—-]?\s+([^.!?]+)", details, re.I)
        if match:
            return {'ticket_error': match.group(1)}
        if tracker.get_slot('requested_slot') == 'ticket_error':
            return {'ticket_error': tracker.latest_message.get('text', '')}
        return {}

    async def extract_ticket_started(self, dispatcher, tracker, domain):
        if tracker.get_slot('ticket_started'):
            return {}
        details = tracker.get_slot('ticket_details') or ''
        match = re.search(r"(?:since|started|from)\s+([^.!?]+)|\b(this morning|today|yesterday)\b", details, re.I)
        if match:
            return {'ticket_started': match.group(1) or match.group(2)}
        if tracker.get_slot('requested_slot') == 'ticket_started':
            return {'ticket_started': tracker.latest_message.get('text', '')}
        return {}

    def validate_ticket_details(self, slot_value, dispatcher, tracker, domain):
        if slot_value is None:
            return {'ticket_details': None}
        value = str(slot_value).strip()
        if len(re.findall(r'[A-Za-z]{2,}', value)) < 2 or value.startswith('/'):
            dispatcher.utter_message(text='Please describe what is failing, for example “Outlook says account locked”. You can also cancel.')
            return {'ticket_details': None}
        return {'ticket_details': value[:4000]}


class ActionDraftTicket(Action):
    def name(self): return 'action_draft_ticket'
    async def run(self, dispatcher, tracker, domain):
        details = tracker.get_slot('ticket_details') or ''
        identity = tracker.latest_message.get('metadata') or {}
        subject, body = draft(details, tracker.get_slot('ticket_started'), identity, tracker.get_slot('ticket_error'))
        preview(dispatcher, subject, body)
        return [SlotSet('ticket_subject', subject), SlotSet('ticket_body', body)]


class ActionEditTicket(Action):
    def name(self): return 'action_edit_ticket'
    async def run(self, dispatcher, tracker, domain):
        if not tracker.get_slot('ticket_body'):
            dispatcher.utter_message(text='Start with “Create IT ticket” to prepare a draft.')
            return [*clear_ticket(), FollowupAction('action_listen')]
        dispatcher.utter_message(json_message={'type': 'ticket_state', 'active': True, 'requested_slot': 'ticket_edit_value'})
        target = 'ticket_subject' if tracker.latest_message['intent']['name'] == 'ticket_edit_subject' else 'ticket_body'
        return [SlotSet('ticket_edit_target', target), SlotSet('ticket_edit_value', None)]


class ActionSaveTicketEdit(Action):
    def name(self): return 'action_save_ticket_edit'
    async def run(self, dispatcher, tracker, domain):
        target = tracker.get_slot('ticket_edit_target')
        if target not in {'ticket_subject', 'ticket_body'}:
            dispatcher.utter_message(text='No draft is available. Say “Create IT ticket” to begin.')
            return clear_ticket()
        value = str(tracker.get_slot('ticket_edit_value') or '').strip()
        subject = value.replace('\n', ' ')[:180] if target == 'ticket_subject' else tracker.get_slot('ticket_subject')
        body = value[:6000] if target == 'ticket_body' else tracker.get_slot('ticket_body')
        preview(dispatcher, subject, body)
        return [SlotSet(target, subject if target == 'ticket_subject' else body), SlotSet('ticket_edit_value', None)]


class ActionCloseTicket(Action):
    def name(self): return 'action_close_ticket'
    async def run(self, dispatcher, tracker, domain):
        opened = tracker.latest_message['intent']['name'] == 'ticket_opened'
        dispatcher.utter_message(json_message={'type': 'ticket_state', 'active': False})
        if opened:
            return [ActiveLoop(None), SlotSet('requested_slot', None), SlotSet('ticket_pending', False)]
        dispatcher.utter_message(text='IT draft cancelled. You can ask another question.')
        return clear_ticket()
