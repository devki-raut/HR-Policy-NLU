import hashlib
import json
import math
import os
import re
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

FAQ_PATH = DATA_DIR / "faq_answers.json"
EMBEDDINGS_PATH = DATA_DIR / "faq_embeddings.npz"
POLICY_PATH = DATA_DIR / "policies" / "policies.json"

MODEL_NAME = os.getenv(
    "FAQ_EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)

THRESHOLD = float(
    os.getenv("FAQ_EMBEDDING_THRESHOLD", "0.75")
)


MODEL = SentenceTransformer(MODEL_NAME)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_candidates():
    """Build semantic FAQ candidates from the reviewed JSON registry."""
    faq = _load_json(FAQ_PATH)

    rows = []

    for entry in faq.get("entries", []):
        if not entry.get("enabled", True):
            continue

        detailed_intent = entry.get("intent")
        routing_intent = entry.get("routing_intent")

        if not detailed_intent or not routing_intent:
            continue

        questions = [
            entry.get("question", ""),
            *entry.get("utterances", []),
        ]

        for question in questions:
            question = str(question).strip()

            if question:
                rows.append({
                    "intent": detailed_intent,
                    "routing_intent": routing_intent,
                    "question": question,
                    "entry": entry,
                })

    return rows


def _calculate_fingerprint(rows):
    """
    Create a fingerprint from the model configuration and all
    FAQ data that contributes to the embeddings.
    """

    fingerprint_data = {
        "model": MODEL_NAME,
        "faqs": [
            {
                "intent": row["intent"],
                "routing_intent": row["routing_intent"],
                "question": row["question"],
                "entry_id": row["entry"].get("id"),
                "answer": row["entry"].get("answer"),
                "source": row["entry"].get("source"),
                "evidence": row["entry"].get("evidence"),
            }
            for row in rows
        ],
    }

    serialized = json.dumps(
        fingerprint_data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()


def _compute_embeddings(rows):
    """Compute embeddings for all FAQ questions."""

    print(
        f"[FAQ] Computing embeddings for "
        f"{len(rows)} FAQ questions..."
    )

    embeddings = MODEL.encode(
        [row["question"] for row in rows],
        normalize_embeddings=True,
        show_progress_bar=True,
    )

    return np.asarray(embeddings, dtype=np.float32)


def _save_embedding_store(rows, embeddings, fingerprint):
    """Save FAQ rows and embeddings to NPZ and JSON files."""

    # 1. Save the efficient runtime embedding store
    np.savez_compressed(
        EMBEDDINGS_PATH,
        embeddings=embeddings,
        fingerprint=np.array(fingerprint),
        model=np.array(MODEL_NAME),
        rows=np.array(
            json.dumps(rows, ensure_ascii=False),
            dtype=object,
        ),
    )

    # 2. Create a human-readable JSON export
    json_path = EMBEDDINGS_PATH.with_suffix(".json")

    json_data = {
        "model": MODEL_NAME,
        "fingerprint": fingerprint,
        "embedding_dimension": int(embeddings.shape[1]),
        "entries": [
            {
                "intent": row["intent"],
                "routing_intent": row["routing_intent"],
                "question": row["question"],
                "embedding": embedding.tolist(),
            }
            for row, embedding in zip(rows, embeddings)
        ],
    }

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            json_data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"[FAQ] Embedding store updated: {EMBEDDINGS_PATH}")
    print(f"[FAQ] JSON export updated: {json_path}")


def _load_embedding_store():
    """Load the local embedding store."""

    if not EMBEDDINGS_PATH.exists():
        return None

    try:
        store = np.load(
            EMBEDDINGS_PATH,
            allow_pickle=True,
        )

        fingerprint = str(
            store["fingerprint"].item()
        )

        model = str(
            store["model"].item()
        )

        rows = json.loads(
            str(store["rows"].item())
        )

        embeddings = store["embeddings"]

        return {
            "fingerprint": fingerprint,
            "model": model,
            "rows": rows,
            "embeddings": embeddings,
        }

    except Exception as exc:
        print(
            f"[FAQ] Failed to load embedding store: {exc}"
        )

        return None


def _load_or_build_embedding_store(rows):
    """
    Load cached embeddings when the FAQ/model fingerprint matches.

    Otherwise recompute the embeddings and replace the store.
    """

    fingerprint = _calculate_fingerprint(rows)

    cached = _load_embedding_store()

    if cached:
        if (
            cached["fingerprint"] == fingerprint
            and cached["model"] == MODEL_NAME
            and len(cached["rows"]) == len(rows)
            and cached["embeddings"].shape[0] == len(rows)
        ):
            print(
                f"[FAQ] Loaded cached embeddings: "
                f"{len(rows)} questions"
            )

            return (
                cached["rows"],
                cached["embeddings"],
            )

        print(
            "[FAQ] Embedding store is stale. "
            "Recomputing embeddings..."
        )

    else:
        print(
            "[FAQ] No embedding store found. "
            "Computing embeddings..."
        )

    embeddings = _compute_embeddings(rows)

    _save_embedding_store(
        rows,
        embeddings,
        fingerprint,
    )

    return rows, embeddings


# ---------------------------------------------------------
# Initialize FAQ store
# ---------------------------------------------------------

FAQ_ROWS = _build_candidates()

if not FAQ_ROWS:
    raise RuntimeError(
        "No enabled FAQ entries with routing_intent were found."
    )


FAQ_ROWS, FAQ_EMBEDDINGS = _load_or_build_embedding_store(
    FAQ_ROWS
)


def find_faq(
    question: str,
    routing_intent: str,
    threshold: float = THRESHOLD,
):
    """
    Match a user question against FAQs belonging
    to the DIET routing intent.
    """

    candidates = [
        (i, row)
        for i, row in enumerate(FAQ_ROWS)
        if row["routing_intent"] == routing_intent
    ]

    if not candidates:
        return None

    indexes = [i for i, _ in candidates]

    # Only the user's query is embedded at runtime.
    query_embedding = MODEL.encode(
        [question],
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0]

    scores = np.dot(
        FAQ_EMBEDDINGS[indexes],
        query_embedding,
    )

    best_position = int(np.argmax(scores))
    best_score = float(scores[best_position])

    best_row = candidates[best_position][1]

    print(
        f"[FAQ] routing_intent={routing_intent}"
    )
    print(
        f"[FAQ] candidates={len(candidates)}"
    )
    print(
        f"[FAQ] best_match={best_row['question']}"
    )
    print(
        f"[FAQ] score={best_score:.4f}"
    )
    print(
        f"[FAQ] threshold={threshold}"
    )

    if best_score < threshold:
        return None

    entry = best_row["entry"]

    evidence = entry.get("evidence") or {}

    return {
        "intent": best_row["intent"],
        "routing_intent": best_row["routing_intent"],
        "matched_question": best_row["question"],
        "score": best_score,
        "answer": entry.get("answer", ""),
        "source": entry.get("source", ""),
        "entry_id": entry.get("id", ""),
        "evidence": {
            "chunk_id": evidence.get("chunk_id", ""),
            "page": evidence.get("page"),
            "section": evidence.get("section", ""),
            "quote": evidence.get("quote", ""),
        },
        "answer_mode": entry.get(
            "answer_mode",
            "fixed",
        ),
    }

_RETRIEVAL_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "for", "from", "get", "how", "i", "in", "is", "it", "many", "of", "on",
    "or", "the", "their", "to", "what", "when", "who", "with",
}


