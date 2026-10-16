"""Import the HTML-as-XLS incident export without copying caller identities."""
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
ADDITIONAL_FAQS = ROOT / 'data/it_faq_additional_test_questions.md'


def additional_faq_entries():
    """Load reviewed troubleshooting Q&A as data-driven FAQ/NLU examples."""
    if not ADDITIONAL_FAQS.is_file():
        return []
    from actions.it_knowledge import topic
    section = ''
    entries = []
    for line in ADDITIONAL_FAQS.read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):
            section = line[3:].strip()
            continue
        if not re.match(r'^\|\s*\d+\s*\|', line):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 3:
            continue
        number, question, answer = cells[0], cells[1], '|'.join(cells[2:]).strip()
        route = topic(question)
        slug = re.sub(r'[^a-z0-9]+', '_', section.casefold()).strip('_')
        entries.append({
            'id': f'it_troubleshooting_{slug}_{number}',
            'intent': route,
            'routing_intent': route,
            'question': question,
            'utterances': [],
            'answer': answer,
            'answer_mode': 'it_troubleshooting',
            'enabled': True,
            'source': ADDITIONAL_FAQS.name,
            'dataset': ADDITIONAL_FAQS.name,
            'category': 'it_troubleshooting',
            'incident_count': 0,
        })
    return entries

class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.row = []; self.cell = None
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row = []
        if tag in ('td', 'th'): self.cell = []
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(html.unescape(' '.join(' '.join(self.cell).split())))
            self.cell = None
        if tag == 'tr' and self.row: self.rows.append(self.row)

CATEGORIES = {
 'it_network_outage': ('Network connection unavailable', ['The office internet link is down', 'Both WAN links are down', 'There is no network connectivity'], 'Check whether other users are affected and whether your device is connected to the approved Wi-Fi or LAN. Record the location, affected users and start time. IT may need to investigate the provider, cabling or network equipment. Do not change shared network equipment yourself.'),
 'it_network_slowness': ('Slow or intermittent network', ['Internet is very slow over Wi-Fi and LAN', 'The link keeps flapping', 'All applications are slow on the office network'], 'Check whether the issue affects Wi-Fi, LAN or both, and whether colleagues are affected. Record the affected applications, location and time. Historical incidents involved link utilisation and provider faults; IT must confirm the current cause.'),
 'it_remote_access': ('VPN, Citrix or ZTNA access', ['Citrix access is denied', 'VPN is not connecting', 'ZTNA login fails'], 'Record the exact error, application and time, and check your approved internet connection. IT may need to check account access, device registration or remote-access configuration. Never disable security or location checks, or share passwords or MFA codes.'),
 'it_email_access': ('Outlook and email access', ['I cannot log in to Outlook', 'Outlook attachments are downloading slowly', 'I need Netcore mail access'], 'Check whether the problem occurs in Outlook on the web, the desktop app or both. Record the error and whether other applications work. Access requests require IT review; do not share credentials or confidential email contents.'),
 'it_meeting_equipment': ('Teams calls and meeting equipment', ['Teams calls keep going on hold', 'The HDMI cable is not working', 'The meeting room USB C cable is stuck'], 'Check the selected microphone, speaker and camera in Teams and whether the issue affects other meetings. Record the room and affected cable or device. Do not force a stuck cable; ask IT to inspect the equipment.'),
 'it_application_access': ('Business application problems', ['The business application is not opening', 'GEMS application is not working', 'AD self service is unavailable'], 'Record the application name, exact error, start time and whether other applications or users are affected. Check your approved network connection. IT must verify the current cause and any required access approval.'),
}


