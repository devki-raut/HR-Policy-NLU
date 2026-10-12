import json, uuid
from urllib.request import Request,urlopen
from pathlib import Path

def post(url,data):
    with urlopen(Request(url,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'}),timeout=60) as response:
        return json.load(response)

failed=[]
for case in json.loads(Path('scripts/ticket_checks/ticket_intents.json').read_text()):
    parsed=post('http://127.0.0.1:5005/model/parse',{'text':case['text']})
    actual=parsed['intent']['name']
    print(case['text'], '->',actual, round(parsed['intent']['confidence'],3))
    if actual!=case['intent']:failed.append((case['text'],actual,case['intent']))
print('Routing mismatches:',failed)

sender='ticket-check-'+uuid.uuid4().hex

def chat(text):
    data=post('http://127.0.0.1:8610/chat',{'sender':sender,'message':text})
    print('Chat:',text, '->',data.get('answer','')[:110])
    return data

chat('/contact_support')
chat('Outlook is not working')
chat('Account locked')
result=chat('this morning')
draft=next(item['custom'] for item in result['messages'] if item.get('custom',{}).get('type')=='ticket_draft')
assert draft['to']=='helpdesk@emergys.com'
assert 'Outlook' in draft['body']
assert 'this morning' in draft['body'].lower()
assert draft['body'].endswith('Ojas Kulkarni')
chat('/ticket_edit_subject')
result=chat('Outlook account locked — please assist')
edited=next(item['custom'] for item in result['messages'] if item.get('custom',{}).get('type')=='ticket_draft')
assert edited['subject']=='Outlook account locked — please assist'
chat('/ticket_opened')
chat('help')
chat('/contact_support')
chat('/ticket_cancel')
with urlopen('http://127.0.0.1:5005/conversations/'+sender+'/tracker',timeout=10) as response:
    tracker=json.load(response)
assert not tracker['active_loop'].get('name')
assert tracker['slots']['ticket_body'] is None
print('End-to-end draft, editing, completion, cancellation, and reset passed.')
if failed:raise SystemExit(1)
