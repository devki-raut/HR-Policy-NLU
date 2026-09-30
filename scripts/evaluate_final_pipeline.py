#!/usr/bin/env python3
"""Evaluate DIET routing, FAQ matching, fact coverage, and evidence grounding."""
from __future__ import annotations

import json
import math
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RASA = "http://127.0.0.1:5005"
OUT_DIR = ROOT / "artifacts" / "evaluations"

# Each fact group accepts any listed phrase. All groups must be present.
CASES = [
("original","How many weeks of maternity leave are available?","policy_maternity",[["26 weeks"],["12 weeks"]]),
("original","How much maternity leave is available if the employee already has two or more surviving children?","policy_maternity",[["12 weeks"],["two or more"]]),
("original","How long must an employee have worked to be eligible for maternity leave?","policy_maternity",[["80 days"],["12 months"]]),
("original","Can maternity leave be taken in parts?","policy_maternity",[["continuous stretch","continuous"],["not availed in separate parts","cannot be availed in parts","not be availed in parts"]]),
("original","Can I work from home during maternity leave?","policy_maternity",[["resume duties"],["additional leave"]]),
("original","How many Privilege Leaves and Casual Leaves does a permanent employee get in a year?","policy_general_leave",[["18 privilege leaves","18 privileged leaves"],["8 casual leaves"]]),
("original","How many PL can I carry forward to the next year?","policy_general_leave",[["20"],["carry","carried forward"]]),
("original","Can I take 3 Casual Leaves continuously in a month?","policy_general_leave",[["two consecutive"],["per month"]]),
("original","Can Casual Leave be carried forward to the next year?","policy_general_leave",[["non-cumulative","non cumulative"],["cannot be carried forward"]]),
("original","What happens if I take LWP from Friday to Monday?","policy_general_leave",[["four days"],["friday"],["monday"]]),
("original","Who is eligible to participate in the employee referral program?","policy_referral",[["employees"],["preceding six months"]]),
("original","Can I refer someone who applied to Emergys three months ago?","policy_referral",[["preceding six months","previous six months","six months"],["not have applied","have not applied"]]),
("original","How much referral bonus is paid for a candidate with 7 years of experience?","policy_referral",[["45,000","45000"],["6 to less than 10"]]),
("original","When will the referral bonus be paid?","policy_referral",[["probation"],["six months"]]),
("original","What happens if two employees refer the same candidate?","policy_referral",[["first-come","first come"],["timestamp"]]),
("original","Who is eligible for bereavement leave?","policy_bereavement",[["full-time employees","full time employees"],["direct contractors"]]),
("original","How many days of paid bereavement leave can I take?","policy_bereavement",[["5 consecutive working days","five consecutive working days"]]),
("original","Does bereavement leave cover the death of a parent-in-law?","policy_bereavement",[["parents-in-law","parents in law"]]),
("original","Do I need to submit a death certificate to apply for bereavement leave?","policy_bereavement",[["may request"],["death certificate"]]),
("original","Can I combine bereavement leave with my Privilege Leave or Casual Leave?","policy_bereavement",[["may be combined"],["subject to approval"]]),
("original","What types of behaviour are considered sexual harassment under the policy?","policy_posh",[["unwelcome"],["sexual advances"]]),
("original","Who can I contact to file a POSH complaint?","policy_posh",[["internal committee","committee member"],["human resources","hr"]]),
("original","Within how many months should I file a POSH complaint?","policy_posh",[["three months"],["last incident"]]),
("original","How long does the Internal Committee have to complete its inquiry?","policy_posh",[["three months"],["10 days"]]),
("original","What happens if someone retaliates against an employee for filing a POSH complaint?","policy_posh",[["strictly prohibited"],["cooperates"]]),
# Unseen paraphrases measure generalisation rather than memorisation.
("paraphrase_regression","Is leave available when my spouse's parent dies?","policy_bereavement",[["parents-in-law","parents in law"]]),
("paraphrase_regression","What proof might HR ask for after a family death?","policy_bereavement",[["may request"],["death certificate"]]),
("paraphrase_regression","How much paid time is allowed after an immediate family death?","policy_bereavement",[["5 consecutive working days","five consecutive working days"]]),
("paraphrase_regression","What service tenure is needed before maternity benefits apply?","policy_maternity",[["80 days"],["12 months"]]),
("paraphrase_regression","Must maternity leave be used as a single block?","policy_maternity",[["continuous"]]),
("paraphrase_regression","What does the policy say about returning after maternity absence?","policy_maternity",[["resume duties"],["additional leave"]]),
("paraphrase_regression","What is the maximum unused PL balance I can move into January?","policy_general_leave",[["20"],["carry","carried forward"]]),
("paraphrase_regression","Are three CL days in a row permitted?","policy_general_leave",[["two consecutive"],["per month"]]),
("paraphrase_regression","Do weekends increase unpaid leave when absence runs Friday through Monday?","policy_general_leave",[["four days"],["weekend","saturday and sunday"]]),
("paraphrase_regression","What rule applies to a candidate who submitted an application recently?","policy_referral",[["preceding six months","previous six months","six months"],["not have applied","have not applied"]]),
("paraphrase_regression","Who receives credit when a candidate is submitted twice?","policy_referral",[["first-come","first come"],["timestamp"]]),
("paraphrase_regression","Which bonus bracket covers eight years of experience?","policy_referral",[["45,000","45000"],["6 to less than 10"]]),
("paraphrase_regression","What conduct falls within the definition of workplace sexual harassment?","policy_posh",[["unwelcome"],["sexual advances"]]),
("paraphrase_regression","By when must the committee finish investigating?","policy_posh",[["three months"],["10 days"]]),
("paraphrase_regression","Does the policy forbid action against a witness who helped an investigation?","policy_posh",[["strictly prohibited"],["cooperates"]]),
("paraphrase_regression","How much leave can a new father receive?","policy_paternity",[["10 working days","ten working days"]]),
("paraphrase_regression","Does paternity leave have to be used in one block?","policy_paternity",[["continuous"]]),
("paraphrase_regression","What is the deadline for using paternity leave after birth?","policy_paternity",[["six months","6 months"]]),
]
STOP=set("a an and are as at be by can do does for from get how i in is it many of on or the their to what when who with policy leave employee employees".split())

