#!/usr/bin/env python3
"""Evaluate the frozen unseen set and reject question leakage."""
import json, re, urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]; URL='http://127.0.0.1:5005'
def norm(x): return ' '.join(re.findall(r'[a-z0-9]+',str(x).casefold()))
def post(path,payload):
 req=urllib.request.Request(URL+path,json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
def div(a,b):return a/b if b else 0
def metric(rows):
 n=len(rows); tp=sum(x['correct'] for x in rows); fp=sum(x['faq_matched'] and not x['correct'] for x in rows); fn=n-tp; p=div(tp,tp+fp); r=div(tp,tp+fn)
 return {'total':n,'correct':tp,'routing_accuracy_percent':round(100*div(sum(x['routing_correct'] for x in rows),n),2),'faq_match_rate_percent':round(100*div(sum(x['faq_matched'] for x in rows),n),2),'fact_answer_accuracy_percent':round(100*div(sum(x['facts_correct'] for x in rows),n),2),'end_to_end_accuracy_percent':round(100*div(tp,n),2),'precision_percent':round(100*p,2),'recall_percent':round(100*r,2),'f1_percent':round(100*div(2*p*r,p+r),2),'false_positive':fp,'false_negative':fn}
def main():
 cases=json.load(open(ROOT/'tests/unseen_policy_questions.json'))['cases']; faq=json.load(open(ROOT/'data/faq_answers.json'))['entries']
 training=[p for e in faq for p in [e.get('question',''),*e.get('utterances',[])]]
 leaks=[]
 for c in cases:
  ratios=[SequenceMatcher(None,norm(c['question']),norm(p)).ratio() for p in training]
  exact=norm(c['question']) in {norm(p) for p in training}; closest=max(ratios)
  if exact: leaks.append(c['id'])
  c['_max_training_similarity']=round(closest,4)
 if leaks: raise SystemExit(f'Unseen-set leakage detected: {leaks}')
 rows=[]
 for c in cases:
  parse=post('/model/parse',{'text':c['question']}); pred=(parse.get('intent') or {}).get('name'); conf=(parse.get('intent') or {}).get('confidence',0)
  msgs=post('/webhooks/rest/webhook',{'sender':'unseen-eval-'+str(uuid4()),'message':c['question']}); answer='\n'.join(m.get('text','') for m in msgs if m.get('text')); customs=[m.get('custom') for m in msgs if isinstance(m.get('custom'),dict)]; meta=next((x for x in customs if x.get('type')=='policy_answer_metadata'),{})
  hits=[any(norm(v) in norm(answer) for v in group) for group in c['facts']]; matched=bool(meta.get('faq_id')); route=pred==c['intent']; facts=all(hits)
  rows.append({**c,'predicted_intent':pred,'routing_confidence':round(float(conf),6),'routing_correct':route,'faq_matched':matched,'faq_id':meta.get('faq_id'),'faq_score':meta.get('score'),'fact_hits':hits,'facts_correct':facts,'correct':route and matched and facts,'answer':answer})
 out={'method':{'frozen_unseen_questions':len(cases),'exact_training_question_leaks':0,'maximum_question_to_training_similarity':max(c['_max_training_similarity'] for c in cases)},'summary':metric(rows),'failures':[x for x in rows if not x['correct']],'results':rows}
 d=ROOT/'artifacts/evaluations';d.mkdir(parents=True,exist_ok=True);(d/'unseen-policy-questions.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'method':out['method'],'summary':out['summary'],'failures':[{'id':x['id'],'question':x['question'],'intent':x['predicted_intent'],'faq':x['faq_id'],'facts':x['fact_hits']} for x in out['failures']]},indent=2))
if __name__=='__main__':main()
