"""Template-based IT drafts. Never sends mail or reads employee records."""
import re
from urllib.parse import quote, urlencode
from rasa_sdk import Action, FormValidationAction
from rasa_sdk.events import SlotSet, ActiveLoop, FollowupAction
from .ticket_templates import draft

TICKET_SLOTS = ('ticket_details', 'ticket_error', 'ticket_started', 'ticket_subject', 'ticket_body', 'ticket_edit_value', 'ticket_edit_target')


def clear_ticket():
    return [ActiveLoop(None), SlotSet('ticket_pending', False), SlotSet('requested_slot', None), *[SlotSet(name, None) for name in TICKET_SLOTS]]


def _described_request(text):
    """Return true when a support command also contains an issue/request."""
    remainder = re.sub(
        r"\b(create|raise|open|prepare|draft|submit|an?|the|it|IT|support|service|technical|helpdesk|help desk|ticket|request|for|on|to)\b",
        " ", str(text or ""), flags=re.I,
    )
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]*", remainder)) >= 2


def preview(dispatcher, subject, body):
    url = 'https://outlook.office.com/mail/deeplink/compose?' + urlencode(
        {'to': 'helpdesk@emergys.com', 'subject': subject, 'body': body},
        quote_via=quote,
    )
    dispatcher.utter_message(text='Review your draft. Outlook uses its selected account as the sender. Opening a draft does not send it.', json_message={
        'type': 'ticket_draft', 'cc': '', 'to': 'helpdesk@emergys.com', 'subject': subject, 'body': body,
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
        current = tracker.latest_message.get('text', '')
        details = tracker.get_slot('ticket_details') if tracker.get_slot('ticket_pending') else (
            current if _described_request(current) else None
        )
        identity = tracker.latest_message.get('metadata') or {}
        subject, body = draft(details, None, identity)
        preview(dispatcher, subject, body)
        return [ActiveLoop(None), SlotSet('requested_slot', None), SlotSet('ticket_pending', False), SlotSet('ticket_details', details),
                SlotSet('ticket_subject', subject), SlotSet('ticket_body', body),
                *[SlotSet(name, None) for name in TICKET_SLOTS if name not in {'ticket_details','ticket_subject','ticket_body'}]]


class ValidateTicketForm(FormValidationAction):
    def name(self): return 'validate_ticket_form'
    async def required_slots(self, domain_slots, dispatcher, tracker, domain):
        required = ['ticket_details']
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
        if intent in {'contact_support', 'report_it_issue'} and _described_request(text):
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
            dispatcher.utter_message(text='Please describe the IT issue or request, including the device, impact, or replacement need. You can also cancel.')
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


class ActionInterruptTicket(Action):
    """Pause ticket collection when the user asks for another bot action."""
    def name(self): return 'action_interrupt_ticket'

    async def run(self, dispatcher, tracker, domain):
        intent = (tracker.latest_message.get('intent') or {}).get('name')
        followups = {
            'ask_personal_leave_balance': 'action_personal_leave_balance',
            'ask_upcoming_holidays': 'action_holiday_list',
            'ask_holiday_list': 'action_holiday_list',
            'help': 'utter_help',
            'greet': 'utter_greet',
            'goodbye': 'utter_goodbye',
            'ask_leave': 'action_answer_policy',
            'ask_remote_work': 'action_answer_policy',
            'ask_benefits': 'action_answer_policy',
            'ask_expenses': 'action_answer_policy',
            'ask_conduct': 'action_answer_policy',
            'ask_onboarding': 'action_answer_policy',
            'policy_bereavement': 'action_answer_policy',
            'policy_maternity': 'action_answer_policy',
            'policy_paternity': 'action_answer_policy',
            'policy_posh': 'action_answer_policy',
            'policy_general_leave': 'action_answer_policy',
            'policy_referral': 'action_answer_policy',
        }
        dispatcher.utter_message(json_message={
            'type': 'ticket_state', 'active': False, 'interrupted_by': intent,
        })
        events = clear_ticket()
        action = followups.get(intent)
        if action:
            events.append(FollowupAction(action))
        return events
