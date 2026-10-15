"""Microsoft Teams activity handler backed by the EmployeeAssist API."""
import logging
import os
from pathlib import Path
import sys
from urllib.parse import quote

import httpx
from botbuilder.core import ActivityHandler, CardFactory, MessageFactory, TurnContext
from botbuilder.core.teams import TeamsInfo
from botbuilder.schema import OAuthCard, CardAction, ActionTypes, InvokeResponse
from teams_app.bot_package.ticket_mail import MailError, owner as mail_owner, create_draft, update_draft, send_draft, cancel_draft, get_draft, outlook_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from actions.policy_store import index_path
from actions.identity_store import save_verified_identity
from teams_app.bot_package.broadcast import remember, handle as handle_broadcast
from teams_app.bot_package.broadcast import recipient_dialog
from teams_app.bot_package import mail_sso
from teams_app.web_paths import public_base_url
from teams_app.chat_service import rasa_chat, chat_sender
from fastapi import HTTPException


LOGGER = logging.getLogger(__name__)


def _message_action(title: str, text: str) -> dict:
    return {
        "type": "Action.Submit",
        "title": title,
        "data": {
            "msteams": {
                "type": "messageBack",
                "displayText": title,
                "text": text,
            }
        },
    }


IT_QUICK_QUESTIONS = [('Internet down', 'The office internet link is down'), ('Slow internet', 'Internet is very slow over Wi-Fi and LAN'), ('VPN / Citrix', 'Citrix access is denied'), ('Outlook / email', 'Outlook on the web is rejecting my login'), ('Teams / meeting room', 'Teams calls keep going on hold'), ('Application issue', 'GEMS application is not working')]


IT_PROMPT_QUESTIONS = IT_QUICK_QUESTIONS + [
    ("OneDrive files", "OneDrive files will not open"),
    ("Device not booting", "My system is not booting"),
    ("Zscaler error", "Zscaler connector shows a firewall error"),
    ("Account access", "AD self service shows permission denied"),
]
HR_PROMPT_QUESTIONS = [
    ("My leave balance", "Show my leaves"),
    ("Upcoming holidays", "Show the next 5 holidays"),
    ("Holiday calendar", "Show the full holiday list for this year"),
    ("Casual leave", "How many casual leaves are allowed?"),
    ("Maternity leave", "What is the maternity leave entitlement?"),
    ("POSH complaint", "How do I file a POSH complaint?"),
]
REPORT_PROMPT_QUESTIONS = [
    ("GEMS vs Outlook", "GEMS unavailable while Outlook worked"),
    ("Citrix device change", "Citrix stopped working after I changed my laptop"),
    ("Citrix location check", "Citrix is detecting the wrong country"),
    ("Microsoft 365 group", "How can I request a Microsoft 365 group?"),
    ("Recorded incident", "What happened in incident 346506?"),
]
PROMPT_MENU_COMMANDS = {"prompt suggestions", "suggestions", "show suggestions", "show prompt suggestions"}
MENU_QUESTION_ALIASES = {
    "it network help": "The office internet link is down",
    "outlook login issue": "Outlook on the web is rejecting my login",
}


def _question_list_card(questions, introduction):
    return {
        "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": introduction, "wrap": True},
            *[{"type": "ActionSet", "actions": [_message_action(title, question) for title, question in questions[start:start + 3]]}
              for start in range(0, len(questions), 3)],
        ],
    }


def _prompt_suggestions_card():
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "Prompt Suggestions from EmployeeAssist", "weight": "Bolder", "size": "Medium", "wrap": True},
            {"type": "TextBlock", "text": "Choose a topic, then select a question to send it. You can also type your own question.", "wrap": True},
        ],
        "actions": [
            {"type": "Action.ShowCard", "title": "HR questions", "card": _question_list_card(HR_PROMPT_QUESTIONS, "Leave, holidays and HR policies")},
            {"type": "Action.ShowCard", "title": "IT support", "card": _question_list_card(IT_PROMPT_QUESTIONS, "Describe an IT issue for guidance. Use Create IT ticket to prepare a helpdesk request.")},
            {"type": "Action.ShowCard", "title": "Recorded fixes", "card": _question_list_card(REPORT_PROMPT_QUESTIONS, "These questions retrieve historical incidents, not a confirmed diagnosis of your current issue.")},
            _message_action("Create IT ticket", "Create IT ticket"),
        ],
    }


