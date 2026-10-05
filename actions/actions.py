import os
from typing import Any, Dict, List, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

from .conversation_log import append_conversation
from .darwinbox import (
    DarwinboxAPIError,
    DarwinboxConfigurationError,
    extract_employee_code,
    fetch_leave_balance,
    remember_employee_code,
    resolve_employee,
)
from .faq_matcher import find_faq, retrieve_policy_chunks

FAQ_THRESHOLD = float(os.getenv("FAQ_EMBEDDING_THRESHOLD", "0.75"))


class ActionAnswerPolicy(Action):
    def name(self) -> Text:
        return "action_answer_policy"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        question = tracker.latest_message.get("text", "").strip()
        intent_data = tracker.latest_message.get("intent", {})
        routing_intent = intent_data.get("name")
        routing_confidence = intent_data.get("confidence")
        channel = (
            tracker.get_latest_input_channel()
            if hasattr(tracker, "get_latest_input_channel")
            else tracker.latest_message.get("input_channel")
        )

        result = find_faq(
            question=question,
            routing_intent=routing_intent,
            threshold=FAQ_THRESHOLD,
        )

        if result and result.get("answer"):
            answer = result["answer"]
            source = result.get("source")
            if source:
                answer += f"\n\nSource: {source}"
            dispatcher.utter_message(text=answer)

            chunks = retrieve_policy_chunks(question, result, limit=5)
            context_ids = [chunk.get("id") for chunk in chunks if chunk.get("id")]
            event_id = append_conversation({
                "user_id": tracker.sender_id,
                "is_evaluation": str(tracker.sender_id).startswith("final-eval-"),
                "channel": channel,
                "question": question,
                "routing_intent": routing_intent,
                "routing_confidence": routing_confidence,
                "detailed_intent": result.get("intent"),
                "faq_id": result.get("entry_id"),
                "matched_question": result.get("matched_question"),
                "faq_score": round(float(result.get("score", 0.0)), 6),
                "answer_mode": result.get("answer_mode"),
                "status": "answered",
                "output": answer,
                "final_answer": answer,
                "source": source,
                "context_ids": context_ids,
                "contexts": [
                    {
                        "id": chunk.get("id"),
                        "source": chunk.get("source"),
                        "page": chunk.get("page"),
                        "section": chunk.get("section"),
                        "mapped": bool(chunk.get("mapped")),
                        "retrieval_score": chunk.get("retrieval_score"),
                    }
                    for chunk in chunks
                ],
            })
            dispatcher.utter_message(json_message={
                "type": "policy_answer_metadata",
                "event_id": event_id,
                "routing_intent": routing_intent,
                "routing_confidence": routing_confidence,
                "detailed_intent": result.get("intent"),
                "faq_id": result.get("entry_id"),
                "matched_question": result.get("matched_question"),
                "score": round(float(result.get("score", 0.0)), 6),
                "answer_mode": result.get("answer_mode"),
                "context_ids": context_ids,
            })
            for position, chunk in enumerate(chunks, start=1):
                dispatcher.utter_message(
                    json_message={
                        "type": "policy_evidence",
                        "position": position,
                        "total": len(chunks),
                        "label": (
                            "Mapped evidence"
                            if chunk.get("mapped")
                            else "Related chunk"
                        ),
                        "chunk": {
                            "id": chunk.get("id"),
                            "source": chunk.get("source"),
                            "page": chunk.get("page"),
                            "section": chunk.get("section"),
                            "text": chunk.get("text"),
                            "mapped": bool(chunk.get("mapped")),
                        },
                    }
                )
            return []

        # Replace this block with your existing PDF RAG function.
        answer = (
            "I couldn't find a confident FAQ match. "
            "I would normally search the policy documents here."
        )
        dispatcher.utter_message(text=answer)
        event_id = append_conversation({
            "user_id": tracker.sender_id,
            "channel": channel,
            "question": question,
            "routing_intent": routing_intent,
            "routing_confidence": routing_confidence,
            "detailed_intent": None,
            "faq_id": None,
            "matched_question": None,
            "faq_score": None,
            "answer_mode": None,
            "status": "no_confident_match",
            "output": answer,
            "final_answer": answer,
            "source": None,
            "context_ids": [],
            "contexts": [],
        })
        dispatcher.utter_message(json_message={
            "type": "policy_answer_metadata",
            "event_id": event_id,
            "routing_intent": routing_intent,
            "routing_confidence": routing_confidence,
            "status": "no_confident_match",
            "context_ids": [],
        })
        return []


async def _personal_balance_answer(employee_id):
    balances, _raw = await fetch_leave_balance(employee_id)
    lines = [f"- {item.leave_type}: {item.balance} {item.unit}" for item in balances]
    return "Your current Darwinbox leave balance is:\n" + "\n".join(lines), balances


