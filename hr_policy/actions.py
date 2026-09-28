import asyncio
import logging
import os
from rasa_sdk import Action
from hr_policy.engine import load_index, CATEGORIES
from hr_policy.store import index_path
log = logging.getLogger(__name__)

class ActionAnswerPolicy(Action):
    def name(self):
        return 'action_answer_policy'

    async def run(self, dispatcher, tracker, domain):
        prediction = tracker.latest_message.get('intent', {})
        intent = prediction.get('name','')
        category = intent.removeprefix('ask_')
        try:
            index = await asyncio.to_thread(load_index, index_path())
            result = await asyncio.to_thread(index.respond, tracker.latest_message.get('text',''),
                                            category if category in CATEGORIES else None, intent,
                                            os.getenv('USE_POLICY_GENERATION','0').lower() in {'1','true','yes'})
        except (OSError, ValueError, KeyError, TypeError):
            log.exception('Policy library unavailable')
            result = {'answer':'The policy library is unavailable. Please try again later or contact HR.', 'sources':[], 'status':'unavailable'}
        dispatcher.utter_message(text=result['answer'], json_message={'sources':result['sources'], 'status':result['status'], 'intent':prediction})
        return []