def _it_questions_card() -> dict:
    return {
        "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "Choose an IT issue for guidance, or use Create IT ticket to prepare a helpdesk request.", "wrap": True},
            *[{"type": "ActionSet", "actions": [_message_action(title, question) for title, question in IT_QUICK_QUESTIONS[start:start + 3]]} for start in (0, 3)],
        ],
    }


def _welcome_card(display_name: str | None = None) -> dict:
    greeting = f"Hi {display_name}!" if display_name else "Hi!"
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": greeting, "size": "Large", "weight": "Bolder"},
            {
                "type": "TextBlock",
                "text": (
                    "I’m EmployeeAssist. I can answer HR policy questions with sources, "
                    "show your live leave balance, list upcoming holidays, and display "
                    "the full annual holiday calendar, help with IT issues, and prepare IT helpdesk emails."
                ),
                "wrap": True,
            },
            {"type": "TextBlock", "text": "Choose a quick action:", "weight": "Bolder", "wrap": True},
            {"type": "ActionSet", "actions": [{"type": "Action.ShowCard", "title": "IT support questions", "card": _it_questions_card()}]},
            {"type": "ActionSet", "actions": [_message_action("Prompt suggestions", "Prompt suggestions")]},
        ],
        "actions": [
            _message_action("Help", "help"),
            _message_action("Create IT ticket", "Create IT ticket"),
            _message_action("My leave balance", "Show my leaves"),
            _message_action("Next holidays", "Show the next 5 holidays"),
            _message_action("Holiday calendar", "Show the full holiday list for this year"),
            _message_action("Leave policy", "How many casual leaves are allowed?"),
            _message_action("POSH", "How do I file a POSH complaint?"),
        ],
    }


def _leave_balance_card(metadata: dict) -> dict:
    facts = []
    for item in metadata.get("balances") or []:
        balance = item.get("available_balance")
        if balance is None:
            continue
        name = str(item.get("leave_name") or "Leave")
        for prefix in ("ESOL_ ", "Emergys_"):
            if name.startswith(prefix):
                name = name[len(prefix):]
        facts.append({"title": name, "value": f"{balance} {item.get('unit') or 'days'}"})
    body = [
        {"type": "TextBlock", "text": "Your current leave balances", "size": "Medium", "weight": "Bolder"},
        {"type": "FactSet", "facts": facts},
    ]
    if metadata.get("employee_mapping_fallback"):
        body.append({
            "type": "TextBlock",
            "text": "Your email was not found in the employee directory, so the configured default employee profile was used.",
            "wrap": True,
            "isSubtle": True,
        })
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": [_message_action("Refresh", "Show my leaves")],
    }


def _holiday_card(metadata: dict) -> dict:
    holidays = metadata.get("holidays") or []
    requested = metadata.get("requested_count")
    title = "Holiday calendar"
    if metadata.get("mode") == "upcoming":
        title = f"Next {len(holidays)} holidays"
        if requested and len(holidays) < requested:
            title = f"Last {len(holidays)} holidays of {metadata.get('year')}"
    rows = [{
        "type": "ColumnSet",
        "separator": True,
        "columns": [
            {"type": "Column", "width": "auto", "items": [{"type": "TextBlock", "text": "Date", "weight": "Bolder"}]},
            {"type": "Column", "width": "auto", "items": [{"type": "TextBlock", "text": "Day", "weight": "Bolder"}]},
            {"type": "Column", "width": "stretch", "items": [{"type": "TextBlock", "text": "Holiday", "weight": "Bolder"}]},
        ],
    }]
    for item in holidays:
        rows.append({
            "type": "ColumnSet",
            "separator": True,
            "columns": [
                {"type": "Column", "width": "auto", "items": [{"type": "TextBlock", "text": str(item.get("date") or ""), "wrap": True}]},
                {"type": "Column", "width": "auto", "items": [{"type": "TextBlock", "text": str(item.get("day") or ""), "wrap": True}]},
                {"type": "Column", "width": "stretch", "items": [{"type": "TextBlock", "text": str(item.get("name") or ""), "wrap": True}]},
            ],
        })
    body = [
        {"type": "TextBlock", "text": title, "size": "Medium", "weight": "Bolder", "wrap": True},
        *rows,
    ]
    if metadata.get("employee_mapping_fallback"):
        body.append({
            "type": "TextBlock",
            "text": "Your email was not found in the employee directory, so the configured default employee profile was used.",
            "wrap": True,
            "isSubtle": True,
        })
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": [
            _message_action("Next 5", "Show the next 5 holidays"),
            _message_action("Full year", "Show the full holiday list for this year"),
        ],
    }


