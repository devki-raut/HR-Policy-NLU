import os
from typing import Any, Dict, List, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

from .conversation_log import append_conversation
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