# Everyday questions complement the incident-report wording. Keep these in the
# importer so a future import preserves the reviewed paraphrases.
PARAPHRASES = {
 'it_network_outage': [
  'Why is there no internet at our branch?', 'Our warehouse has lost internet connectivity',
  'The office LAN connection has gone down', 'Only one of our WAN circuits is available',
  'Neither internet connection at the site is available', 'Can you help with a site network outage?',
  'The Airtel circuit at our office is offline', 'There is no connectivity through the Sify circuit',
  'Our network stopped connecting this afternoon', 'What should I check when the branch internet is down?'],
 'it_network_slowness': [
  'Why does the office internet keep getting slow?', 'Pages take a long time to load on our LAN',
  'Downloads are very slow for everyone at the branch', 'The internet connection keeps dropping and coming back',
  'How do I report an intermittent WAN connection?', 'Our Wi-Fi speed drops throughout the day',
  'Applications respond slowly only on the office network', 'The provider circuit is repeatedly flapping',
  'What details should I collect for a slow network?', 'Can you help investigate frequent network interruptions?'],
 'it_remote_access': [
  'Why does Citrix deny access when I sign in?', 'My VPN disconnects immediately after login',
  'I cannot reach work applications through Citrix SPA', 'ZTNA refuses to authenticate my account',
  'How do I report a remote access login error?', 'Citrix displays an error when connecting from home',
  'The VPN session times out every time I connect', 'I need help with the secure remote access client',
  'What information does IT need for a Citrix failure?', 'AD self service does not load inside Citrix'],
 'it_email_access': [
  'Why can I not access Outlook in my browser?', 'Outlook on the web is rejecting my login',
  'Email attachments take too long to download in Outlook', 'I need permission to use the Netcore email journal',
  'How do I report an Outlook authentication problem?', 'The Outlook desktop app cannot open my mailbox',
  'My work mailbox is inaccessible from the browser', 'Can IT help me restore access to my email?',
  'What should I check when Outlook stops loading?', 'Outlook keeps showing an error when I sign in'],
 'it_meeting_equipment': [
  'Why do Teams calls keep dropping?', 'Nobody can hear my microphone during Teams meetings',
  'The meeting room display has no signal over HDMI', 'Our conference room USB C connector is jammed',
  'How do I report a faulty meeting room cable?', 'Teams keeps putting my call on hold unexpectedly',
  'The camera does not appear in the Teams device settings', 'The conference room speakers are silent in Teams',
  'Can IT check the broken HDMI connector?', 'My audio cuts out during a Teams video call'],
 'it_application_access': [
  'Why will GEMS not load when I open it?', 'The business portal shows an error after login',
  'I need technical help with an application that will not start', 'GEMS is displaying an access error',
  'How do I report a business application failure?', 'An internal application closes as soon as I launch it',
  'Only the sales application is unavailable while other apps work', 'The application screen freezes after signing in',
  'What information should I include about an application error?', 'Can IT investigate a portal that keeps crashing'],
}

def category(text):
    from actions.it_knowledge import topic
    return topic(text)


def clean_text(value, names=()):
    value = html.unescape(str(value))
    value = re.sub(r'https?://\S+', '[internal URL]', value)
    value = re.sub(r'\b[\w.+-]+@[\w.-]+\b', '[work account]', value)
    value = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '[site gateway]', value)
    value = re.sub(r'\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b', '[device MAC]', value, flags=re.I)
    value = re.sub(r'\b[A-Z]{2,}[_-]\d{4,}\b', '[user ID]', value, flags=re.I)
    value = re.sub(r'\b\d{9,}\b', '[reference]', value)
    for name in sorted(names, key=len, reverse=True):
        if len(name.strip()) > 5:
            value = re.sub(re.escape(name), '[requester]', value, flags=re.I)
    return ' '.join(value.split())


def report_rows():
    source = ROOT / 'data/Report_15_Oct_26_092938.xls'
    parser = ReportParser(); parser.feed(source.read_text(encoding='utf-16'))
    header = next(r for r in parser.rows if 'Incident ID' in r and 'Solution' in r)
    return source, [dict(zip(header, r)) for r in parser.rows if len(r) == len(header) and r != header]


