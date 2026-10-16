"""Retrieve source-backed IT FAQs, abstaining on conflicting incident fixes."""
from functools import lru_cache
import json
from pathlib import Path
import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from actions.it_knowledge import topic

FAQ_PATH = Path(__file__).resolve().parents[1] / 'data/faq_answers.json'


def normalize(text):
    text = text.casefold()
    for pattern, replacement in [(r'\bfib(?:er|re)\b', 'fiber'), (r'\bfibercut\b', 'fiber cut'), (r'\bwan\b', 'network'),
                                  (r'\bisp\b', 'provider'), (r'\badaptor\b', 'adapter'), (r'\bcannot\b|\bcan.t\b', 'not'),
                                  (r'\b(?:m365|office 365)\b', 'microsoft 365'), (r'one\s+drive', 'onedrive')]:
        text = re.sub(pattern, replacement, text)
    return ' '.join(re.findall(r'[a-z0-9]+', text))


@lru_cache(maxsize=2)
def _index(stamp):
    entries = [e for e in json.loads(FAQ_PATH.read_text())['entries'] if e.get('enabled', True) and e.get('category', '').startswith('it_')]
    return build_index(entries)


def build_index(entries):
    candidates = []
    docs = []
    for entry in entries:
        if entry.get('answer_mode') not in {
                'it_guidance', 'it_resolution', 'it_incident', 'it_troubleshooting', 'it_symptom_guidance'}:
            continue
        # Guidance utterances also contain the route's incident/NLU examples.
        # Indexing those as guidance FAQ text creates score-1 ties with the
        # actual incident. Only the reviewed guidance question represents the
        # guidance answer in retrieval; other FAQ modes index every paraphrase.
        phrases = [entry.get('question', '')]
        if entry.get('answer_mode') != 'it_guidance':
            phrases.extend(entry.get('utterances', []))
        if entry.get('symptom'):
            phrases.append(entry['symptom'])
        seen = set()
        for phrase in phrases:
            document = normalize(phrase)
            if document and document not in seen:
                seen.add(document)
                candidates.append(entry)
                docs.append(document)
    word = TfidfVectorizer(ngram_range=(1, 2), stop_words='english', sublinear_tf=True)
    char = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), sublinear_tf=True)
    return entries, candidates, word, word.fit_transform(docs), char, char.fit_transform(docs)


def current_index():
    return _index(FAQ_PATH.stat().st_mtime_ns)


def _result(entry, mode, score=1.0):
    return {'answer': entry['answer'], 'faq_ids': [entry['id']], 'incident_ids': entry.get('incident_ids', []),
            'routing_intent': entry['routing_intent'], 'resolution_key': entry.get('resolution_key'),
            'match_mode': mode, 'score': float(score)}


def match_it_faq(text, routing_intent=None, index=None):
    entries, candidates, word, matrix, char, char_matrix = index or current_index()
    ids = re.findall(r'\b(?:incident|ticket|case)\s*#?\s*(\d{6})\b', text, re.I)
    if ids and not any(e.get('incident_id') == ids[0] for e in candidates):
        return {'answer': 'That incident ID is not present in this report. Please check the ID or describe the issue.', 'faq_ids': [], 'incident_ids': [], 'routing_intent': routing_intent or topic(text), 'match_mode': 'unknown_incident', 'resolution_key': None, 'score': 0.0}
    query = normalize(text)
    route = routing_intent if routing_intent and routing_intent.startswith('it_') else topic(text)
    scores = .65 * cosine_similarity(word.transform([query]), matrix)[0] + .35 * cosine_similarity(char.transform([query]), char_matrix)[0]
    eligible = [i for i, entry in enumerate(candidates) if entry.get('routing_intent') == route]
    order = sorted(eligible, key=lambda i: scores[i], reverse=True)
    near = [candidates[i] for i in order[:4]]
    if order:
        best = order[0]
        entry = candidates[best]
        minimum = {
            'it_guidance': .46,
            'it_troubleshooting': .46,
            'it_symptom_guidance': .46,
            'it_resolution': .55,
            'it_incident': .82,
        }[entry['answer_mode']]
        if scores[best] >= minimum:
            return _result(entry, 'cosine_similarity', scores[best])
    generic = next((e for e in entries if e['id'] == route and e.get('answer_mode') == 'it_guidance'), None)
    if generic:
        score = scores[order[0]] if order else 0.0
        return _result(generic, 'intent_guidance', score)
    return _clarify(near, route)


def _clarify(entries, route):
    return {'answer': 'Similar symptoms appear in incidents with different or undocumented resolutions. I cannot select a single historical fix from this description. What exact error appears, when did it start, and which applications still work? Choose Create IT ticket to prepare a request.',
            'faq_ids': [], 'incident_ids': [e['incident_id'] for e in entries], 'routing_intent': route, 'resolution_key': None, 'match_mode': 'ambiguous', 'score': 0.0}


