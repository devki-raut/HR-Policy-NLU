import os
import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Text
from zoneinfo import ZoneInfo

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

from .conversation_log import append_conversation
from .darwinbox_adapters.configuration import configuration_status
from .darwinbox_adapters.diagnostics import record_darwinbox_event, setting_names
from .darwinbox import (
    DarwinboxAPIError,
    DarwinboxConfigurationError,
    fetch_holidays,
    fetch_leave_balance,
    resolve_employee_id,
)
from .faq_matcher import find_faq, retrieve_policy_chunks

FAQ_THRESHOLD = float(os.getenv("FAQ_EMBEDDING_THRESHOLD", "0.75"))


def _employee_for_tracker(tracker, allow_default=True):
    metadata = tracker.latest_message.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    employee_no = resolve_employee_id(tracker.sender_id, metadata)
    fallback = False
    if not employee_no and allow_default:
        default_email = os.getenv("DEFAULT_EMPLOYEE_EMAIL", "guest").strip() or "guest"
        employee_no = resolve_employee_id("guest", {"email": default_email})
        employee_no = employee_no or (
            os.getenv("DEFAULT_EMPLOYEE_CODE", "Ell0769").strip() or "Ell0769"
        )
        fallback = True
    return employee_no, metadata, fallback


def _fallback_notice(metadata, fallback):
    if not fallback:
        return ""
    email = str(metadata.get("email") or metadata.get("user_principal_name") or "this account")
    return (
        f"\n\nNo employee-code mapping was found for {email}; "
        "the configured default employee profile was used."
    )


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


def _personal_log(
    dispatcher, tracker, question, answer, status, integration_status, balances,
    employee_no=None, mapping_fallback=False,
):
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
        "employee_no_requested": employee_no,
        "employee_mapping_fallback": mapping_fallback,
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
        "employee_no_requested": employee_no,
        "employee_mapping_fallback": mapping_fallback,
        "balance_count": len(balances),
        "requested_leave_names": _requested_leave_names(),
        "not_returned_leave_names": _missing_leave_names(balances),
        "employee_name": next((item.employee_name for item in balances if item.employee_name), None),
        "employee_no": next((item.employee_no for item in balances if item.employee_no), None),
        "balances": [item.as_dict() for item in balances],
    })


class ActionPersonalLeaveBalance(Action):
    def name(self) -> Text:
        return "action_personal_leave_balance"

    async def run(self, dispatcher, tracker, domain):
        metadata = tracker.latest_message.get('metadata') or {}
        if metadata.get('is_guest'):
            dispatcher.utter_message(text='Sign in with Microsoft to check your personal leave balance.')
            return []
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
        employee_no = None
        identity_metadata = {}
        mapping_fallback = True
        leave_config = configuration_status()['leave']
        record_darwinbox_event('actions', 'leave_balance', 'started',
                               configured=leave_config['configured'],
                               missing_settings=leave_config['missing'],
                               auth_mode=leave_config['auth_mode'])
        try:
            employee_no, identity_metadata, mapping_fallback = _employee_for_tracker(tracker, allow_default=False)
            if not employee_no:
                record_darwinbox_event('actions', 'leave_balance', 'identity_mapping_required')
                answer = 'I could not map your signed-in account to an employee record. Contact HR or the application administrator to link your account before checking your personal leave balance.'
                dispatcher.utter_message(text=answer)
                _personal_log(dispatcher, tracker, question, answer, 'identity_mapping_required', 'employee_not_mapped', [])
                return []
            answer, balances = await _personal_balance_answer(employee_no)
            answer += _fallback_notice(identity_metadata, mapping_fallback)
            integration_status = "success"
        except DarwinboxConfigurationError as exc:
            logging.getLogger(__name__).warning('Darwinbox leave configuration: %s', exc)
            record_darwinbox_event('actions', 'leave_balance', 'configuration_error',
                                   setting_names=setting_names(exc))
            answer = "The Darwinbox leave-balance adapter is not configured yet. Please contact the application administrator."
            status = "integration_unavailable"
            integration_status = "not_configured"
        except DarwinboxAPIError:
            record_darwinbox_event('actions', 'leave_balance', 'api_error')
            answer = "I could not retrieve the Darwinbox leave balance right now. Please try again later or check Darwinbox directly."
            status = "integration_unavailable"
            integration_status = "api_error"

        dispatcher.utter_message(text=answer)
        _personal_log(
            dispatcher, tracker, question, answer, status, integration_status,
            balances, employee_no, mapping_fallback,
        )
        return []


