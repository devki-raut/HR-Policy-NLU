"""High-precision support routing alongside trained DIET intent detection."""
import re
from typing import Any, Dict, List
from rasa.engine.graph import ExecutionContext, GraphComponent
from rasa.engine.recipes.default_recipe import DefaultV1Recipe
from rasa.engine.storage.resource import Resource
from rasa.engine.storage.storage import ModelStorage
from rasa.nlu.classifiers.classifier import IntentClassifier
from rasa.shared.nlu.training_data.message import Message

POLICY = re.compile(r'\b(polic(?:y|ies)|rules?|guidelines?|entitlements?|eligibility|allowed)\b', re.I)
CONTACT_INFO = re.compile(r'\b(?:what|which|where|how)\b.*\b(?:support|help\s*desk|IT)\b.*\b(?:email|address|phone|number|contact)\b|\bhow\s+(?:do|can)\s+I\s+contact\s+(?:IT|support|help\s*desk)\b', re.I)
APPLICATION = re.compile(r'\b(outlook|outlok|vpn|wi[ -]?fi|laptop|computer|keyboard|printer|teams|darwinbox|internet|network|email)\b', re.I)
FAILURE = re.compile(r"\b(?:not\s+(?:working|connecting|starting)|won['’]?t\s+(?:connect|work|start|open|load|boot|turn|let|sign|log)|cannot|can['’]?t|cant|failed|fails|broken|crash(?:es|ing)?|unable|errors?|locked|freezes?|disconnecting)\b", re.I)
REQUEST = re.compile(r'\b(?:raise|create|open|log|prepare|draft)\b.*\b(?:ticket|support request|helpdesk email)\b|\bcontact\s+(?:IT|support|help\s*desk)\b', re.I)
# A user can describe a problem without saying “ticket”.  Keep this
# deliberately tied to a known work application/device or failure signal so
# ordinary HR questions do not enter the support flow.
ISSUE_REPORT = re.compile(r"\b(?:i\s+am|i'm|im|we\s+are|we're|my|the)\b.*\b(?:facing|experiencing|have|has|issue|problem|not\s+working|error|unable|failed|broken)\b", re.I)


def support_route(text: str, predicted: str | None) -> str | None:
    if POLICY.search(text):
        return 'nlu_fallback' if predicted in {'contact_support', 'report_it_issue'} else None
    if CONTACT_INFO.search(text):
        return 'ask_support_contact'
    # Explicit ticket/support requests should not fall through to the generic
    # clarification intent merely because the issue wording is unfamiliar.
    if REQUEST.search(text):
        return 'contact_support'
    if ISSUE_REPORT.search(text) and (APPLICATION.search(text) or FAILURE.search(text)):
        return 'report_it_issue'
    if APPLICATION.search(text) and FAILURE.search(text):
        return 'contact_support' if REQUEST.search(text) else 'report_it_issue'
    return None


@DefaultV1Recipe.register(DefaultV1Recipe.ComponentType.INTENT_CLASSIFIER, is_trainable=False)
class TicketIntentGuard(GraphComponent, IntentClassifier):
    @classmethod
    def create(cls, config: Dict[str, Any], model_storage: ModelStorage,
               resource: Resource, execution_context: ExecutionContext) -> 'TicketIntentGuard':
        return cls()

    def process(self, messages: List[Message]) -> List[Message]:
        for message in messages:
            route = support_route(message.get('text', ''), (message.get('intent') or {}).get('name'))
            if route:
                intent = {'name': route, 'confidence': 1.0}
                ranking = [intent, *[item for item in message.get('intent_ranking', []) if item.get('name') != route]]
                message.set('intent', intent, add_to_output=True)
                message.set('intent_ranking', ranking, add_to_output=True)
        return messages
