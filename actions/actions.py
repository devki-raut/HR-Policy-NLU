import os
import re
from typing import Any, Dict, List, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

from .conversation_log import append_conversation
from .darwinbox import (
    DarwinboxAPIError,
    DarwinboxConfigurationError,
    fetch_leave_balance,
)
from .faq_matcher import find_faq, retrieve_policy_chunks

FAQ_THRESHOLD = float(os.getenv("FAQ_EMBEDDING_THRESHOLD", "0.75"))


def _requested_leave_names():
    configured = os.getenv(
        "DARWINBOX_LEAVE_NAMES",
        "Comp off,ESOL_ Privilege Leave,ESOL_ Casual Leave,"
        "Emergys_Bereavement Leave,Leave Without Pay",
    )
    return [name.strip() for name in configured.split(",") if name.strip()]


def _missing_leave_names(balances):
    returned = {item.leave_type.casefold() for item in balances}
    return [name for name in _requested_leave_names() if name.casefold() not in returned]


def _is_personal_leave_request(text):
    """Require both personal ownership and a leave/balance concept."""
    normalized = " ".join(re.findall(r"[a-z0-9]+", str(text).casefold()))
    personal = bool(re.search(
        r"\b(my|mine|me|i|profile|account)\b|\bdo i have\b|\bfor me\b",
        normalized,
    ))
    leave_topic = bool(re.search(
        r"\b(leave|leaves|balance|balances|pl|cl|time off|timeoff|darwinbox)\b",
        normalized,
    ))
    return personal and leave_topic


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

        if question.casefold() in {"leave", "leaves"}:
            answer = (
                "What would you like to know about leave? Ask a policy question such as "
                "‘How many casual leaves are allowed?’ or ask ‘Show my leaves’ to fetch "
                "your personal balance."
            )
            event_id = append_conversation({
                "user_id": tracker.sender_id,
                "channel": channel,
                "question": question,
                "routing_intent": routing_intent,
                "routing_confidence": routing_confidence,
                "detailed_intent": None,
                "status": "clarification_required",
                "output": answer,
                "final_answer": answer,
                "source": None,
                "context_ids": [],
                "contexts": [],
            })
            dispatcher.utter_message(text=answer)
            dispatcher.utter_message(json_message={
                "type": "policy_answer_metadata",
                "event_id": event_id,
                "status": "clarification_required",
                "routing_intent": routing_intent,
                "routing_confidence": routing_confidence,
                "context_ids": [],
            })
            return []

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
    lines = []
    for item in balances:
        if item.balance is not None:
            display_name = item.leave_type
            for prefix in ("ESOL_ ", "Emergys_"):
                if display_name.startswith(prefix):
                    display_name = display_name[len(prefix):]
            lines.append(f"{display_name}: {item.balance} {item.unit}")
    answer = "Your current leave balances are:\n" + "\n".join(lines)
    return answer, balances


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
        "contexts": [item.as_dict() for item in balances],
    })
    dispatcher.utter_message(json_message={
        "type": "leave_balance_metadata",
        "event_id": event_id,
        "status": status,
        "integration": "darwinbox",
        "integration_status": integration_status,
        "balance_count": len(balances),
        "requested_leave_names": _requested_leave_names(),
        "not_returned_leave_names": _missing_leave_names(balances),
        "employee_name": next((item.employee_name for item in balances if item.employee_name), None),
        "employee_no": next((item.employee_no for item in balances if item.employee_no), None),
        "balances": [item.as_dict() for item in balances],
    })


class ActionPersonalLeaveBalance(Action):
    # Temporary test identity requested for the leave-balance integration.
    # Do not derive this value from chat input.
    EMPLOYEE_NO = "Ell0769"

    def name(self) -> Text:
        return "action_personal_leave_balance"

    async def run(self, dispatcher, tracker, domain):
        question = tracker.latest_message.get("text", "").strip()
        balances = []
        status = "answered"
        if not _is_personal_leave_request(question):
            answer = (
                "What would you like to fetch? To check your leave balance, ask "
                "‘Show my leaves’ or ‘What is my leave balance?’"
            )
            dispatcher.utter_message(text=answer)
            _personal_log(
                dispatcher, tracker, question, answer, "clarification_required",
                "request_incomplete", [],
            )
            return []
        try:
            answer, balances = await _personal_balance_answer(self.EMPLOYEE_NO)
            integration_status = "success"
        except DarwinboxConfigurationError:
            answer = "The Darwinbox leave-balance adapter is not configured yet. Please contact the application administrator."
            status = "integration_unavailable"
            integration_status = "not_configured"
        except DarwinboxAPIError:
            answer = "I could not retrieve the Darwinbox leave balance right now. Please try again later or check Darwinbox directly."
            status = "integration_unavailable"
            integration_status = "api_error"

        dispatcher.utter_message(text=answer)
        _personal_log(dispatcher, tracker, question, answer, status, integration_status, balances)
        return []


class ActionRememberEmployeeCode(Action):
    def name(self) -> Text:
        return "action_remember_employee_code"

    async def run(self, dispatcher, tracker, domain):
        original = str(tracker.latest_message.get("text") or "").strip()
        answer = (
            "Employee-code entry is disabled in this test deployment. "
            "Ask ‘What is my leave balance?’ to test the configured leave account."
        )

        dispatcher.utter_message(text=answer)
        _personal_log(
            dispatcher, tracker, original, answer, "employee_code_entry_disabled",
            "fixed_test_employee", [],
        )
        return []