def _ticket_card(draft, request_id=None):
    data = {'request_id': request_id}
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": "IT support request", "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": "Describe the issue in the mail below. Include the application/device, exact error and start time if known.", "wrap": True},
            {"type": "Input.Text", "id": "ticket_to", "label": "To", "value": draft.get('to','helpdesk@emergys.com'), "maxLength": 2000, "isRequired": True, "errorMessage": "Enter at least one recipient email address."},
            {"type": "Input.Text", "id": "ticket_cc", "label": "CC (optional)", "value": draft.get('cc',''), "maxLength": 2000, "placeholder": "name@example.com; another@example.com"},
            {"type": "Input.Text", "id": "ticket_subject", "label": "Subject", "value": draft.get('subject',''), "placeholder": "Internet connectivity issue", "maxLength": 180, "isRequired": True, "errorMessage": "Enter a subject."},
            {"type": "Input.Text", "id": "ticket_body", "label": "Body", "value": draft.get('body',''), "placeholder": "Describe the issue or access request, affected device/application, exact error, impact and start time.", "isMultiline": True, "maxLength": 6000, "isRequired": True, "errorMessage": "Describe your issue or request."},
            {"type": "TextBlock", "text": "Raise a request sends these fields from your Teams Microsoft mailbox using SSO. Open in Outlook prepares these same edited fields for manual sending. Enter in the body adds a new line; select Raise a request to send.", "wrap": True},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Raise a request", "data": {**data,"command":"ticket_send"}},
            {"type": "Action.Submit", "title": "Open in Outlook", "data": {**data,"command":"ticket_open_outlook"}},
            {"type": "Action.Submit", "title": "Cancel", "associatedInputs":"none", "data": {**data,"command":"ticket_cancel_send"}},
        ],
    }


async def _teams_identity(turn_context: TurnContext) -> dict:
    """Return identity attributes verified by Teams for the message sender.

    Member lookup can be unavailable in local Bot Framework tests or restricted
    tenants. In that case the activity's authenticated sender identifiers remain
    usable, while personal Darwinbox requests fail closed if no mapping exists.
    """
    activity = turn_context.activity
    account = activity.from_property
    member = None
    member_id = getattr(account, "id", None)

    if member_id:
        try:
            member = await TeamsInfo.get_member(turn_context, member_id)
        except Exception as exc:  # SDK exposes multiple connector error types.
            LOGGER.warning("Unable to retrieve Teams member profile: %s", exc)

    identity = member or account
    aad_object_id = (
        getattr(identity, "aad_object_id", None)
        or getattr(account, "aad_object_id", None)
    )
    email = getattr(identity, "email", None)
    user_principal_name = getattr(identity, "user_principal_name", None)
    display_name = getattr(identity, "name", None) or getattr(account, "name", None)

    channel_data = activity.channel_data or {}
    tenant = channel_data.get("tenant") if isinstance(channel_data, dict) else None
    tenant_id = tenant.get("id") if isinstance(tenant, dict) else None

    result = {
        "aad_object_id": aad_object_id,
        "email": email or user_principal_name,
        "user_principal_name": user_principal_name,
        "display_name": display_name,
        "tenant_id": tenant_id,
        "teams_user_id": member_id,
        "profile_verified": member is not None,
    }
    if aad_object_id and tenant_id:
        save_verified_identity(result, "teams_bot_member")
    return result


def _evidence_card(evidence):
    actions = []
    for item in evidence:
        chunk = item.get("chunk") or {}
        title = f"{item.get('position', len(actions) + 1)}. {chunk.get('section') or 'Policy excerpt'}"
        facts = [
            {"title": "Source", "value": str(chunk.get("source") or "Unspecified")},
            {"title": "Page", "value": str(chunk.get("page") or "Unspecified")},
            {"title": "Chunk", "value": str(chunk.get("id") or "Unspecified")},
        ]
        public_url = public_base_url()
        source = chunk.get("source")
        page = chunk.get("page")
        card_actions = []
        if source:
            pdf_url = f"{public_url}/policies/pdfs/{quote(str(source), safe='')}"
            if page:
                pdf_url += f"#page={page}"
            card_actions.append({
                "type": "Action.OpenUrl",
                "title": f"Open PDF{f' at page {page}' if page else ''}",
                "url": pdf_url,
            })
        actions.append({
            "type": "Action.ShowCard",
            "title": title,
            "card": {
                "type": "AdaptiveCard",
                "body": [
                    {"type": "FactSet", "facts": facts},
                    {
                        "type": "TextBlock",
                        "text": str(chunk.get("text") or "No excerpt available."),
                        "wrap": True,
                    },
                ],
                "actions": card_actions,
            },
        })
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [{
            "type": "TextBlock",
            "text": f"Source excerpts ({len(evidence)})",
            "weight": "Bolder",
            "size": "Medium",
        }],
        "actions": actions,
    }


