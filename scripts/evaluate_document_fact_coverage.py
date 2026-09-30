#!/usr/bin/env python3
"""Estimate how much askable policy content is represented by FAQ answers.

Coverage is semantic support between atomic policy statements and reviewed FAQ
answer facts from the same source. This is a reproducible proxy; the uncovered
list is retained for human review.
"""
from __future__ import annotations
import json,re
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer
ROOT=Path(__file__).resolve().parents[1]; THRESHOLD=.65
EXCLUDED_SECTIONS=('purpose','objective','policy review','policy statement')
NOISE=('rasci:','responsible: who','accountable: accountable')
def clean(s): return re.sub(r'\s+',' ',s).strip(' •➢-\t')
def atomic(text):
 text=re.sub(r'\s+[➢•]\s*','\n',text); text=re.sub(r'\s+(?=(?:[ivx]+|[a-z])\.)','\n',text,flags=re.I)
 parts=re.split(r'\n+|(?<=[.!?])\s+(?=[A-Z])',text)
 out=[]
 for p in parts:
  p=clean(p)
  if 7<=len(re.findall(r'[A-Za-z0-9]+',p))<=90 and not any(n in p.casefold() for n in NOISE): out.append(p)
 return out
def main():
 chunks=json.load(open(ROOT/'data/policies/policies.json'))['chunks']; entries=[e for e in json.load(open(ROOT/'data/faq_answers.json'))['entries'] if e.get('enabled',True)]
 inventory=[]; seen=set()
 for c in chunks:
  if any(x in c.get('section','').casefold() for x in EXCLUDED_SECTIONS): continue
  for fact in atomic(c.get('text','')):
   key=(c.get('source'),re.sub(r'\W+',' ',fact.casefold()).strip())
   if key in seen:continue
   seen.add(key); inventory.append({'fact_id':f"{c['id']}:{len(inventory)+1}",'chunk_id':c['id'],'source':c.get('source'),'page':c.get('page'),'section':c.get('section'),'fact':fact})
 answer_units=[]
 for e in entries:
  claims=e.get('facts') or atomic(e.get('answer',''))
  for claim in claims:
   if clean(claim): answer_units.append({'faq_id':e['id'],'source':e.get('source'),'claim':clean(claim)})
 model_name='sentence-transformers/all-MiniLM-L6-v2'; model=SentenceTransformer(model_name)
 fact_vec=model.encode([x['fact'] for x in inventory],normalize_embeddings=True,show_progress_bar=False)
 claim_vec=model.encode([x['claim'] for x in answer_units],normalize_embeddings=True,show_progress_bar=False)
 source_claims=defaultdict(list)
 for i,x in enumerate(answer_units):source_claims[x['source']].append(i)
 for i,item in enumerate(inventory):
  ids=source_claims[item['source']]; scores=np.dot(claim_vec[ids],fact_vec[i]); j=int(np.argmax(scores)); best=ids[j]
  item.update({'covered':float(scores[j])>=THRESHOLD,'support_score':round(float(scores[j]),4),'supporting_faq_id':answer_units[best]['faq_id'],'supporting_claim':answer_units[best]['claim']})
 def stats(rows,t=THRESHOLD):return {'facts':len(rows),'covered':sum(x['support_score']>=t for x in rows),'coverage_percent':round(100*sum(x['support_score']>=t for x in rows)/len(rows),2) if rows else 0}
 by_source={s:stats([x for x in inventory if x['source']==s]) for s in sorted({x['source'] for x in inventory})}
 sensitivity={str(t):stats(inventory,t) for t in (.55,.60,.65,.70,.75)}
 report={'generated_at':datetime.now(timezone.utc).isoformat(),'method':{'definition':'An askable atomic document fact is covered when an enabled FAQ answer claim from the same source has cosine similarity >= 0.65.','model':model_name,'threshold':THRESHOLD,'excluded_sections':list(EXCLUDED_SECTIONS),'limitation':'Automated semantic coverage is an estimate. Review uncovered and borderline facts before treating it as policy completeness.'},'summary':stats(inventory),'threshold_sensitivity':sensitivity,'by_source':by_source,'covered_facts':[x for x in inventory if x['covered']],'uncovered_facts':[x for x in inventory if not x['covered']]}
 out=ROOT/'artifacts/evaluations/document-fact-coverage.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
 print(json.dumps({'summary':report['summary'],'by_source':by_source,'threshold_sensitivity':sensitivity,'uncovered_sample':[{'source':x['source'],'section':x['section'],'fact':x['fact'],'score':x['support_score']} for x in report['uncovered_facts'][:15]]},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
