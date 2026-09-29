#!/usr/bin/env python3
"""Build a runtime-ready FAQ answer map from the reviewed CSV artifact."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def split_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(';') if part.strip()]


def normalize(value: str) -> str:
    return ' '.join(value.casefold().split())


def build(source: Path, index_path: Path) -> dict:
    from hr_policy.engine import PolicyIndex, intent_mapping

    registry = intent_mapping()
    index = PolicyIndex(index_path)
    entries = []
    with source.open(encoding='utf-8-sig', newline='') as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            intent = row['intent'].strip()
            route = registry.get(intent)
            if route is None:
                raise ValueError(f'Row {row_number}: unknown intent {intent!r}')
            ranked = index.retrieve(row['question'], intent=intent, limit=1)
            if not ranked:
                raise ValueError(f'Row {row_number}: no evidence for {intent!r}')
            chunk = ranked[0][1]
            if chunk.source != route['source']:
                raise ValueError(f'Row {row_number}: evidence source does not match intent')
            answer = row['answer'].strip()
            entry_id = hashlib.sha256(f'{intent}\0{row["question"]}'.encode()).hexdigest()[:16]
            entries.append({
                'id': entry_id,
                'intent': intent,
                'question': row['question'].strip(),
                'utterances': split_values(row.get('utterances', '')),
                'answer': answer,
                'answer_mode': 'fixed',
                'tags': split_values(row.get('tags', '')),
                'enabled': row.get('enabled', '').strip().casefold() in {'yes', 'true', '1'},
                'source': route['source'],
                'mapped_sections': route.get('sections', []),
                'evidence': {
                    'chunk_id': chunk.id,
                    'page': chunk.page,
                    'section': chunk.section,
                    'quote': chunk.text,
                },
                # Exact answers can be auto-verified; paraphrases require human review.
                'answer_is_exact_quote': normalize(answer) in normalize(chunk.text),
            })
    return {
        'version': 1,
        'generated_from': str(source.relative_to(ROOT)),
        'policy_index': str(index_path.relative_to(ROOT)),
        'entries': entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/policies/policy_questions_and_answers.csv')
    parser.add_argument('--index', type=Path, default=ROOT / 'data/policies/policies.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/faq_answers.json')
    args = parser.parse_args()
    payload = build(args.source.resolve(), args.index.resolve())
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    exact = sum(entry['answer_is_exact_quote'] for entry in payload['entries'])
    print(f'Wrote {len(payload["entries"])} FAQ answers to {args.output} ({exact} exact quotes).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
