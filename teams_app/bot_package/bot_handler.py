"""Microsoft Teams activity handler backed by the Rasa REST channel."""
import os
from pathlib import Path
import sys
from urllib.parse import quote

import httpx
from botbuilder.core import ActivityHandler, CardFactory, MessageFactory, TurnContext

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from actions.policy_store import index_path


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
        sender = (
            getattr(turn_context.activity.from_property, "aad_object_id", None)
            or turn_context.activity.from_property.id
            or turn_context.activity.conversation.id
        )
        url = os.getenv("RASA_URL", "http://127.0.0.1:5005").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{url}/webhooks/rest/webhook",
                    json={"sender": sender, "message": question},
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