def _personal_log(dispatcher, tracker, question, answer, status, integration_status, balances):
    intent_data = tracker.latest_message.get("intent", {})
    event_id = append_conversation({
        "user_id": tracker.sender_id,
        "channel": tracker.get_latest_input_channel(),
        "question": question,
        "routing_intent": intent_data.get("name"),
        "routing_confidence": intent_data.get("confidence"),
        "detailed_intent": "personal_leave_balance",
        "faq_id": None,
        "faq_score": None,
        "answer_mode": "darwinbox_api",
        "status": status,
        "integration": "darwinbox",
        "integration_status": integration_status,
        "output": answer,
        "final_answer": answer,
        "source": "Darwinbox Leave Management API" if balances else None,
        "context_ids": [],
        "contexts": [],
    })
    dispatcher.utter_message(json_message={
        "type": "leave_balance_metadata",
        "event_id": event_id,
        "status": status,
        "integration": "darwinbox",
        "integration_status": integration_status,
        "balance_count": len(balances),
    })


class ActionPersonalLeaveBalance(Action):
    def name(self) -> Text:
        return "action_personal_leave_balance"

    async def run(self, dispatcher, tracker, domain):
        question = tracker.latest_message.get("text", "").strip()
        metadata = tracker.latest_message.get("metadata") or {}
        resolution = await resolve_employee(tracker.sender_id, metadata)
        balances = []
        status = "answered"
        integration_status = resolution.status

        if not resolution.employee_no:
            status = "identity_required"
            if resolution.status == "not_found":
                answer = (
                    "I could not match your verified Teams account to the synchronized "
                    "Darwinbox employee directory. Please reply with your employee code "
                    "(for example, ‘My employee code is EMP001’). I will verify it against "
                    "your Teams work email before remembering the mapping."
                )
            elif resolution.status == "refresh_failed":
                status = "integration_unavailable"
                answer = (
                    "I could not refresh the Darwinbox employee directory right now. "
                    "Your existing saved mapping was also not found. Please try again later."
                )
            else:
                status = "integration_unavailable"
                answer = (
                    "The Darwinbox employee-directory integration is not fully configured. "
                    "Please contact the application administrator."
                )
        else:
            try:
                answer, balances = await _personal_balance_answer(resolution.employee_no)
                integration_status = "success"
            except DarwinboxConfigurationError:
                answer = "The Darwinbox leave-balance adapter is not configured yet. Please contact the application administrator."
                status = "integration_unavailable"
                integration_status = "not_configured"
            except DarwinboxAPIError:
                answer = "I could not retrieve your Darwinbox leave balance right now. Please try again later or check Darwinbox directly."
                status = "integration_unavailable"
                integration_status = "api_error"

        dispatcher.utter_message(text=answer)
        _personal_log(dispatcher, tracker, question, answer, status, integration_status, balances)
        return []


class ActionRememberEmployeeCode(Action):
    def name(self) -> Text:
        return "action_remember_employee_code"

    async def run(self, dispatcher, tracker, domain):
        metadata = tracker.latest_message.get("metadata") or {}
        original = str(metadata.get("original_text") or tracker.latest_message.get("text") or "").strip()
        employee_code = extract_employee_code(original)
        balances = []
        status = "identity_required"

        if not employee_code:
            answer = "Please provide your employee code in this format: ‘My employee code is EMP001’."
            integration_status = "invalid_employee_code"
        else:
            resolution = await remember_employee_code(tracker.sender_id, metadata, employee_code)
            integration_status = resolution.status
            if resolution.employee_no:
                try:
                    answer, balances = await _personal_balance_answer(resolution.employee_no)
                    answer = "Your employee code has been verified and remembered.\n\n" + answer
                    status = "answered"
                    integration_status = "bound"
                except DarwinboxConfigurationError:
                    answer = "Your employee code was verified and remembered, but the leave-balance adapter is not configured yet."
                    status = "integration_unavailable"
                    integration_status = "leave_not_configured"
                except DarwinboxAPIError:
                    answer = "Your employee code was verified and remembered, but I could not retrieve the leave balance right now. Please try again later."
                    status = "integration_unavailable"
                    integration_status = "leave_api_error"
            elif resolution.status == "employee_not_found":
                answer = "That employee code does not exist in the current Darwinbox employee directory. Please check the code or contact HR."
            elif resolution.status == "identity_mismatch":
                answer = "That employee code does not match your verified Teams work email. Please check the code or contact HR to correct the records."
            elif resolution.status == "employee_inactive":
                answer = "That Darwinbox employee record is inactive. Please contact HR if this is unexpected."
            elif resolution.status == "employee_email_missing":
                answer = "Darwinbox does not provide an official email for that employee record, so I cannot verify it securely. Please contact HR or the application administrator."
            elif resolution.status in {"authenticated_teams_required", "verified_email_required"}:
                answer = "I can remember an employee code only from an authenticated Teams conversation with a verified work email."
            elif resolution.status == "refresh_failed":
                status = "integration_unavailable"
                answer = "I could not verify that employee code because Darwinbox is unavailable. Please try again later."
            else:
                status = "integration_unavailable"
                answer = "The Darwinbox employee-directory adapter is not configured. Please contact the application administrator."

        dispatcher.utter_message(text=answer)
        _personal_log(dispatcher, tracker, original, answer, status, integration_status, balances)
        return []
