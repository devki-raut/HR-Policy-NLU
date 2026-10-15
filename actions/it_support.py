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


def gems_unavailable_outlook_working(text):
    clauses = re.split(r'\b(?:while|but|whereas|although|however)\b|[;,.\n]', text, flags=re.I)
    gems_down = any(re.search(r'\bgems\b', c, re.I) and re.search(
        r"\bunavailable\b|\bnot\s+(?:working|opening|loading|accessible|connecting)\b|\b(?:down|inaccessible)\b|\b(?:cannot|can.t)\s+(?:be\s+opened|open|access)\b", c, re.I) for c in clauses)
    outlook_ok = any(re.search(r'\b(?:outlook|email)\s+(?:(?:is|was|still|remains)\s+)*(?:works|worked|working|available|accessible|usable|fine)\b', c, re.I) for c in clauses)
    return bool(gems_down and outlook_ok)


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
    candidates = [e for e in entries if e.get('answer_mode') in {'it_resolution', 'it_incident'}]
    docs = [normalize(' '.join([e.get('question', ''), *e.get('utterances', [])]) if e['answer_mode'] == 'it_resolution' else e['symptom']) for e in candidates]
    word = TfidfVectorizer(ngram_range=(1, 2), stop_words='english', sublinear_tf=True)
    char = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), sublinear_tf=True)
    return entries, candidates, word, word.fit_transform(docs), char, char.fit_transform(docs)


def current_index():
    return _index(FAQ_PATH.stat().st_mtime_ns)


def _result(entry, mode, score=1.0):
    return {'answer': entry['answer'], 'faq_ids': [entry['id']], 'incident_ids': entry.get('incident_ids', []),
            'routing_intent': entry['routing_intent'], 'resolution_key': entry.get('resolution_key'),
            'match_mode': mode, 'score': float(score)}


def _anchor(text):
    """Only explicit distinguishing details can select a historical cause."""
    v = normalize(text)
    if gems_unavailable_outlook_working(text) or ('gems' in v and 'email is fine' in v): return 'dns'
    rules = [
        ('geo', r'citrix.*(?:denied|location|countr|overseas|switzerland)|(?:geo.?location|switzerland|appear overseas)'),
        ('mac', r'(?:replac\w*|chang\w*).*(?:pc|laptop|device).*citrix|(?:replaced|changed).*(?:pc|laptop|device).*citrix|\bmac\b|(?:citrix|remote).*(?:changed|replaced|new (?:pc|device|laptop)|device change)|(?:replaced|changed).*(?:pc|laptop|device).*citrix'),
        ('no_logs', r'(?:citrix|remote|authentication).*(?:no .*logs|no logs)|no .*logs.*(?:citrix|remote|authentication)'),
        ('mailbox_missing', r'mailbox.*(?:not.*(?:creat|provision)|never|does not exist)|no.*(?:creat|provision).*mailbox'),
        ('onedrive_access', r'onedrive.*(?:access|handover|transfer)|access.*onedrive'),
        ('m365_group', r'microsoft 365.*group'),
        ('archive', r'(?:enable|activat|provision).*archiv|archiv.*(?:enabl|activat|provision)'),
        ('ad_policy', r'(?:ad|directory|domain).*policy.*(?:sync|synchron)|policy.*sync'),
        ('ad_auth', r'(?:ad|directory|domain).*(?:authenticat|sign in|login).*(?:fail|reject)|(?:ad|directory|domain).*authenticat'),
        ('new_access', r'(?:new|provision|enable).*(?:citrix|remote.*access)|(?:citrix|remote.*access).*(?:new user|new joiner|entitlement|new requirement)'),
        ('port', r'(?:damaged|failed|faulty|broken).*lan.*(?:port|connector)|(?:lan|network|device).*(?:port|connector).*(?:fault|fail|damag|broken)|(?:fault|fail|damag).*lan.*port'),
        ('adapter', r'(?:defect|fault).*adapter|(?:adapter|adaptor|power supply).*(?:fault|fail|defect)|(?:fault|defect).*power supply'),
        ('sdwan', r'sd\s*(?:wan|network)|sdwan'),
        ('cloud_station', r'lavelle.*(?:cloud|server)|cloud.*station'),
        ('device_stuck', r'(?:lavelle|network appliance).*(?:stuck|hang|hung|unresponsive|stopped responding)'),
        ('poe', r'\bpoe\b|power over ethernet'),
        ('sessions', r'(?:network|provider|circuit|link).*(?:session|stale)|session.*reset'),
        ('salesforce', r'salesforce.*(?:primary|secondary|rout)|(?:rout|primary).*salesforce'),
        ('dc_outage', r'(?:applications?|systems).*(?:all|every).*(?:branches|branch|sites|locations)|data cent(?:re|er)|(?:all|every).*(?:locations?|branches|sites).*(?:application|business)|(?:gems|application|systems).*(?:all sites|all locations|company wide|across locations)'),
        ('local_it', r'outside.*scope|out of scope|different it team|concerned local|assigned.*(?:cannot|not).*support'),
        ('power', r'(?:both|two).*(?:same time|same instant|simultaneous|power interruption)|simultaneous.*link'),
        ('traffic_failover', r'(?:call|conferenc|video|voice).*(?:provider|circuit|isp|wan|packets)|(?:video|voice).*traffic.*(?:move|another)'),
        ('load_balance', r'load balanc|distribut.*(?:network|wan|circuit)|not taking traffic'),
        ('fiber', r'fiber|optical cable break|severed.*cable'),
        ('alignment', r'align|antenna'),
        ('bts', r'\bbts\b|base station|tower'),
        ('upgrade', r'upgrad|feasibility'),
        ('utilisation', r'utilis|utiliz|saturat|capacity.*exhaust|consumed.*capacity'),
    ]
    # No-log evidence is more specific than a generic Citrix access denial.
    if re.search(r'(?:no|missing).*logs|logs.*(?:missing|not.*generat)', v): return 'no_logs'
    for key, pattern in rules:
        if re.search(pattern, v): return key
    return None


