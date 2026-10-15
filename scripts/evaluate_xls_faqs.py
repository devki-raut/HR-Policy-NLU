"""Offline evaluation of the deployed IT response function and saved Rasa model.

Source reconstruction, source symptom replay, and held-out paraphrases are
reported independently. None of these automatically generated probes replaces
human review or real-user evaluation.
"""
import argparse
import asyncio
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from actions.it_support import match_it_faq, normalize


def fixtures():
    tests = json.loads((ROOT/'scripts/ticket_checks/xls_test_set.json').read_text())['tests']
    tests.extend(json.loads((ROOT/'scripts/ticket_checks/xls_validation_set.json').read_text())['tests'])
    tests.extend(json.loads((ROOT/'scripts/ticket_checks/xls_fresh_validation.json').read_text())['tests'])
    entries = json.loads((ROOT/'data/faq_answers.json').read_text())['entries']
    cases = [e for e in entries if e.get('answer_mode') == 'it_incident']
    for entry in cases:
        same = [e for e in cases if normalize(e['symptom']) == normalize(entry['symptom'])]
        tests.append({'id': 'symptom_' + entry['incident_id'], 'split': 'source_symptom_replay',
            'question': entry['symptom'], 'expected_intent': entry['routing_intent'],
            'expected_resolution_keys': sorted({e['resolution_key'] for e in same}),
            'expected_faq_ids': [e['id'] for e in same], 'allow_clarification': True})
    from scripts.evaluate_hr_it import PROBES
    for i,(question, intent) in enumerate(PROBES):
        if not intent.startswith('it_'):
            tests.append({'id': 'regression_' + str(i), 'split': 'hr_and_ticket_regression', 'question': question, 'expected_intent': intent})
    controls = [
        ('GEMS works but Outlook is unavailable', 'it_email_access'),
        ('Outlook is not working but GEMS is unavailable', 'it_email_access'),
        ('GEMS and Outlook are not working', 'it_email_access'),
        ('How many Microsoft Teams meetings can I attend during maternity leave under the policy?', 'policy_maternity'),
        ('The network link is down', 'it_network_outage'),
        ('Our internet keeps disconnecting', 'it_network_slowness'),
        ('My online archive mailbox is full', 'it_email_access'),
        ('The system is not booting', 'it_device_support'),
        ('OneDrive files will not open', 'it_storage_collaboration'),
        ('Zscaler reports a firewall error', 'it_security'),
        ('AD self service says permission denied', 'it_account_access'),
        ('My Outlook is working fine and I need the bereavement leave policy', 'policy_bereavement'),
    ]
    for i,(q,intent) in enumerate(controls):
        tests.append({'id':'control_'+str(i), 'split':'contrast_and_edge_cases', 'question':q,'expected_intent':intent,
                      'forbidden_resolution_keys': ['dns'] if i < 3 else []})
    return tests, entries


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--skip-model',action='store_true');args=parser.parse_args()
    tests, entries=fixtures()
    training = []
    import yaml
    for path in (ROOT/'data').glob('*.yml'):
        for e in (yaml.safe_load(path.read_text()) or {}).get('nlu',[]):
            training += [normalize(s.strip()[2:]) for s in e.get('examples','').splitlines() if s.strip().startswith('- ')]
    leaked=[t['id'] for t in tests if t['split'] in {'development_paraphrase', 'held_out_validation', 'held_out_validation_ambiguity', 'fresh_holdout'} and normalize(t['question']) in training]
    assert not leaked, 'Evaluation examples leaked into training: ' + str(leaked)
    agent=None; model=None
    if not args.skip_model:
        from rasa.core.agent import Agent
        model=max((ROOT/'models').glob('*.tar.gz'),key=lambda p:p.stat().st_mtime)
        agent=Agent.load(str(model))
    results=[]
    for t in tests:
        parsed=await agent.parse_message(t['question']) if agent else None
        predicted=parsed['intent']['name'] if parsed else t['expected_intent']
        result=match_it_faq(t['question'],predicted) if predicted.startswith('it_') else None
        is_it=t['expected_intent'].startswith('it_')
        keys=t.get('expected_resolution_keys', [t.get('expected_resolution_key')])
        response_correct=None
        if is_it and 'expected_faq_ids' in t:
            response_correct=bool(result and (set(result['faq_ids']) & set(t['expected_faq_ids']) or result.get('resolution_key') in keys and result.get('resolution_key') is not None))
        if t.get('forbidden_resolution_keys'):
            response_correct=bool(result and result.get('resolution_key') not in t['forbidden_resolution_keys'])
        clarification=bool(result and result['match_mode'] in {'clarification','ambiguous'})
        if t.get('require_clarification'):
            response_correct = clarification
        results.append({**t,'predicted_intent':predicted,'route_correct':predicted==t['expected_intent'],
            'intent_before_guard': (parsed.get('intent_before_it_guard') or {}).get('name') if parsed else None,
            'response_correct':response_correct, 'clarification':clarification,
            'response_acceptable':response_correct or bool(t.get('allow_clarification') and clarification) if response_correct is not None else None,
            'result':result})
    summary={}
    for split in sorted({r['split'] for r in results}):
        rows=[r for r in results if r['split']==split]; scored=[r for r in rows if r['response_correct'] is not None]
        summary[split]={'count':len(rows),'routing_correct':sum(r['route_correct'] for r in rows) if agent else None,
            'classifier_before_guard_correct':sum(r['intent_before_guard']==r['expected_intent'] for r in rows) if agent else None,
            'response_correct':sum(bool(r['response_correct']) for r in scored), 'response_scored':len(scored),
            'clarifications':sum(r['clarification'] for r in rows),
            'response_acceptable_including_clarification':sum(bool(r['response_acceptable']) for r in scored)}
    output=ROOT/'artifacts/evaluations/xls';output.mkdir(parents=True,exist_ok=True)
    (output/'test_set.json').write_text(json.dumps({'source':'Report_15_Oct_26_092938.xls','tests':tests},indent=2)+'\n')
    payload={'model':model.name if model else None,'training_overlap':leaked,'summary':summary,'results':results}
    (output/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    with (output/'responses.csv').open('w',newline='') as f:
        fields=['id','split','question','expected_intent','predicted_intent','route_correct','response_correct','clarification','answer','faq_ids']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in results:
            writer.writerow({**{k:row.get(k) for k in fields[:-2]},'answer':(row['result'] or {}).get('answer',''),'faq_ids':','.join((row['result'] or {}).get('faq_ids',[]))})
    lines=['# XLS FAQ evaluation','',f'Model: {model.name if model else "routing model skipped"}',
        '', 'Source reconstruction and symptom replay reuse report material and are not unseen accuracy. Development and frozen validation paraphrases were excluded from NLU and retrieval text. The development set was used during implementation; the frozen validation set was held out for the baseline run, then inspected; reruns are regression checks, not a fresh estimate of unseen accuracy. Baseline files preserve the first results. fresh_holdout was first evaluated on 20 new questions (19/20 routing and 11/12 specific answers), revealing an auxiliary-word contrast bug. That first result is preserved in fresh_baseline_* files. Its rerun after correction is a regression check, not a fresh holdout estimate. Routing metrics include the trained classifier and IT routing guard; raw pre-guard predictions are retained in results.json. This is offline automated verification, not live Teams testing or human-reviewed semantic accuracy.',
        '', '| Set | Questions | Routing correct | Exact/expected answer | Clarifications | Acceptable incl. clarification |',
        '|---|---:|---:|---:|---:|---:|']
    for split, v in summary.items():
        lines.append(f"| {split} | {v['count']} | {v['routing_correct']} | {v['response_correct']}/{v['response_scored']} | {v['clarifications']} | {v['response_acceptable_including_clarification']} |")
    lines += ['', '## Failures and abstentions','']
    for row in results:
        if row['route_correct'] and row['response_correct'] is not False:continue
        lines.append(f"- {row['id']}: {row['question']} — route={row['predicted_intent']}; answer mode={(row['result'] or {}).get('match_mode')}; response correct={row['response_correct']}")
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':asyncio.run(main())
