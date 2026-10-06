#!/usr/bin/env python3
"""Evaluate every question in the HR Policy testing workbook.

The workbook's ANSWER cells contain historical chatbot output, so they are
reported as the baseline rather than treated as authoritative ground truth.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = ROOT / "artifacts" / "NLU HR Policy - Testing.xlsx"
DEFAULT_OUTPUT = ROOT / "artifacts" / "evaluations"
RASA_URL = "http://127.0.0.1:5005"
NO_MATCH_TEXT = "couldn't find a confident faq match"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

SHEET_ROUTES = {
    "Maternity & Paternity Leave": {"policy_maternity", "policy_paternity"},
    "Leave Policy": {"policy_general_leave"},
    "Employee Referral Bonus Policy": {"policy_referral"},
    "Bereavement Leave Policy": {"policy_bereavement"},
    "POSH Policy": {"policy_posh"},
}


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference).group(0)
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value


def read_workbook(path: Path) -> list[dict]:
    ns = {"m": MAIN_NS, "r": REL_NS}
    records: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
                for item in root
            ]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relation_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relations = {item.attrib["Id"]: item.attrib["Target"] for item in relation_root}
        for sheet in workbook.find("m:sheets", ns):
            title = sheet.attrib["name"]
            target = relations[sheet.attrib[f"{{{REL_NS}}}id"]]
            worksheet_path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
            worksheet = ET.fromstring(archive.read(worksheet_path))
            rows: list[dict[int, str]] = []
            for row in worksheet.findall(".//m:sheetData/m:row", ns):
                values: dict[int, str] = {}
                for cell in row.findall("m:c", ns):
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find("m:v", ns)
                    if cell_type == "inlineStr":
                        value = "".join(
                            node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t")
                        )
                    elif value_node is None:
                        value = ""
                    elif cell_type == "s":
                        value = shared[int(value_node.text)]
                    else:
                        value = value_node.text or ""
                    values[_column_number(cell.attrib["r"])] = value.strip()
                rows.append(values)
            for row_number, values in enumerate(rows[1:], start=2):
                question = values.get(1, "").strip()
                if question:
                    records.append(
                        {
                            "sheet": title,
                            "row": row_number,
                            "question": question,
                            "baseline_answer": values.get(2, "").strip(),
                        }
                    )
    return records


def post(path: str, payload: dict) -> dict | list:
    request = urllib.request.Request(
        RASA_URL + path,
        json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def evidence_registry() -> dict[str, dict]:
    payload = json.loads((ROOT / "data/faq_answers.json").read_text(encoding="utf-8"))
    chunks_payload = json.loads(
        (ROOT / "data/policies/policies.json").read_text(encoding="utf-8")
    )
    chunks = {chunk["id"]: chunk for chunk in chunks_payload["chunks"]}
    registry = {}
    for entry in payload["entries"]:
        evidence = entry.get("evidence") or {}
        chunk = chunks.get(evidence.get("chunk_id"))
        registry[entry["id"]] = {
            "intent": entry.get("intent"),
            "valid": bool(
                chunk
                and chunk.get("source") == entry.get("source")
                and chunk.get("text") == evidence.get("quote")
            ),
        }
    return registry


def exact_training_overlaps(records: list[dict]) -> list[dict]:
    """Find verbatim normalized overlap with FAQ questions or utterances."""
    payload = json.loads((ROOT / "data/faq_answers.json").read_text(encoding="utf-8"))
    phrases = defaultdict(list)
    for entry in payload["entries"]:
        for phrase in [
            entry.get("question", ""),
            *entry.get("utterances", []),
            *entry.get("matching_phrases", []),
        ]:
            normalized = " ".join(re.findall(r"[a-z0-9]+", str(phrase).casefold()))
            if normalized:
                phrases[normalized].append(entry["id"])
    overlaps = []
    for record in records:
        normalized = " ".join(
            re.findall(r"[a-z0-9]+", record["question"].casefold())
        )
        if normalized in phrases:
            overlaps.append(
                {
                    "sheet": record["sheet"],
                    "row": record["row"],
                    "question": record["question"],
                    "faq_ids": phrases[normalized],
                }
            )
    return overlaps


def evaluate(records: list[dict]) -> list[dict]:
    registry = evidence_registry()
    results = []
    for index, record in enumerate(records, start=1):
        question = record["question"]
        parse = post("/model/parse", {"text": question})
        route = (parse.get("intent") or {}).get("name")
        route_confidence = float((parse.get("intent") or {}).get("confidence", 0))
        messages = post(
            "/webhooks/rest/webhook",
            {"sender": f"workbook-eval-{uuid4()}", "message": question},
        )
        answer = "\n".join(message.get("text", "") for message in messages if message.get("text"))
        customs = [
            message["custom"]
            for message in messages
            if isinstance(message.get("custom"), dict)
        ]
        metadata = next(
            (item for item in customs if item.get("type") == "policy_answer_metadata"),
            {},
        )
        faq_id = metadata.get("faq_id")
        expected_routes = SHEET_ROUTES.get(record["sheet"], set())
        baseline_no_match = NO_MATCH_TEXT in record["baseline_answer"].casefold()
        current_no_match = metadata.get("status") == "no_confident_match" or not faq_id
        results.append(
            {
                "index": index,
                **record,
                "expected_routes": sorted(expected_routes),
                "predicted_route": route,
                "routing_confidence": round(route_confidence, 6),
                "routing_correct": route in expected_routes,
                "faq_matched": not current_no_match,
                "faq_id": faq_id,
                "detailed_intent": metadata.get("detailed_intent"),
                "faq_score": metadata.get("score"),
                "matched_question": metadata.get("matched_question"),
                "citation_present": "Source:" in answer,
                "evidence_valid": registry.get(faq_id, {}).get("valid", False),
                "baseline_no_match": baseline_no_match,
                "baseline_had_answer": bool(record["baseline_answer"]) and not baseline_no_match,
                "current_answer": answer,
            }
        )
    return results


def percentage(value: int, total: int) -> float:
    return round(100 * value / total, 2) if total else 0.0


def metrics(rows: list[dict]) -> dict:
    total = len(rows)
    return {
        "total": total,
        "routing_correct": sum(row["routing_correct"] for row in rows),
        "routing_accuracy_percent": percentage(
            sum(row["routing_correct"] for row in rows), total
        ),
        "faq_matched": sum(row["faq_matched"] for row in rows),
        "faq_match_coverage_percent": percentage(sum(row["faq_matched"] for row in rows), total),
        "citation_coverage_percent": percentage(
            sum(row["citation_present"] for row in rows), total
        ),
        "valid_evidence_percent": percentage(sum(row["evidence_valid"] for row in rows), total),
        "baseline_no_match": sum(row["baseline_no_match"] for row in rows),
        "current_no_match": sum(not row["faq_matched"] for row in rows),
    }


def write_outputs(
    results: list[dict], output_dir: Path, workbook: Path, overlaps: list[dict]
) -> dict:
    grouped = defaultdict(list)
    for row in results:
        grouped[row["sheet"]].append(row)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workbook": str(workbook),
        "method": {
            "answer_cells": "historical baseline output; not used as ground truth",
            "routing": "predicted broad policy route compared with workbook sheet",
            "faq_coverage": "response contains a confident FAQ identifier",
            "evidence_validity": "FAQ source, chunk id and quote match the current policy index",
            "exact_training_question_overlaps": len(overlaps),
        },
        "training_question_overlaps": overlaps,
        "summary": metrics(results),
        "by_sheet": {sheet: metrics(rows) for sheet, rows in grouped.items()},
        "failures": [
            row
            for row in results
            if not row["routing_correct"] or not row["faq_matched"] or not row["evidence_valid"]
        ],
        "results": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "testing-workbook-evaluation.json"
    csv_path = output_dir / "testing-workbook-evaluation.csv"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    columns = [key for key in results[0] if key not in {"expected_routes"}]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in results:
            writer.writerow({key: row.get(key) for key in columns})
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", nargs="?", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    records = read_workbook(args.workbook.resolve())
    overlaps = exact_training_overlaps(records)
    report = write_outputs(
        evaluate(records), args.output_dir.resolve(), args.workbook.resolve(), overlaps
    )
    print(json.dumps({"summary": report["summary"], "by_sheet": report["by_sheet"], "failures": [{"sheet": row["sheet"], "row": row["row"], "question": row["question"], "route": row["predicted_route"], "faq": row["faq_id"]} for row in report["failures"]]}, indent=2))


if __name__ == "__main__":
    main()