def post(path,payload):
 data=json.dumps(payload).encode(); req=urllib.request.Request(RASA+path,data=data,headers={"Content-Type":"application/json"})
 with urllib.request.urlopen(req,timeout=120) as response: return json.load(response)
def norm(s): return " ".join(re.findall(r"[a-z0-9]+",str(s).casefold()))
def has_group(answer,group):
 a=norm(answer); return any(norm(x) in a for x in group)
def safe_div(a,b): return a/b if b else 0.0
def pct(x): return round(100*x,2)
def content_tokens(s): return [x for x in re.findall(r"[a-z0-9]+",str(s).casefold()) if len(x)>2 and x not in STOP]

def registry_audit():
 faq_all=json.load(open(ROOT/'data/faq_answers.json'))['entries']; faq=[e for e in faq_all if e.get('enabled',True)]; chunks=json.load(open(ROOT/'data/policies/policies.json'))['chunks']; by_id={c['id']:c for c in chunks}
 source_text=defaultdict(str)
 for c in chunks: source_text[c.get('source','')]+=' '+c.get('text','')
 valid=0; recalls=[]; fact_count=0
 docs=defaultdict(set); term_counts=defaultdict(Counter)
 for e in faq:
  ev=e.get('evidence') or {}; c=by_id.get(ev.get('chunk_id'))
  valid += bool(c and c.get('source')==e.get('source') and c.get('text')==ev.get('quote'))
  for fact in e.get('facts',[]):
   tokens=content_tokens(fact); document=set(content_tokens(source_text[e.get('source','')]))
   if tokens: recalls.append(sum(t in document for t in tokens)/len(tokens)); fact_count+=1
  route=e.get('routing_intent')
  for phrase in [e.get('question',''),*e.get('utterances',[])]: docs[route].update(content_tokens(phrase))
 for route, terms in docs.items():
  for term in terms: term_counts[route][term]=sum(term in set(content_tokens(p)) for e in faq if e.get('routing_intent')==route for p in [e.get('question',''),*e.get('utterances',[])])
 return {"enabled_faq_entries":sum(e.get('enabled',True) for e in faq),"valid_evidence_entries":valid,"evidence_validity_percent":pct(safe_div(valid,len(faq))),"policy_facts":fact_count,"mean_fact_token_coverage_percent":pct(sum(recalls)/len(recalls)),"facts_with_at_least_65_percent_token_coverage_percent":pct(safe_div(sum(r>=.65 for r in recalls),len(recalls))),"keyword_sets":{r:[t for t,_ in c.most_common(20)] for r,c in term_counts.items()}}

