import logging
from rasa_sdk import Action
from hr_policy.engine import PolicyIndex, CATEGORIES
from hr_policy.store import index_path

log = logging.getLogger(__name__)

class ActionAnswerPolicy(Action):
    def name(self):
        return "action_answer_policy"

    async def run(self, dispatcher, tracker, domain):
        path = index_path()
        intent = tracker.latest_message.get("intent", {}).get("name", "")
        category = intent.removeprefix("ask_")
        try:
            answer = PolicyIndex(path).answer(tracker.latest_message.get("text", ""), category if category in CATEGORIES else None)
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("Policy index unavailable")
            answer = "The policy library is unavailable. Please try again later or contact HR."
        dispatcher.utter_message(text=answer)
        return []
