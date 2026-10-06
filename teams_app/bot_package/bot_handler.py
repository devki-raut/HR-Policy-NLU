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


LOGGER = logging.getLogger(__name__)


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

    return {
        "aad_object_id": aad_object_id,
        "email": email or user_principal_name,
        "user_principal_name": user_principal_name,
        "display_name": display_name,
        "tenant_id": tenant_id,
        "teams_user_id": member_id,
    }


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
    async def on_message_activity(self, turn_context: TurnContext):
        question = (turn_context.activity.text or "").strip()
        if not question:
            await turn_context.send_activity("Please enter an HR policy question.")
            return
        identity = await _teams_identity(turn_context)
        identity["channel"] = "msteams"
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
            if not texts and not evidence:
                await turn_context.send_activity(
                    "I couldn't find a policy answer. Please rephrase your question."
                )
                return
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