def evaluate():
 results=[]
 for index,(split,q,expected_intent,facts) in enumerate(CASES,1):
  parse=post('/model/parse',{"text":q}); predicted=(parse.get('intent') or {}).get('name'); confidence=(parse.get('intent') or {}).get('confidence',0)
  messages=post('/webhooks/rest/webhook',{"sender":f"final-eval-{uuid4()}","message":q})
  text="\n".join(m.get('text','') for m in messages if m.get('text'))
  customs=[m.get('custom') for m in messages if isinstance(m.get('custom'),dict)]
  meta=next((x for x in customs if x.get('type')=='policy_answer_metadata'),{})
  evidence=[x for x in customs if x.get('type')=='policy_evidence']
  routing_ok=predicted==expected_intent; matched=meta.get('status')!='no_confident_match' and bool(meta.get('faq_id'))
  fact_hits=[has_group(text,g) for g in facts]; facts_ok=all(fact_hits); correct=routing_ok and matched and facts_ok
  results.append({"split":split,"question":q,"expected_routing_intent":expected_intent,"predicted_routing_intent":predicted,"routing_confidence":round(float(confidence),6),"routing_correct":routing_ok,"faq_matched":matched,"faq_id":meta.get('faq_id'),"detailed_intent":meta.get('detailed_intent'),"faq_score":meta.get('score'),"matched_question":meta.get('matched_question'),"required_fact_groups":facts,"fact_group_hits":fact_hits,"fact_coverage_percent":pct(safe_div(sum(fact_hits),len(fact_hits))),"facts_correct":facts_ok,"citation_present":"Source:" in text,"context_ids":meta.get('context_ids',[]),"evidence_count":len(evidence),"correct":correct,"answer":text})
 return results

def metrics(rows):
 total=len(rows); tp=sum(r['correct'] for r in rows); fp=sum(r['faq_matched'] and not r['correct'] for r in rows); fn=total-tp
 precision=safe_div(tp,tp+fp); recall=safe_div(tp,tp+fn)
 return {"total":total,"correct":tp,"incorrect":total-tp,"routing_accuracy_percent":pct(safe_div(sum(r['routing_correct'] for r in rows),total)),"faq_match_coverage_percent":pct(safe_div(sum(r['faq_matched'] for r in rows),total)),"fact_answer_accuracy_percent":pct(safe_div(sum(r['facts_correct'] for r in rows),total)),"citation_coverage_percent":pct(safe_div(sum(r['citation_present'] for r in rows),total)),"context_coverage_percent":pct(safe_div(sum(bool(r['context_ids']) for r in rows),total)),"end_to_end_accuracy_percent":pct(safe_div(tp,total)),"precision_percent":pct(precision),"recall_percent":pct(recall),"f1_percent":pct(safe_div(2*precision*recall,precision+recall)),"true_positive":tp,"false_positive":fp,"false_negative":fn}

def main():
 OUT_DIR.mkdir(parents=True,exist_ok=True); rows=evaluate(); report={"generated_at":datetime.now(timezone.utc).isoformat(),"method":{"original_questions":25,"paraphrase_regression_questions":18,"correct_requires":"correct DIET route + confident FAQ + all required fact groups","precision_recall_note":"wrong confident answers count as both FP and missed expected answer (FN); abstentions count as FN"},"summary":{"all":metrics(rows),"original":metrics([r for r in rows if r['split']=='original']),"paraphrase_regression":metrics([r for r in rows if r['split']=='paraphrase_regression'])},"registry_audit":registry_audit(),"failures":[r for r in rows if not r['correct']],"results":rows}
 (OUT_DIR/'final-pipeline.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
 lines=["EmployeeAssist final pipeline evaluation",json.dumps(report['summary'],indent=2),json.dumps(report['registry_audit'],indent=2)]
 (OUT_DIR/'final-pipeline.txt').write_text('\n\n'.join(lines)+'\n')
 print(json.dumps({"summary":report['summary'],"registry_audit":{k:v for k,v in report['registry_audit'].items() if k!='keyword_sets'},"failures":[{"question":r['question'],"predicted":r['predicted_routing_intent'],"faq":r['faq_id'],"score":r['faq_score'],"facts":r['fact_group_hits']} for r in report['failures']]},indent=2))
if __name__=='__main__': main()
