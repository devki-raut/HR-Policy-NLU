import os
from typing import Any, Dict, List, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher

from .faq_matcher import find_faq

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
            return []

        # Replace this block with your existing PDF RAG function.
        dispatcher.utter_message(
            text="I couldn't find a confident FAQ match. "
            "I would normally search the policy documents here."
        )
        return []