def match_it_faq(text, routing_intent=None, index=None):
    entries, candidates, word, matrix, char, char_matrix = index or current_index()
    ids = re.findall(r'\b(?:incident|ticket|case)\s*#?\s*(\d{6})\b', text, re.I)
    if ids:
        entry = next((e for e in candidates if e.get('incident_id') == ids[0]), None)
        if entry: return _result(entry, 'incident_id')
        return {'answer': 'That incident ID is not present in this report. Please check the ID or describe the issue.', 'faq_ids': [], 'incident_ids': [], 'routing_intent': routing_intent or topic(text), 'match_mode': 'unknown_incident', 'resolution_key': None, 'score': 0.0}
    query = normalize(text)
    exact = [e for e in candidates if e.get('symptom') and normalize(e['symptom']) == query]
    if exact:
        if len({e['resolution_key'] for e in exact}) == 1:
            return _result(exact[0], 'source_symptom')
        return _clarify(exact, routing_intent or topic(text))
    anchor = _anchor(text)
    # "Access denied" alone is a known similar case, not proof of geolocation.
    if anchor:
        entry = next((e for e in candidates if e['id'] == 'it_resolution_' + anchor), None)
        if entry: return _result(entry, 'distinctive_symptoms')
    scores = .65 * cosine_similarity(word.transform([query]), matrix)[0] + .35 * cosine_similarity(char.transform([query]), char_matrix)[0]
    order = np.argsort(scores)[::-1]
    best = int(order[0]); entry = candidates[best]
    # A semantic/lexical neighbour without distinguishing evidence must not turn
    # a generic outage into a confident claim about fibre, DNS or permissions.
    if entry['answer_mode'] == 'it_resolution' and scores[best] >= .60:
        return _result(entry, 'faq_similarity', scores[best])
    near = [candidates[int(i)] for i in order[:4] if scores[int(i)] >= .38 and candidates[int(i)]['answer_mode'] == 'it_incident']
    if near and scores[best] >= .65 and len({e['resolution_key'] for e in near}) == 1:
        return _result(near[0], 'similar_incident', scores[best])
    route = routing_intent if routing_intent and routing_intent.startswith('it_') else topic(text)
    generic = next((e for e in entries if e['id'] == route and e.get('answer_mode') == 'it_guidance'), None)
    if generic:
        return _result(generic, 'clarification', scores[best])
    return _clarify(near, route)


def _clarify(entries, route):
    return {'answer': 'Similar symptoms appear in incidents with different or undocumented resolutions. I cannot select a single historical fix from this description. What exact error appears, when did it start, and which applications still work? Choose Create IT ticket to prepare a request.',
            'faq_ids': [], 'incident_ids': [e['incident_id'] for e in entries], 'routing_intent': route, 'resolution_key': None, 'match_mode': 'ambiguous', 'score': 0.0}


def specific_it_answer(text, entries):
    # Compatibility helper for callers/tests that only request a specific match.
    if not _anchor(text): return None
    result = match_it_faq(text, index=build_index([e for e in entries if e.get('category', '').startswith('it_')]))
    return result['answer'] if result['match_mode'] == 'distinctive_symptoms' else None


def explicit_it_route(text):
    """Route explicit report references and recognised IT subjects consistently."""
    if re.search(r'\b(?:incident|ticket|case)\s*#?\s*\d{6}\b', text, re.I):
        return match_it_faq(text)['routing_intent']
    if not re.search(r'remote applications|power supply|directory authentication|different it team|concerned local support|citrix|vpn|ztna|gems|gke|outlook|outlok|mailbox|email|onedrive|one drive|m365|microsoft 365|office 365|adselfservice|ad self service|\bad\b|active directory|domain|zscal|antivirus|firewall|pdf|adobe|lavelle|sd\s*wan|sdwan|\bwan\b|\blan\b|\bisp\b|provider|fiber|fibre|optical cable|network|internet|wi[ -]?fi|teams|hdmi|usb|meeting|conferenc|salesforce|data cent|\bpoe\b|power over ethernet|base station|antenna|load balanc', text, re.I):
        return None
    source = [e for e in current_index()[0] if e.get('symptom') and normalize(e['symptom']) == normalize(text)]
    if source and len({e['routing_intent'] for e in source}) == 1:
        return source[0]['routing_intent']
    key = _anchor(text)
    if key:
        entry = next((e for e in current_index()[0] if e['id'] == 'it_resolution_' + key), None)
        if entry: return entry['routing_intent']
    if re.search(r'network|internet|wi[ -]?fi|\blan\b|\bwan\b', text, re.I) and re.search(r'disconnect|dropp|intermittent', text, re.I):
        return 'it_network_slowness'
    return topic(text)