def main():
    from collections import Counter
    from actions.it_knowledge import TOPICS, RESOLUTIONS, SYMPTOM_GUIDANCE, resolution_key
    source, rows = report_rows()
    names = {r.get(k, '') for r in rows for k in ('Caller', 'Analyst') if r.get(k)}
    faq_path = ROOT / 'data/faq_answers.json'
    faq = json.loads(faq_path.read_text())
    retained = [e for e in faq['entries'] if e.get('dataset') not in {source.name, ADDITIONAL_FAQS.name}]
    entries = []
    examples = {key: list(values[1]) + PARAPHRASES.get(key, []) for key, values in TOPICS.items()}
    incidents = []
    for row in rows:
        symptom = clean_text(row['Symptom'], names)
        # Empty/vague descriptions still receive an auditable incident record.
        route = category(symptom)
        solution = clean_text(row['Solution'], names)
        key = resolution_key(row['Solution'])
        incident_id = row['Incident ID']
        question = f'What was recorded for incident {incident_id}: {symptom}?'
        if not solution:
            body = 'No solution is recorded in the report for this incident. Its status does not establish a fix.'
        elif key == 'closure_only':
            body = 'The note records follow-ups or closure without documenting a technical fix. Recorded note: ' + solution
        elif key in {'unspecified', 'observation'}:
            body = 'The report does not establish a reproducible technical fix. Recorded note: ' + solution
        else:
            body = 'Recorded resolution note: ' + solution
        answer = (f'Historical incident {incident_id} ({row["Status"]}). Reported issue: {symptom}\n\n'
                  + body + '\n\nThis historical note is not a confirmed diagnosis of a new incident. '
                  'For a current issue, provide the exact error, start time and affected applications to IT. '
                  'Choose Create IT ticket to prepare a request. Network, device and access changes must be reviewed by IT.\n\n'
                  f'Source: {source.name}, incident {incident_id}.')
        entry = {'id': 'it_incident_' + incident_id, 'intent': 'it_incident_' + incident_id,
                 'routing_intent': route, 'question': question,
                 'utterances': [symptom, f'What happened in incident {incident_id}?', f'What resolution was recorded for incident {incident_id}?'],
                 'answer': answer, 'answer_mode': 'it_incident', 'enabled': True,
                 'source': source.name, 'dataset': source.name, 'category': 'it_incident',
                 'incident_id': incident_id, 'incident_ids': [incident_id], 'incident_count': 1,
                 'symptom': symptom, 'recorded_resolution': solution, 'resolution_key': key,
                 'status': row['Status'], 'report_category': row['Category']}
        incidents.append(entry); examples[route].extend([symptom, question])
    additional_entries = additional_faq_entries()
    for entry in additional_entries:
        examples[entry['routing_intent']].append(entry['question'])
    entries.extend(additional_entries)
    for route, (title, seeds, guidance) in TOPICS.items():
        entries.append({'id': route, 'intent': route, 'routing_intent': route,
            'question': seeds[0], 'utterances': examples[route][1:], 'answer': guidance,
            'answer_mode': 'it_guidance', 'enabled': True, 'source': 'General IT triage guidance',
            'dataset': source.name, 'category': 'it_support', 'title': title, 'incident_count': 0})
    additional_questions = {entry['question'].strip().casefold() for entry in additional_entries}
    for symptom_id, symptom in SYMPTOM_GUIDANCE.items():
        if symptom['question'].strip().casefold() in additional_questions:
            continue
        examples[symptom['route']].extend([symptom['question'], *symptom['utterances']])
        entries.append({'id': symptom_id, 'intent': symptom['route'],
            'routing_intent': symptom['route'], 'question': symptom['question'],
            'utterances': symptom['utterances'], 'answer': symptom['answer'],
            'answer_mode': 'it_symptom_guidance', 'enabled': True,
            'source': 'General IT troubleshooting guidance', 'dataset': source.name,
            'category': 'it_symptom', 'incident_count': 0})
    tests = []
    for key, (question, train, heldout, summary) in RESOLUTIONS.items():
        evidence = [e for e in incidents if e['resolution_key'] == key]
        if not evidence: continue
        # Curated questions describe the subject rather than reproduce noisy labels.
        route = category(question + ' ' + train[0])
        if key in {'fiber', 'alignment', 'bts', 'cloud_station', 'power', 'poe', 'sessions', 'upgrade', 'load_balance'}:
            route = 'it_network_outage'
        if key == 'utilisation': route = 'it_network_slowness'
        if key in {'mac', 'geo', 'new_access', 'no_logs'}: route = 'it_remote_access'
        if key in {'port', 'adapter', 'sdwan', 'device_stuck'}: route = 'it_device_support'
        if key in {'ad_policy', 'ad_auth'}: route = 'it_account_access'
        if key in {'archive', 'mailbox_missing'}: route = 'it_email_access'
        if key in {'onedrive_access', 'm365_group'}: route = 'it_storage_collaboration'
        if key in {'dns', 'dc_outage', 'salesforce'}: route = 'it_application_access'
        if key == 'traffic_failover': route = 'it_meeting_equipment'
        if key == 'local_it': route = 'it_account_access'
        if key == 'cloud_station': route = 'it_network_outage'
        ids = [e['incident_id'] for e in evidence]
        entries.append({'id': 'it_resolution_' + key, 'intent': 'it_resolution_' + key,
            'routing_intent': route, 'question': question, 'utterances': train,
            'answer': summary + '\n\nThis is historical report guidance, not a confirmed diagnosis. '
            'For a current issue, share the exact error and start time with IT. '
            'Choose Create IT ticket to prepare a request.\n\nSource: ' + source.name + ', incidents ' + ', '.join(ids) + '.',
            'answer_mode': 'it_resolution', 'enabled': True, 'category': 'it_resolution',
            'source': source.name, 'dataset': source.name, 'resolution_key': key,
            'incident_ids': ids, 'incident_count': 0})
        examples[route].extend([question, *train])
        for q in heldout:
            tests.append({'id': f'heldout_{key}_{len(tests)}', 'split': 'development_paraphrase',
                'question': q, 'expected_intent': route, 'expected_faq_ids': ['it_resolution_' + key],
                'expected_resolution_key': key})
    entries.extend(incidents)
    faq['entries'] = retained + entries
    faq_path.write_text(json.dumps(faq, ensure_ascii=False, indent=2) + '\n')
    import csv
    with (ROOT/'data/it_faqs.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['id','routing_intent','question','answer','answer_mode','incident_ids'])
        writer.writeheader()
        for entry in entries:
            writer.writerow({**{k:entry.get(k, '') for k in writer.fieldnames if k != 'incident_ids'},
                             'incident_ids': ', '.join(entry.get('incident_ids', []))})
    markdown = ['# IT FAQs from the incident report', '',
                'Historical resolutions are investigation leads, not confirmed diagnoses. Empty solutions and closure-only notes do not establish a technical fix.', '']
    for entry in entries:
        markdown += ['## ' + entry['question'], '', entry['answer'], '']
    (ROOT/'data/it_faqs.md').write_text('\n'.join(markdown))

    assigned = {}
    nlu = []; rules = []
    for route in TOPICS:
        values = []
        for text in examples[route]:
            normalized = text.strip().casefold()
            if normalized and normalized not in assigned:
                assigned[normalized] = route; values.append(text)
        nlu.append({'intent': route, 'examples': '\n'.join('- ' + v for v in values) + '\n'})
        rules.append({'rule': 'Answer ' + route, 'condition': [{'active_loop': None}],
                      'steps': [{'intent': route}, {'action': 'action_answer_it'}]})
    for name, payload in [('nlu_it.yml', {'version': '3.1', 'nlu': nlu}), ('rules_it.yml', {'version': '3.1', 'rules': rules})]:
        (ROOT/'data'/name).write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    path = ROOT/'domain.yml'; domain = yaml.safe_load(path.read_text())
    domain['intents'] = list(dict.fromkeys(domain['intents'] + list(TOPICS)))
    domain['actions'] = list(dict.fromkeys(domain['actions'] + ['action_answer_it']))
    path.write_text(yaml.safe_dump(domain, sort_keys=False, allow_unicode=True))
    # Source reconstruction is measured separately from paraphrase generalisation.
    for entry in incidents:
        tests.append({'id': 'source_' + entry['incident_id'], 'split': 'source_reconstruction',
            'question': 'What happened in incident ' + entry['incident_id'] + '?',
            'expected_intent': entry['routing_intent'], 'expected_faq_ids': [entry['id']],
            'expected_resolution_key': entry['resolution_key']})
    fixture = {'source': source.name, 'note': 'Source reconstruction is not unseen accuracy. Development paraphrases are excluded from NLU and retrieval text.', 'tests': tests}
    (ROOT/'scripts/ticket_checks/xls_test_set.json').write_text(json.dumps(fixture, indent=2) + '\n')
    coverage = {'source': source.name, 'source_rows': len(rows), 'incident_faqs': len(incidents),
        'resolution_faqs': sum(e['answer_mode'] == 'it_resolution' for e in entries),
        'guidance_faqs': len(TOPICS), 'resolution_quality': dict(Counter(e['resolution_key'] for e in incidents)),
        'incidents': [{k:e[k] for k in ('incident_id','routing_intent','resolution_key','status','id')} for e in incidents]}
    (ROOT/'data/it_report_coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
    print(json.dumps({k:v for k,v in coverage.items() if k != 'incidents'}, indent=2))

if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    main()
