"""PDF validation, extraction, normalization and page-level quality reporting.

This layer has no dependency on Rasa, retrieval or policy storage.
"""
import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import unicodedata

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 500


class PDFProcessingError(ValueError):
    """A document cannot be processed safely into usable policy text."""


@dataclass
class ProcessedPage:
    number: int
    text: str
    word_count: int


@dataclass
class ProcessedPDF:
    source: str
    page_count: int
    pages: list[ProcessedPage]
    warnings: list[str]


def normalize_text(text):
    text = unicodedata.normalize('NFKC', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = ''.join(c for c in text if c in '\n\t' or unicodedata.category(c) != 'Cc')
    return '\n'.join(re.sub(r'[^\S\n]+', ' ', line).strip() for line in text.splitlines())


def clean_pages(raw):
    # Remove only repeated page-edge lines, preserving repeated policy body text.
    edges = Counter()
    for page in raw:
        lines = [s.strip() for s in page.splitlines() if s.strip()]
        edges.update(set(lines[:1] + lines[-1:]))
    repeated = {s for s, n in edges.items() if len(raw) > 1 and n >= max(2, math.ceil(len(raw) * .6))}
    cleaned = []
    for page in raw:
        lines = [re.sub(r"[ \t]+", " ", s).strip() for s in page.splitlines()]
        nonempty = [i for i, s in enumerate(lines) if s]
        edge_ids = set(nonempty[:1] + nonempty[-1:])
        cleaned.append("\n".join(s for i, s in enumerate(lines) if not (i in edge_ids and (s in repeated or re.fullmatch(r"(?:Page\s+)?\d+(?:\s+of\s+\d+)?", s, re.I)))))
    return cleaned


def process_pdf(path, max_pages=MAX_PAGES):
    """Read a text PDF, retaining empty pages so citations never shift.

    Empty individual pages produce warnings, not silent renumbering. No OCR,
    table reconstruction or eligibility interpretation is performed.
    """
    from pypdf import PdfReader
    path = Path(path)
    if max_pages < 1:
        raise ValueError('max_pages must be positive')
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise PDFProcessingError('PDF must contain between 1 byte and 20 MiB')
    try:
        with path.open('rb') as stream:
            if stream.read(5) != b'%PDF-':
                raise PDFProcessingError('The file is not a PDF')
            stream.seek(0)
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise PDFProcessingError('Encrypted PDFs are unsupported; upload an unencrypted copy')
            if len(reader.pages) > max_pages:
                raise PDFProcessingError(f'PDF exceeds the {max_pages}-page limit')
            raw = [normalize_text(page.extract_text() or '') for page in reader.pages]
    except PDFProcessingError:
        raise
    except Exception as exc:
        raise PDFProcessingError('Unable to read PDF; the document may be malformed') from exc
    cleaned = clean_pages(raw)
    warnings = []
    result = []
    for number, (original, text) in enumerate(zip(raw, cleaned), 1):
        # Repeated edge detection must never erase an entire nonempty page.
        if original.strip() and not text.strip():
            text = original
            warnings.append(f'Page {number}: retained text because edge cleaning removed all content')
        if not text.strip():
            warnings.append(f'Page {number}: no extractable text; blank or scanned page (OCR may be required)')
        result.append(ProcessedPage(number, text, len(text.split())))
    if not any(page.word_count for page in result):
        raise PDFProcessingError('PDF contains no extractable text; scanned PDFs require OCR')
    return ProcessedPDF(path.name, len(result), result, warnings)


def main():
    parser = argparse.ArgumentParser(description='Preview processed PDF text without updating the policy library')
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--output', type=Path, help='Optional JSON report path; defaults to stdout')
    args = parser.parse_args()
    payload = json.dumps(asdict(process_pdf(args.pdf)), indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
    else:
        print(payload)


if __name__ == '__main__':
    main()
