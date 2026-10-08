"""Microsoft Teams activity handler backed by the Rasa REST channel."""
import logging
import os
from pathlib import Path
import sys
from urllib.parse import quote

import httpx
from botbuilder.core import ActivityHandler, CardFactory, MessageFactory, TurnContext
from botbuilder.core.teams import TeamsInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from actions.policy_store import index_path
from actions.identity_store import save_verified_identity


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
                    "the full annual holiday calendar."
                ),
                "wrap": True,
            },
            {"type": "TextBlock", "text": "Choose a quick action:", "weight": "Bolder", "wrap": True},
        ],
        "actions": [
            _message_action("Help", "help"),
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
        public_url = (
            os.getenv("APP_PUBLIC_URL")
            or os.getenv("PUBLIC_BASE_URL")
            or "http://localhost:8610"
        ).rstrip("/")
        source = chunk.get("source")
        page = chunk.get("page")
        card_actions = []
        if source:
            pdf_url = f"{public_url}/policy-documents/pdfs/{quote(str(source), safe='')}"
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

    async def on_message_activity(self, turn_context: TurnContext):
        question = (turn_context.activity.text or "").strip()
        if not question:
            await turn_context.send_activity("Please enter an HR policy question.")
            return
        identity = await _teams_identity(turn_context)
        identity["channel"] = "msteams"
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
        url = os.getenv("RASA_URL", "http://127.0.0.1:5005").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{url}/webhooks/rest/webhook",
                    json={
                        "sender": sender,
                        "message": question,
                        "metadata": {**identity, "channel": "msteams"},
                    },
                )
                response.raise_for_status()
            messages = response.json()
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
        except (httpx.HTTPError, ValueError, TypeError):
            await turn_context.send_activity(
                "The HR policy service is temporarily unavailable. Please try again."
            )


BOT = TeamsBot()
