"""Run document-intent integration checks against the local Rasa server.

The registry examples test wiring, not unseen-language accuracy. Held-out cases
are reported separately and are not used to generate training data.
"""
import json
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
DEVELOPMENT = [
 ('How many working days off can I take after a family member dies?', 'policy_bereavement_entitlement'),
 ('For bereavement, which relatives qualify as immediate family?', 'policy_bereavement_family'),
 ('What paperwork is necessary when requesting maternity leave?', 'policy_parental_maternity_application'),
 ('How long may a new father be away on paternity leave?', 'policy_parental_paternity_entitlement'),
 ('Where can I lodge a complaint under POSH?', 'policy_posh_complaint'),
 ('Am I protected against retaliation after a harassment report?', 'policy_posh_protection'),
 ('How many privilege leave days can be transferred to next year?', 'policy_general_leave_privilege'),
 ('What is the process for getting manager approval for leave?', 'policy_general_leave_approval'),
 ('What referral incentive is paid for hiring a fresher?', 'policy_referral_bonus'),
 ('For how many months does a submitted referral stay active?', 'policy_referral_validity'),
]
HELD_OUT = [
 ('Do direct contractors qualify for bereavement leave?', 'policy_bereavement_eligibility'),
 ('Is proof of death compulsory when taking bereavement leave?', 'policy_bereavement_documentation'),
 ('How many days of service make me eligible for maternity leave?', 'policy_parental_maternity_eligibility'),
 ('Will my salary and benefits continue while I am on maternity leave?', 'policy_parental_maternity_benefits'),
 ('Who are the members of the internal POSH committee?', 'policy_posh_committee'),
 ('What steps are involved in a POSH investigation?', 'policy_posh_inquiry'),
 ('Can unused casual leave roll over into the following year?', 'policy_general_leave_casual'),
 ('How does joining midway through the year affect leave credits?', 'policy_general_leave_credit'),
 ('Which company email address should receive an employee referral?', 'policy_referral_contacts'),
 ('Must a referred candidate join before the bonus is payable?', 'policy_referral_payment'),
]
def main():
    registry=json.loads((ROOT/'data/policy_intents.json').read_text())
    rows=[]
    with httpx.Client(timeout=60) as client:
        for group, cases in [('integration',[(v['examples'][0],k) for k,v in registry.items()]),('development',DEVELOPMENT),('held_out',HELD_OUT)]:
            for question, expected in cases:
                parsed=client.post('http://127.0.0.1:5005/model/parse',json={'text':question})
                parsed.raise_for_status()
                prediction=parsed.json()['intent']
                messages=client.post('http://127.0.0.1:5005/webhooks/rest/webhook',json={'sender':f'eval-{len(rows)}','message':question})
                messages.raise_for_status()
                sources=[source for m in messages.json() for source in m.get('custom',{}).get('sources',[])]
                row={'group':group,'question':question,'expected':expected,'prediction':prediction,
                     'intent_correct':prediction['name']==expected,'source_correct':bool(sources) and all(s['source']==registry[expected]['source'] for s in sources),
                     'sources':sources, 'answer':'\n\n'.join(m.get('text','') for m in messages.json() if m.get('text'))}
                rows.append(row)
                print(group, expected, prediction['name'], row['source_correct'], flush=True)
    summary={g:{'total':sum(r['group']==g for r in rows), 'correct_intents':sum(r['group']==g and r['intent_correct'] for r in rows),
                'correct_sources':sum(r['group']==g and r['source_correct'] for r in rows)} for g in ['integration','development','held_out']}
    (ROOT/'artifacts/policy-evaluation.json').write_text(json.dumps({'summary':summary,'results':rows},indent=2))
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
