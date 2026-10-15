"""Offline routing probes; never calls employee APIs or sends Teams messages."""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROBES = [
 ('GEMS unavailable while Outlook worked', 'it_application_access'),
 ('Outlook works but GEMS is not opening', 'it_application_access'),
 ('Our branch has lost both internet connections', 'it_network_outage'),
 ('The office WAN circuit has gone down', 'it_network_outage'),
 ('Internet speed drops repeatedly at the warehouse', 'it_network_slowness'),
 ('Every application loads very slowly on LAN', 'it_network_slowness'),
 ('Citrix refuses my login with access denied', 'it_remote_access'),
 ('Unable to establish my VPN connection', 'it_remote_access'),
 ('Outlook on the web will not let me sign in', 'it_email_access'),
 ('I need access to the Netcore mail journal', 'it_email_access'),
 ('The conference room HDMI connection is broken', 'it_meeting_equipment'),
 ('Teams meeting audio keeps cutting out', 'it_meeting_equipment'),
 ('GEMS will not open on my computer', 'it_application_access'),
 ('What is the maternity leave entitlement?', 'policy_maternity'),
 ('How long can fathers take paternity leave?', 'policy_paternity'),
 ('Are my parents in law covered by bereavement leave?', 'policy_bereavement'),
 ('How do I make a POSH complaint?', 'policy_posh'),
 ('What is the employee referral bonus policy?', 'policy_referral'),
 ('How many casual leave days are allowed?', 'policy_general_leave'),
 ('Please create an IT support ticket', 'contact_support'),
]

async def main():
    from rasa.core.agent import Agent
    model = max((ROOT/'models').glob('*.tar.gz'), key=lambda p:p.stat().st_mtime)
    agent = Agent.load(str(model))
    rows = []
    for question, expected in PROBES:
        parsed = await agent.parse_message(question)
        prediction = parsed['intent']['name']
        rows.append({'question':question, 'expected':expected, 'predicted':prediction, 'confidence':parsed['intent']['confidence'], 'passed':prediction==expected})
    result = {'model': model.name, 'passed':sum(r['passed'] for r in rows), 'total':len(rows), 'probes':rows}
    (ROOT/'artifacts/logs/hr_it_evaluation.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))

if __name__ == '__main__': asyncio.run(main())