class TeamsBot(ActivityHandler):
    async def on_turn(self, turn_context):
        if turn_context.activity.channel_id == "msteams":
            identity = await _teams_identity(turn_context)
            remember(turn_context, identity)
            if turn_context.activity.type == "message":
                text = (turn_context.activity.text or "").strip()
                if await handle_broadcast(turn_context, identity, text):
                    return
        await super().on_turn(turn_context)

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        recipient_id = getattr(turn_context.activity.recipient, "id", None)
        for member in members_added:
            if getattr(member, "id", None) == recipient_id:
                continue
            identity = await _teams_identity(turn_context)
            activity = MessageFactory.attachment(
                CardFactory.adaptive_card(_welcome_card(identity.get("display_name")))
            )
            await turn_context.send_activity(activity)

    async def on_token_response_event(self, turn_context):
        identity = await _teams_identity(turn_context)
        await mail_sso.token_event(turn_context, identity)

    async def on_sign_in_invoke(self, turn_context):
        identity = await _teams_identity(turn_context)
        values = turn_context.activity.value or {}
        await mail_sso.token_event(turn_context, identity, values.get('state'))

    async def on_invoke_activity(self, turn_context):
        if turn_context.activity.name in {'task/fetch', 'task/submit'}:
            identity = await _teams_identity(turn_context)
            return await recipient_dialog(turn_context, identity)
        if turn_context.activity.name == 'signin/tokenExchange':
            identity = await _teams_identity(turn_context)
            return await mail_sso.token_exchange(turn_context, identity)
        if turn_context.activity.name == 'signin/failure':
            await turn_context.send_activity('Teams SSO could not complete. Your request is retained. Ask the administrator to check the app installation, Token Exchange URL, consent and SSO manifest settings.')
            return InvokeResponse(status=200)
        return await super().on_invoke_activity(turn_context)

    async def _raise_ticket(self, turn_context, values):
        try:
            identity = await _teams_identity(turn_context)
            identity_owner = mail_owner(identity, turn_context.activity.conversation.id)
            key = str(values.get('request_id') or '')
            update_draft(key, identity_owner, values.get('ticket_subject'), values.get('ticket_body'), values.get('ticket_to'), values.get('ticket_cc'))
            if values.get('command') == 'ticket_open_outlook':
                row = get_draft(key, identity_owner)
                card = {"type":"AdaptiveCard", "version":"1.4", "body":[{"type":"TextBlock","text":"Your edited To, CC, subject and body are ready. Open Outlook to review and send manually.","wrap":True}],
                        "actions":[{"type":"Action.OpenUrl","title":"Open Outlook draft","url":outlook_url(row)}]}
                await turn_context.send_activity(MessageFactory.attachment(CardFactory.adaptive_card(card)))
                return
            await mail_sso.begin(turn_context, identity, key)
        except MailError as exc:
            await turn_context.send_activity(str(exc))
        except Exception:
            LOGGER.error('Teams request could not complete')
            await turn_context.send_activity('Could not complete the request. Your draft is retained. Check Sent Items before retrying.')

    async def on_message_activity(self, turn_context: TurnContext):
        values = turn_context.activity.value
        if isinstance(values, dict) and values.get('command') == 'ticket_cancel_send':
            try:
                identity = await _teams_identity(turn_context)
                cancel_draft(str(values.get('request_id') or ''), mail_owner(identity, turn_context.activity.conversation.id))
                await turn_context.send_activity('IT draft cancelled.')
            except MailError as exc:
                await turn_context.send_activity(str(exc))
            return
        if isinstance(values, dict) and values.get('command') in {'ticket_send', 'ticket_open_outlook'}:
            await self._raise_ticket(turn_context, values)
            return
        question = (turn_context.activity.text or "").strip()
        if not question and isinstance(turn_context.activity.value, dict):
            question = str(turn_context.activity.value.get("command") or "").strip()
        if not question:
            await turn_context.send_activity("Please enter an HR policy question.")
            return
        identity = await _teams_identity(turn_context)
        identity["channel"] = "msteams"
        if question.casefold() in PROMPT_MENU_COMMANDS:
            await turn_context.send_activity(MessageFactory.attachment(
                CardFactory.adaptive_card(_prompt_suggestions_card())
            ))
            return
        question = MENU_QUESTION_ALIASES.get(question.casefold(), question)
        if question.casefold() in {"hi", "hello", "hey", "help", "what can you do"}:
            activity = MessageFactory.attachment(
                CardFactory.adaptive_card(_welcome_card(identity.get("display_name")))
            )
            await turn_context.send_activity(activity)
            return
        sender = (
            identity.get("aad_object_id")
            or identity.get("teams_user_id")
            or turn_context.activity.conversation.id
        )
        # Bot Framework has authenticated this activity. Call the shared
        # service directly; verified Teams identity never crosses a public API
        # as caller-controlled metadata, and no browser sign-in token is needed.
        if not identity.get('tenant_id') or not identity.get('aad_object_id'):
            await turn_context.send_activity('I could not verify your Teams account. Please reopen this bot in a personal Teams chat.')
            return
        sender = chat_sender(identity, str(sender))
        try:
            payload = await rasa_chat(sender, question, identity)
            messages = payload.get("messages") if isinstance(payload, dict) else payload
            if not isinstance(messages, list):
                raise ValueError("Invalid Rasa response")
            texts = [
                item["text"] for item in messages
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            evidence = [
                item["custom"] for item in messages
                if isinstance(item, dict)
                and isinstance(item.get("custom"), dict)
                and item["custom"].get("type") == "policy_evidence"
            ]
            leave_metadata = next((
                item["custom"] for item in messages
                if isinstance(item, dict)
                and isinstance(item.get("custom"), dict)
                and item["custom"].get("type") == "leave_balance_metadata"
            ), None)
            holiday_metadata = next((
                item["custom"] for item in messages
                if isinstance(item, dict)
                and isinstance(item.get("custom"), dict)
                and item["custom"].get("type") == "holiday_list_metadata"
            ), None)
            ticket = next((item.get('custom') for item in messages if isinstance(item.get('custom'), dict) and item['custom'].get('type') == 'ticket_draft'), None)
            if ticket:
                await turn_context.send_activity(MessageFactory.attachment(CardFactory.adaptive_card(_ticket_card(ticket, create_draft(mail_owner(identity, turn_context.activity.conversation.id), ticket['subject'], ticket['body'], ticket.get('to','helpdesk@emergys.com'), ticket.get('cc',''))))))
                return
            choices = [choice for item in messages for choice in item.get('buttons', [])]
            if choices:
                card = {"type": "AdaptiveCard", "version": "1.4", "body": [{"type": "TextBlock", "text": text, "wrap": True} for text in texts], "actions": [_message_action(choice['title'], choice['payload']) for choice in choices]}
                await turn_context.send_activity(MessageFactory.attachment(CardFactory.adaptive_card(card)))
                return
            if not texts and any(item.get('custom', {}).get('type') == 'ticket_state' for item in messages if isinstance(item.get('custom'), dict)):
                return
            if not texts and not evidence:
                await turn_context.send_activity(
                    "I couldn't find a policy answer. Please rephrase your question."
                )
                return
            if leave_metadata and leave_metadata.get("status") == "answered":
                await turn_context.send_activity(MessageFactory.attachment(
                    CardFactory.adaptive_card(_leave_balance_card(leave_metadata))
                ))
            elif holiday_metadata and holiday_metadata.get("status") == "answered":
                await turn_context.send_activity(MessageFactory.attachment(
                    CardFactory.adaptive_card(_holiday_card(holiday_metadata))
                ))
            else:
                for text in texts:
                    await turn_context.send_activity(text)
            if evidence:
                activity = MessageFactory.attachment(
                    CardFactory.adaptive_card(_evidence_card(evidence))
                )
                await turn_context.send_activity(activity)
        except (httpx.HTTPError, HTTPException, ValueError, TypeError):
            await turn_context.send_activity(
                "The HR policy service is temporarily unavailable. Please try again."
            )


BOT = TeamsBot()
