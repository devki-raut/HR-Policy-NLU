"""Rasa-routed retrieval with grounded excerpts and optional remote generation."""
import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
from urllib.parse import quote
import httpx
from dotenv import load_dotenv
from hr_policy.processing import clean_pages, process_pdf
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)
log = logging.getLogger(__name__)

CATEGORIES = {
    "leave": {"leave", "vacation", "holiday", "pto", "sick", "parental", "bereavement", "funeral", "mourning"},
    "remote_work": {"remote", "hybrid", "home", "office", "wfh"},
    "benefits": {"benefits", "insurance", "medical", "health", "coverage", "referral", "bonus"},
    "expenses": {"expense", "expenses", "reimbursement", "travel", "receipt"},
    "conduct": {"conduct", "harassment", "grievance", "discrimination", "complaint", "posh", "sexual"},
    "onboarding": {"onboarding", "joining", "probation", "orientation"},
}
STOP = set("a an the is are of to for in on i my me do does can how what when where and or with about policy please tell many much get have be it our your".split())


def infer_category_from_text(text):
    text_tokens = set(tokens(text))
    counts = {name: len(text_tokens & terms) for name, terms in CATEGORIES.items()}
    if any(counts.values()):
        return max(counts, key=counts.get)
    return "general"


def infer_category(question):
    query = set(tokens(question))
    matches = {name for name, terms in CATEGORIES.items() if query & terms}
    if len(matches) == 1:
        return next(iter(matches))
    if len(matches) > 1:
        return None
    text = " ".join(tokens(question))
    if any(term in text for term in {"bereavement", "funeral", "mourning", "death"}):
        return "leave"
    return None


def lookup_policy_intent(question, fallback_intent=None):
    text = question.lower()
    if re.search(r'\bannual leave\b|\bannual leaves\b|\bannual holiday\b|\bprivileged leave\b|\bcasual leave\b|\bleave entitlement\b', text):
        return 'policy_general_leave_entitlement'
    if re.search(r'\bpaternity leave\b|\bpaternity\b|\bnew father\b', text):
        return 'policy_parental_paternity_entitlement'
    if re.search(r'\bbereavement leave\b|\bfuneral leave\b|\bdeath in the family\b', text):
        return 'policy_bereavement_entitlement'
    if re.search(r'\bmedical certificate\b|\bsick leave\b|\bmedical leave\b', text):
        return 'policy_general_leave_entitlement'
    return fallback_intent


def tokens(text):
    words = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]
    aliases = {'funeral':'bereavement', 'mourning':'bereavement', 'vacation':'leave', 'eligible':'eligibility', 'apply':'application', 'request':'application', 'salary':'paid', 'allowance':'entitlement', 'days':'entitlement', 'weeks':'entitlement', 'remote':'remote', 'remotely':'remote', 'wfh':'remote', 'complain':'complaint'}
    return words + [aliases[t] for t in words if t in aliases and aliases[t] != t]


def public_base_url():
    for env_name in ("APP_PUBLIC_URL", "PUBLIC_BASE_URL", "BASE_URL"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value.rstrip("/")
    return "http://127.0.0.1:8000"


def build_pdf_url(filename: str) -> str:
    encoded = quote(filename, safe="")
    return f"{public_base_url()}/policies/pdfs/{encoded}"


@dataclass
class Chunk:
    id: str
    source: str
    page: int
    section: str
    category: str
    text: str

def pages(path):
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return [page.extract_text() or "" for page in PdfReader(path).pages]
    if path.suffix.lower() == ".docx":
        from docx import Document
        doc = Document(path)
        lines = []
        for paragraph in doc.paragraphs:
            prefix = "## " if paragraph.style.name.startswith("Heading") else ""
            lines.append(prefix + paragraph.text)
        for table in doc.tables:
            lines.extend(" | ".join(c.text for c in row.cells) for row in table.rows)
        return ["\n".join(lines)]
    return [path.read_text(encoding="utf-8")]

def ingest(folder, output, max_words=180):
    if max_words < 1:
        raise ValueError("max_words must be positive")
    folder, output = Path(folder), Path(output)
    chunks = []
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in {".pdf", ".docx", ".txt", ".md"})
    if not files:
        raise ValueError("No PDF, DOCX, TXT or Markdown policies found")
    if all(path.suffix.lower() == '.pdf' for path in files):
        from hr_policy.store import prepare_pdf, publish
        prepared = []
        for path in files:
            with path.open('rb') as stream:
                prepared.append(prepare_pdf(path.name, stream.read(20*1024*1024+1)))
        if len({item['filename'] for item in prepared}) != len(prepared):
            raise ValueError('Duplicate PDF filenames in folder; rename documents before ingestion')
        publish(prepared, output, 'replace')
        return [Chunk(**c) for item in prepared for c in item['chunks']]
    for path in files:
        extracted = [p.text for p in process_pdf(path).pages] if path.suffix.lower() == ".pdf" else clean_pages(pages(path))
        if not any(p.strip() for p in extracted):
            raise ValueError(f"No text in {path.name}; scanned PDFs require OCR before ingestion")
        section = path.stem
        for page_number, page in enumerate(extracted, 1):
            for block in re.split(r"\n\s*\n|(?=^#{1,6} )", page, flags=re.M):
                lines = block.strip().splitlines()
                if not lines:
                    continue
                if lines[0].startswith("#"):
                    section = lines.pop(0).lstrip("# ")
                words = " ".join(lines).split()
                for start in range(0, len(words), max_words):
                    body = " ".join(words[start:start + max_words])
                    counts = {k: len(set(tokens(section + " " + body)) & v) for k, v in CATEGORIES.items()}
                    category = max(counts, key=counts.get) if any(counts.values()) else "general"
                    source = path.relative_to(folder).as_posix()
                    key = f"{source}:{page_number}:{section}:{start}:{body}"
                    chunks.append(Chunk(hashlib.sha256(key.encode()).hexdigest()[:16], source, page_number, section, category, body))
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "chunks": [asdict(c) for c in chunks]}, indent=2), encoding="utf-8")
    temp.replace(output)
    return chunks