def _retrieval_tokens(value):
    return {
        token for token in re.findall(r"[a-z0-9]+", str(value).casefold())
        if len(token) > 1 and token not in _RETRIEVAL_STOP_WORDS
    }


def retrieve_policy_chunks(question, faq_result, limit=5):
    """Return mapped evidence first, then related chunks from the same policy."""
    if limit < 1 or not POLICY_PATH.exists():
        return []
    chunks = _load_json(POLICY_PATH).get("chunks", [])
    source = faq_result.get("source")
    candidates = [chunk for chunk in chunks if chunk.get("source") == source]
    if not candidates:
        return []

    mapped_id = (faq_result.get("evidence") or {}).get("chunk_id")
    mapped = next((chunk for chunk in candidates if chunk.get("id") == mapped_id), None)
    query = _retrieval_tokens(" ".join((
        question,
        faq_result.get("matched_question", ""),
        faq_result.get("answer", ""),
        faq_result.get("intent", "").replace("_", " "),
    )))
    terms = [
        _retrieval_tokens(f"{chunk.get('section', '')} {chunk.get('text', '')}")
        for chunk in candidates
    ]
    count = max(len(candidates), 1)
    frequencies = {term: sum(term in document for document in terms) for term in query}
    ranked = []
    for chunk, document in zip(candidates, terms):
        if chunk.get("id") == mapped_id:
            continue
        score = sum(
            math.log((count + 1) / (frequencies[term] + 1)) + 1
            for term in query & document
        ) / math.sqrt(max(len(document), 1))
        ranked.append((score, chunk))
    ranked.sort(key=lambda item: (item[0], item[1].get("id", "")), reverse=True)

    selected = []
    if mapped:
        selected.append({**mapped, "mapped": True, "retrieval_score": 1.0})
    for score, chunk in ranked:
        if len(selected) >= limit:
            break
        selected.append({**chunk, "mapped": False, "retrieval_score": round(score, 4)})
    return selected