class ActionHolidayList(Action):
    def name(self) -> Text:
        return "action_holiday_list"

    async def run(self, dispatcher, tracker, domain):
        question = tracker.latest_message.get("text", "").strip()
        intent_data = tracker.latest_message.get("intent", {})
        intent = intent_data.get("name")
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        year_match = re.search(r"\b(20\d{2})\b", question)
        explicit_year = year_match is not None
        year = int(year_match.group(1)) if year_match else today.year
        full_year = intent == "ask_holiday_list" or bool(re.search(
            r"\b(all|full|complete|entire|annual|yearly|every)\b", question.casefold()
        ))
        count_match = re.search(r"\b(?:next|upcoming)\s+(\d+)\b", question.casefold())
        limit = max(1, min(int(count_match.group(1)), 20)) if count_match else 5
        holidays = []
        status = "answered"
        integration_status = "success"
        employee_no, identity_metadata, mapping_fallback = None, {}, False
        holiday_config = configuration_status()['holidays']
        record_darwinbox_event('actions', 'holiday_list', 'started',
                               configured=holiday_config['configured'],
                               missing_settings=holiday_config['missing'])
        try:
            employee_no, identity_metadata, mapping_fallback = _employee_for_tracker(tracker)
            holidays, _raw = await fetch_holidays(employee_no, year)
            selected = (
                [item for item in holidays if item.date.year == year]
                if full_year
                else [item for item in holidays if item.date >= today][:limit]
            )
            # An unqualified "next N" request can cross the calendar-year
            # boundary. Fetch the following year only when the current year
            # does not contain enough remaining holidays.
            if not full_year and not explicit_year and len(selected) < limit:
                try:
                    next_year_holidays, _next_raw = await fetch_holidays(
                        employee_no, year + 1
                    )
                except (DarwinboxConfigurationError, DarwinboxAPIError):
                    next_year_holidays = []
                seen = {(item.date, item.name.casefold()) for item in selected}
                for item in next_year_holidays:
                    key = (item.date, item.name.casefold())
                    if key not in seen:
                        selected.append(item)
                        seen.add(key)
                    if len(selected) >= limit:
                        break
            if selected:
                if full_year:
                    heading = f"Holiday list for {year}:"
                elif len(selected) < limit:
                    heading = (
                        f"Last {len(selected)} holidays of {year}:"
                    )
                else:
                    heading = f"Next {limit} holidays:"
                rows = [
                    f"| {item.date.strftime('%d %b %Y')} | "
                    f"{item.day or item.date.strftime('%A')} | {item.name} |"
                    for item in selected
                ]
                answer = "\n".join([
                    heading,
                    "",
                    "| Date | Day | Holiday |",
                    "|---|---|---|",
                    *rows,
                ]) + _fallback_notice(identity_metadata, mapping_fallback)
            else:
                status = "no_holidays_found"
                answer = (
                    f"Darwinbox did not return any {'holidays for ' + str(year) if full_year else 'upcoming holidays'}."
                )
        except DarwinboxConfigurationError as exc:
            logging.getLogger(__name__).warning('Darwinbox holiday configuration: %s', exc)
            record_darwinbox_event('actions', 'holiday_list', 'configuration_error',
                                   setting_names=setting_names(exc))
            status = "integration_unavailable"
            integration_status = "not_configured"
            answer = "The Darwinbox holiday-list API is not configured yet. Please contact the application administrator."
            selected = []
        except DarwinboxAPIError:
            record_darwinbox_event('actions', 'holiday_list', 'api_error')
            status = "integration_unavailable"
            integration_status = "not_authorized_or_api_error"
            answer = (
                "The Darwinbox holiday-list API is not enabled for this application yet. "
                "Please contact the application administrator."
            )
            selected = []

        dispatcher.utter_message(text=answer)
        event_id = append_conversation({
            "user_id": tracker.sender_id,
            "channel": tracker.get_latest_input_channel(),
            "question": question,
            "routing_intent": intent,
            "routing_confidence": intent_data.get("confidence"),
            "detailed_intent": "holiday_list",
            "answer_mode": "darwinbox_api",
            "status": status,
            "integration": "darwinbox",
            "integration_status": integration_status,
            "employee_no_requested": employee_no,
            "employee_mapping_fallback": mapping_fallback,
            "output": answer,
            "final_answer": answer,
            "source": "Darwinbox Holiday List API" if selected else None,
            "context_ids": [],
            "contexts": [item.as_dict() for item in selected],
        })
        dispatcher.utter_message(json_message={
            "type": "holiday_list_metadata",
            "event_id": event_id,
            "status": status,
            "integration": "darwinbox",
            "integration_status": integration_status,
            "employee_no_requested": employee_no,
            "employee_mapping_fallback": mapping_fallback,
            "year": year,
            "mode": "full_year" if full_year else "upcoming",
            "requested_count": None if full_year else limit,
            "returned_count": len(selected),
            "holidays": [item.as_dict() for item in selected],
        })
        return []


class ActionRememberEmployeeCode(Action):
    def name(self) -> Text:
        return "action_remember_employee_code"

    async def run(self, dispatcher, tracker, domain):
        original = str(tracker.latest_message.get("text") or "").strip()
        answer = (
            "Contact HR or the application administrator to link your Microsoft account "
            "to the correct employee record. Employee codes sent in chat cannot change your account mapping."
        )

        dispatcher.utter_message(text=answer)
        _personal_log(
            dispatcher, tracker, original, answer, "employee_code_entry_disabled",
            "fixed_test_employee", [],
        )
        return []

# Register the IT draft actions with the action server.
from .tickets import (ActionStartTicket, ActionOfferTicket, ValidateTicketForm, ActionDraftTicket, ActionEditTicket, ActionSaveTicketEdit, ActionCloseTicket, ActionInterruptTicket)


class ActionAnswerIT(Action):
    def name(self):
        return "action_answer_it"

    def run(self, dispatcher, tracker, domain):
        from .it_support import match_it_faq
        intent = (tracker.latest_message.get("intent") or {}).get("name")
        result = match_it_faq(tracker.latest_message.get("text", ""), intent)
        dispatcher.utter_message(text=result["answer"])
        dispatcher.utter_message(json_message={"type": "it_answer_metadata", **{k:v for k,v in result.items() if k != "answer"}})
        return []