def intent_mapping():
    return json.loads((ROOT / 'data/policy_intents.json').read_text())


def _clean_generation_excerpt(excerpt, max_chars=12000):
    excerpt = re.split(r'Version Control', excerpt, flags=re.I)[0]
    return excerpt[:max_chars].strip()


def generate_policy_answer(question, excerpt, citation, model_name=None):
    """Optional model service; never download/load a model in a request handler.

    Validate extractive evidence returned by the service. Citations are appended
    by this application, never accepted from model output.
    """
    cleaned = _clean_generation_excerpt(excerpt)
    fallback = f'Relevant policy excerpt:\n\n{cleaned}\n\n{citation}'
    url = os.getenv('POLICY_GENERATION_URL', '').strip()
    if not url:
        return fallback
    try:
        response = httpx.post(url, json={'question':question, 'excerpt':cleaned, 'citation':citation}, timeout=25)
        response.raise_for_status()
        data = response.json()
        # A model must return exact supporting quotes; displaying the validated
        # quotes prevents unsupported paraphrases from becoming policy advice.
        evidence = data.get('evidence', [])
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('Model returned no evidence')
        quotes = [q for q in evidence if isinstance(q,str) and len(q.strip()) >= 15 and q in cleaned]
        if len(quotes) != len(evidence):
            raise ValueError('Model evidence was not in retrieved context')
        return 'Policy answer (verified excerpts):\n\n' + '\n\n'.join(dict.fromkeys(quotes)) + '\n\n' + citation
    except (httpx.HTTPError, ValueError, TypeError):
        log.warning('Generation unavailable or unsupported evidence; returning retrieved excerpts')
        return fallback


