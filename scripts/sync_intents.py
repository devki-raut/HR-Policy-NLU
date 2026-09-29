"""Generate Rasa policy NLU/rules from the reviewed JSON registries."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main():
    mapping = json.loads((DATA / "policy_intents.json").read_text(encoding="utf-8"))
    faq = json.loads((DATA / "faq_answers.json").read_text(encoding="utf-8"))

    # policy_intents.json contains only broad routing intents for DIET.
    examples_by_intent = {
        name: list(item.get("examples", []))
        for name, item in mapping.items()
    }

    for entry in faq.get("entries", []):
        if not entry.get("enabled", True):
            continue
        intent = entry.get("routing_intent")
        if intent not in examples_by_intent:
            continue
        examples_by_intent[intent].extend([
            entry.get("question", ""),
            *entry.get("utterances", []),
        ])

    nlu = ['version: "3.1"', "nlu:"]
    rules = ['version: "3.1"', "rules:"]

    for name in mapping:
        seen = set()
        examples = []
        for example in examples_by_intent[name]:
            example = str(example).strip()
            key = example.casefold()
            if example and key not in seen:
                seen.add(key)
                examples.append(example)

        nlu.extend([f"- intent: {name}", "  examples: |"])
        nlu.extend(f"    - {example}" for example in examples)

        rules.extend([
            f"- rule: Route {name}",
            "  steps:",
            f"    - intent: {name}",
            "    - action: action_answer_policy",
        ])

    (DATA / "policy_rules_nlu.yml").write_text(
        "\n".join(nlu) + "\n", encoding="utf-8"
    )
    (DATA / "rules_policy.yml").write_text(
        "\n".join(rules) + "\n", encoding="utf-8"
    )

    domain_path = ROOT / "domain.yml"
    domain = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    general_intents = [
        "greet", "goodbye", "ask_leave", "ask_remote_work", "ask_benefits",
        "ask_expenses", "ask_conduct", "ask_onboarding", "out_of_scope", "nlu_fallback",
    ]
    domain["intents"] = general_intents + list(mapping.keys())
    domain_path.write_text(
        yaml.safe_dump(domain, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    # Remove exact duplicates from general NLU. Policy-specific questions are
    # intentionally not included in ask_leave/ask_conduct examples.
    base_path = DATA / "nlu_general.yml"
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    specific = {
        str(q).strip().casefold()
        for values in examples_by_intent.values()
        for q in values
        if str(q).strip()
    }

    for entry in base.get("nlu", []):
        if "examples" not in entry:
            continue
        kept = []
        for line in entry["examples"].splitlines():
            value = line.lstrip("- ").strip().casefold()
            if value and value not in specific:
                kept.append(line)
        entry["examples"] = "\n".join(kept) + ("\n" if kept else "")

    base_path.write_text(
        yaml.safe_dump(base, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    print(f"Synchronized {len(mapping)} broad policy routing intents from {len(faq.get('entries', []))} FAQ entries.")


if __name__ == "__main__":
    main()
