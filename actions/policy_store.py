"""Atomic policy publication, using the shared PDF processing pipeline."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from actions.policy_processing import MAX_BYTES, process_pdf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / 'data/policies/policies.json'
POLICY_PDF_DIR = ROOT / 'data/policies/pdfs'
MARKDOWN_DIR = ROOT / 'data/policies/mds'


def index_path():
    return Path(os.getenv('POLICY_INDEX', str(DEFAULT_INDEX))).resolve()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def validate_filename(filename):
    if not filename or '/' in filename or '\\' in filename or len(filename) > 200 or Path(filename).suffix.lower() != '.pdf':
        raise ValueError('Provide a plain PDF filename of at most 200 characters')


def pdf_storage_path(filename):
    validate_filename(filename)
    return POLICY_PDF_DIR / filename


def markdown_output_path(filename):
    return MARKDOWN_DIR / Path(filename).with_suffix('.md').name


def chunk_markdown(markdown_text, filename, max_words=180):
    """Section-aware bounded chunks, retaining physical PDF page markers."""
    from actions.policy_engine import infer_category_from_text
    if max_words < 1:
        raise ValueError('max_words must be positive')
    chunks, buffer = [], []
    page, section = 1, Path(filename).stem
    parent_heading = ''
    def flush():
        if not buffer:
            return
        words = ' '.join(buffer).split()
        for start in range(0, len(words), max_words):
            text = ' '.join(words[start:start+max_words])
            if not text:
                continue
            chunks.append({'id': hashlib.sha256(f'{filename}:{page}:{section}:{start}:{text}'.encode()).hexdigest()[:16],
                           'source': filename, 'page': page, 'section': section,
                           'category': infer_category_from_text(f'{filename} {section} {text}'), 'text': text})
        buffer.clear()
    for line in markdown_text.splitlines():
        marker = re.fullmatch(r'## Page (\d+)', line.strip())
        if marker:
            flush()
            page = int(marker[1])
        elif line.startswith('### '):
            flush()
            section = line[4:].strip()
            if re.match(r'^\d+\.\s', section):
                parent_heading = section
            elif re.match(r'^\d+\.\d+', section) and parent_heading:
                section = parent_heading + ' / ' + section
        elif line.strip():
            buffer.append(line.strip())
    flush()
    return chunks


def processed_markdown(report):
    pages = []
    for page in report.pages:
        lines = page.text.splitlines()
        # Contents and cover/version metadata are retained in the report, not searchable evidence.
        if any(line.strip().upper() in {'CONTENTS', 'TABLE OF CONTENTS'} for line in lines):
            continue
        output = []
        for line in lines:
            stripped = line.strip()
            if re.match(r'^Version Control', stripped, re.I):
                break
            if re.match(r'^(Version\b|Type:|Code:|Reg\. Office|CIN:|Emergys Solutions Private Limited|\(Formerly)', stripped, re.I):
                continue
            if 'Version Number' in stripped or 'Accessibility:' in stripped:
                continue
            heading = bool(re.match(r'^\d+(?:\.\d+)*\.?\s+[A-Z]', stripped)) and len(stripped.split()) <= 12
            heading |= bool(re.match(r'^[•●]\s+[A-Z]', stripped)) and len(stripped.split()) <= 10
            heading |= stripped in {'Purpose / Objective', 'Eligibility / Scope', 'Terms & Conditions / Rules'}
            output.append(('### ' if heading else '') + stripped)
        # Cover pages contain no substantive section headings.
        if page.number == 1 and not any(line.startswith('### ') for line in output) and report.page_count > 1:
            continue
        pages.append(f'## Page {page.number}\n\n' + '\n'.join(output))
    return '\n\n'.join(pages)


def prepare_pdf(filename, content):
    validate_filename(filename)
    if not 0 < len(content) <= MAX_BYTES:
        raise ValueError('PDF must contain between 1 byte and 20 MiB')
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / filename
        path.write_bytes(content)
        report = process_pdf(path)
    markdown = processed_markdown(report)
    chunks = chunk_markdown(markdown, filename)
    if not chunks:
        raise ValueError('PDF contains no usable policy text after processing')
    return {'filename': filename, 'content': content, 'markdown': markdown,
            'chunks': chunks, 'warnings': report.warnings, 'page_count': report.page_count}


def pdf_to_markdown(pdf_bytes):
    return prepare_pdf('policy.pdf', pdf_bytes)['markdown']


def publish(prepared, target, mode):
    if mode not in {'upsert', 'replace'}:
        raise ValueError('mode must be upsert or replace')
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = json.loads(target.read_text()) if target.exists() else {}
        previous = old.get('chunks', [])
        demo = bool(previous) and all(c['source'] == 'employee-handbook.md' and ('DEMO POLICY ONLY' in c['text'] or 'fictional examples' in c['text']) for c in previous)
        names = {p['filename'] for p in prepared}
        keep = mode == 'upsert' and not demo
        chunks = [c for c in previous if c['source'] not in names] if keep else []
        documents = {k:v for k,v in old.get('documents', {}).items() if k not in names} if keep else {}
        # Immutable content-addressed originals: publish index last, making it the commit point.
        for item in prepared:
            digest = hashlib.sha256(item['content']).hexdigest()
            relative = f'documents/{digest}.pdf'
            atomic_write(target.parent / relative, item['content'])
            atomic_write(target.parent / f'documents/{digest}.md', item['markdown'].encode())
            documents[item['filename']] = {'pdf_path': relative, 'page_count': item['page_count'], 'warnings': item['warnings'], 'sha256': digest}
            chunks.extend(item['chunks'])
        payload = {'version': 1, 'library': 'company', 'pipeline_version': 2, 'documents': documents, 'chunks': chunks}
        atomic_write(target, json.dumps(payload, indent=2, ensure_ascii=False).encode())
    return {'demo_removed': demo, 'total_chunks': len(chunks)}


def update_pdf(filename, content, mode='upsert', index=None):
    if mode not in {'upsert', 'replace'}:
        raise ValueError('mode must be upsert or replace')
    item = prepare_pdf(filename, content)
    target = Path(index) if index else index_path()
    result = publish([item], target, mode)
    from actions.policy_engine import intent_mapping
    intents = [k for k,v in intent_mapping().items() if v['source']==filename]
    return {'intents':intents, 'intent_mapping_status':'mapped' if intents else 'needs_review', 'filename': filename, 'mode': mode, **result, 'uploaded_chunks': len(item['chunks']),
            'page_count': item['page_count'], 'warnings': item['warnings']}


def rebuild_pdf_index(folder, index=None, mode='upsert', filenames=None):
    paths = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() == '.pdf' and (filenames is None or p.name in filenames))
    if not paths:
        raise ValueError('No PDFs found; existing library was preserved')
    prepared = []
    for path in paths:
        with path.open('rb') as stream:
            prepared.append(prepare_pdf(path.name, stream.read(MAX_BYTES+1)))
    result = publish(prepared, index or index_path(), mode)
    return [{**result, 'filename': p['filename'], 'uploaded_chunks': len(p['chunks']), 'warnings': p['warnings']} for p in prepared]


def main():
    parser = argparse.ArgumentParser(description='Process and atomically publish a PDF or folder')
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--mode', choices=['upsert', 'replace'], default='upsert')
    parser.add_argument('--index', type=Path, default=index_path())
    parser.add_argument('--registered-only', action='store_true', help='For folders, only PDFs in the reviewed intent registry')
    args = parser.parse_args()
    if args.pdf.is_dir():
        names = None
        if args.registered_only:
            names = {v['source'] for v in json.loads((ROOT/'data/policy_intents.json').read_text()).values()}
        result = rebuild_pdf_index(args.pdf, args.index, args.mode, names)
    else:
        with args.pdf.open('rb') as stream:
            result = update_pdf(args.pdf.name, stream.read(MAX_BYTES+1), args.mode, args.index)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