class PolicyIndex:
    def __init__(self, path):
        self.path = Path(path)
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != 1:
            raise ValueError('Unsupported policy index version')
        self.chunks = [Chunk(**c) for c in payload['chunks']]
        self.documents = payload.get('documents', {})
        self.terms = [Counter(tokens(c.section + ' ' + c.text)) for c in self.chunks]
        self.df = Counter(t for terms in self.terms for t in terms)
        self.mapping = intent_mapping()
        self.embedding_url = os.getenv('POLICY_EMBEDDING_URL', '').strip()
        self.embeddings = None
        if self.embedding_url and self.chunks:
            self.embeddings = self.embed([c.section+' '+c.text for c in self.chunks])

    def embed(self, texts):
        try:
            response = httpx.post(self.embedding_url, json={'model':os.getenv('POLICY_EMBEDDING_MODEL','policy-embedding'), 'input':texts}, timeout=15)
            response.raise_for_status()
            rows = sorted(response.json()['data'], key=lambda row:row['index'])
            vectors = [row['embedding'] for row in rows]
            if len(vectors) != len(texts) or not vectors or not vectors[0] or any(len(v)!=len(vectors[0]) for v in vectors):
                raise ValueError('Invalid embedding shape')
            return [[float(x) for x in v] for v in vectors]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            log.warning('Embedding service unavailable; using lexical retrieval')
            return None

    def retrieve(self, question, category=None, intent=None, limit=3):
        query = set(tokens(question))
        route = self.mapping.get(intent, {})
        source, sections = route.get('source'), route.get('sections', [])
        category = route.get('category', category)
        average = sum(sum(t.values()) for t in self.terms) / max(len(self.terms), 1)
        ranked = []
        query_vectors = self.embed([question]) if self.embeddings is not None else None
        for idx, (chunk, terms) in enumerate(zip(self.chunks, self.terms)):
            semantic = 0.0
            if query_vectors:
                a,b = query_vectors[0], self.embeddings[idx]
                if len(a)==len(b):
                    norm = math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
                    semantic = sum(x*y for x,y in zip(a,b))/norm if norm else 0.0
            if source and chunk.source != source:
                continue
            if category and not source and chunk.category != category:
                continue
            # Fine intents must use the registered sections, never another PDF.
            section_match = any(s.lower() in chunk.section.lower() for s in sections)
            if sections and not section_match:
                continue
            overlap = query & terms.keys()
            if not source and semantic < .65 and (len(overlap) < 2 or len(overlap) / max(len(query),1) < .25):
                continue
            score = sum(math.log(1+(len(self.chunks)-self.df[t]+.5)/(self.df[t]+.5))*terms[t]*2.5/(terms[t]+1.5*(.25+.75*sum(terms.values())/max(average,1))) for t in overlap)
            score += max(0,semantic) * 3
            if section_match:
                score += 2
            if score > 0:
                ranked.append((score, chunk))
        ranked.sort(key=lambda x:x[0], reverse=True)
        return ranked[:limit]

    def evidence(self, chunk):
        return {**asdict(chunk), 'pdf_url': build_pdf_url(chunk.source) if chunk.source in self.documents else None}

    def find_best_chunk(self, question, category=None, intent=None):
        result = self.retrieve(question, category, intent)
        return self.evidence(result[0][1]) if result else None

    def respond(self, question, category=None, intent=None, generate=False):
        query = set(tokens(question))
        if len(query) < 2:
            return {'answer':'Please add details—for example, “How do I request annual leave?”', 'sources':[], 'status':'clarify'}
        if intent and intent.startswith('ask_'):
            intent = lookup_policy_intent(question, intent)
        route = self.mapping.get(intent)
        named = [name for name, words in {
            'bereavement': {'bereavement', 'funeral', 'mourning'},
            'parental': {'maternity', 'paternity'},
            'POSH': {'posh', 'harassment'},
            'referral': {'referral', 'referrals'},
        }.items() if query & words]
        if len(named) > 1:
            return {'answer':'Please ask about one policy at a time: '+', '.join(named)+'.', 'sources':[], 'status':'clarify'}
        topics = {k for k,v in CATEGORIES.items() if query & v}
        if len(topics) > 1 and not route and category is None:
            return {'answer':'Please ask about one topic at a time: '+', '.join(sorted(topics))+'.', 'sources':[], 'status':'clarify'}
        ranked = self.retrieve(question, category, intent)
        if not ranked:
            return {'answer':'I couldn’t find enough policy evidence to answer that. Please rephrase or contact HR.', 'sources':[], 'status':'no_evidence'}
        same_source = len(ranked) > 1 and ranked[0][1].source == ranked[1][1].source
        generic_policy_question = bool(re.search(r'\b(policy|leave policy|bereavement leave|funeral leave|maternity leave|paternity leave)\b', question, flags=re.I))
        if not route and len(ranked) > 1 and ranked[1][0] >= ranked[0][0] * .9 and (ranked[0][1].source, ranked[0][1].section) != (ranked[1][1].source, ranked[1][1].section):
            if same_source and generic_policy_question:
                pass
            else:
                return {'answer':'Which policy section do you mean: '+' or '.join(c.section for _,c in ranked[:2])+'?', 'sources':[], 'status':'clarify'}
        top = ranked[0][1]
        selected = [c for _,c in ranked if c.source == top.source and (route or c.section == top.section or (same_source and generic_policy_question))]
        sources = [self.evidence(c) for c in selected]
        citations = '\n'.join(f'Source: {c.source} — {c.section}, page {c.page} [chunk {c.id}]' for c in selected)
        context = '\n\n'.join(c.text for c in selected)
        answer = generate_policy_answer(question, context, citations) if generate else f'Relevant policy excerpt:\n\n{context}\n\n{citations}'
        return {'answer':answer, 'sources':sources, 'status':'answered'}

    def answer(self, question, category=None, concise=False, generate=False, intent=None):
        return self.respond(question, category, intent, generate)['answer']


@lru_cache(maxsize=4)
def _cached_index(path, mtime_ns, size):
    return PolicyIndex(path)


def load_index(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _cached_index(str(path), stat.st_mtime_ns, stat.st_size)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    load = sub.add_parser('ingest'); load.add_argument('folder'); load.add_argument('--output',default='artifacts/policies.json')
    ask = sub.add_parser('ask'); ask.add_argument('question'); ask.add_argument('--index',default='artifacts/policies.json'); ask.add_argument('--intent')
    args=parser.parse_args()
    if args.command=='ingest':
        print(f'Indexed {len(ingest(args.folder,args.output))} chunks')
    else:
        print(PolicyIndex(args.index).answer(args.question,intent=args.intent))

if __name__=='__main__':
    main()
