"""Atomic policy updates shared by the local importer and HTTP service (Linux)."""
import argparse
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import tempfile

from hr_policy.engine import ingest
from hr_policy.processing import MAX_BYTES, PDFProcessingError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / 'artifacts/policies.json'


def index_path():
    return Path(os.getenv('POLICY_INDEX', str(DEFAULT_INDEX))).resolve()


def update_pdf(filename, content, mode='upsert', index=None):
    """First company upload replaces demo data; later upserts replace by filename."""
    if mode not in {'upsert', 'replace'}:
        raise ValueError('mode must be upsert or replace')
    if not filename or '/' in filename or '\\' in filename or Path(filename).suffix.lower() != '.pdf':
        raise ValueError('Provide a plain PDF filename, without directory components')
    if len(filename) > 200:
        raise ValueError('Filename must be at most 200 characters')
    if not content or len(content) > MAX_BYTES:
        raise ValueError('PDF must contain between 1 byte and 20 MiB')
    if not content.startswith(b'%PDF-'):
        raise ValueError('The file is not a PDF')
    target = Path(index) if index else index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Parse before acquiring the update lock; failed parsing never changes active data.
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, filename).write_bytes(content)
        try:
            chunks = ingest(folder, Path(folder, 'parsed.json'))
        except PDFProcessingError:
            raise
        except Exception as exc:
            raise ValueError('Unable to extract PDF text. Use a valid, unencrypted text PDF; scanned PDFs need OCR.') from exc
    if not chunks:
        raise ValueError('PDF contains no usable policy text')
    with target.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = json.loads(target.read_text()) if target.exists() else {}
        previous = old.get('chunks', [])
        # Only the known sample corpus is automatically discarded.
        is_demo = bool(previous) and all(c['source'] == 'employee-handbook.md' and ('DEMO POLICY ONLY' in c['text'] or 'fictional examples' in c['text']) for c in previous)
        keep = mode == 'upsert' and not is_demo
        merged = [c for c in previous if c['source'] != filename] if keep else []
        merged.extend(asdict(c) for c in chunks)
        payload = {'version': 1, 'library': 'company', 'chunks': merged}
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, suffix='.json', delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(payload, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {'filename': filename, 'mode': mode, 'demo_removed': is_demo,
            'uploaded_chunks': len(chunks), 'total_chunks': len(merged)}


def main():
    parser = argparse.ArgumentParser(description='Import a local PDF into the active policy library')
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--mode', choices=['upsert', 'replace'], default='upsert')
    parser.add_argument('--index', type=Path, default=index_path())
    args = parser.parse_args()
    with args.pdf.open('rb') as stream:
        content = stream.read(MAX_BYTES + 1)
    print(json.dumps(update_pdf(args.pdf.name, content, args.mode, args.index), indent=2))

if __name__ == '__main__':
    main()
