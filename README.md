# HR Policy NLU

Rasa classifies HR questions; a local retrieval engine returns policy excerpts with filename, section, page and chunk references. The unified FastAPI server hosts chat, PDF upload and the Teams personal-tab UI. Answers default to cited excerpts. Optional remote embeddings add semantic matching; optional model-assisted evidence selection is validated against the retrieved text.

## Setup and run

Requires Python 3.10 on Linux/WSL. From this directory:

```bash
make deploy-setup
make local-start
```

Setup installs dependencies, validates configuration and runs tests. It creates credentials, the sample index and a trained model only when missing, preserving existing policies and models.

- Chat: http://localhost:8000/
- Swagger: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json

```bash
make local-status
make logs
make local-restart
make local-stop
```

Individual logs: `./scripts/logs.sh api` (or `rasa` / `actions`). Logs and process records are in `artifacts/local/`. Services are detached local processes, not systemd: no automatic reboot startup, crash supervision or log rotation. The launcher reuses pre-existing services but only stops processes it started. Do not run deployment commands concurrently.

For foreground debugging, run `make actions`, `make serve` and `make api` in separate terminals. Rasa listens on 5005, actions on 5055, and the unified UI/API on 8000.

## Policies and training

Sample policies are fictional. Upload approved PDFs through Swagger, or import locally:

```bash
.venv-runtime/bin/python -m hr_policy.store /absolute/path/company-policy.pdf
```

The first company upload removes the known demo corpus. Subsequent uploads replace by filename; `--mode replace` replaces the full library. Failed extraction preserves the active index. Updates need no retraining: actions read the index for each question. Original PDFs and processed Markdown are stored immutably under `artifacts/documents/`; the active index determines which files can be downloaded.

For PDF/DOCX/TXT/Markdown folder ingestion, use `.venv-runtime/bin/python -m hr_policy.engine ingest policies/company`. This replaces the index; do not run it concurrently with uploads. Scanned PDFs require OCR. PDF citations retain page numbers; DOCX/TXT/Markdown use logical page 1. Chunks are capped at 180 words. Category tagging is keyword based; PDF heading extraction and complex document layouts need review.

After editing `data/nlu.yml` or intents, run `make train`, then restart Rasa. `make ingest` deliberately restores sample policies. Export the same absolute `POLICY_INDEX` for API and actions if overriding the default; scripts do not load `.env` automatically.

## Documentation and Teams

- [API reference and UAT checklist](API.md): endpoints, uploads, authentication, examples and error responses.
- [Teams packaging](teams_app/README.md): build a personal-tab ZIP pointing at the unified API's HTTPS origin. No separate Teams server exists. Installation requires HTTPS hosting and tenant permission; local hosting does not install the app in Teams.

Before exposing company policies, configure employee authentication and policy-write authorization. The optional shared `HR_API_KEY` is for API access, not employee roles. Keep Rasa and action-server ports private.

## Layout

| Location | Purpose |
| --- | --- |
| `api/` | FastAPI server and chat UI in `static/` |
| `hr_policy/` | Ingestion, retrieval, updates and Rasa custom actions |
| `data/` | NLU examples and conversation rules |
| `policies/` | Source policy documents |
| `teams_app/` | Teams package builder and metadata |
| `scripts/` | Setup, service management, logs and smoke check |
| `tests/` | Automated checks |
| `artifacts/`, `models/` | Generated index, logs and trained models |

Rasa YAML files stay at the root for standard CLI compatibility. Runtime artifacts, company documents and credentials are gitignored.

## Verification and limits

Run `make test` and `make validate`. With the original demo corpus and action server running, `.venv-runtime/bin/python scripts/smoke.py` tests six trained-model questions; its expected values are demo-specific.

Low-confidence intents and ambiguous retrieval ask for clarification. Missing evidence refers users to HR. Questions are standalone; follow-ups need the topic. Lexical retrieval can miss synonyms or return related excerpts that do not fully answer the question. It does not resolve conflicting policies, determine individual eligibility, or enforce region-specific access. Evaluate company questions before rollout.

PDF preprocessing lives in `hr_policy/processing.py` and is shared by uploads and ingestion. Preview a PDF without publishing it using `.venv-runtime/bin/python -m hr_policy.processing /path/to/policy.pdf`; see [processing details](API.md#pdf-processing-layer).

## Document intents and RAG workflow

The five supplied company PDFs have six reviewed intents each in `data/policy_intents.json`. This maps each intent to an exact PDF filename and section patterns. `data/policy_nlu.yml` and `data/policy_rules.yml` are generated from that registry; `data/nlu.yml` retains general HR intents.

```bash
make sync-intents        # After reviewing/editing the registry
make reindex             # Atomically process only registered PDFs in policies/pdfs/
make train               # Train Rasa DIET on the updated intents
make local-restart
.venv-runtime/bin/python scripts/evaluate_policies.py
```

Adding a new PDF does not automatically train new intents. Uploads return the matching `intents` and `intent_mapping_status`; `needs_review` means add 5–6 meaningful intents and examples, map them to real document sections, sync and retrain. Do not force six intents onto a document with fewer distinct topics. Updating the text of a mapped PDF needs reprocessing but no retraining unless topic/section mappings change.

Rasa classifies the question first. Its custom action scopes retrieval to the mapped PDF and sections, selects up to three chunks, and returns citations and source metadata together. The API passes those exact sources through; it no longer retrieves a second, unrelated chunk. `POST /nlu/parse` exposes Rasa’s intent and confidence; `GET /policies/intents` exposes reviewed mappings. Keep Rasa’s `--enable-api` port private.

The active library contains 80 chunks from the five registered PDFs. Previously generated test PDFs in `policies/pdfs/` are preserved on disk but excluded by `make reindex`. Tests now use isolated storage. The pre-migration index is backed up at `artifacts/backups/policies-before-rag.json`.

The core environment is `.venv-runtime/`. The old `.venv/` is preserved, but has incompatible Rasa/vLLM dependencies and is no longer used by Make or the launcher. Do not install vLLM or SGLang into the core environment. Optional model serving runs independently; `scripts/local_model_service.py` forwards to `MODEL_BASE_URL` and reports unhealthy when the backend is down. It no longer pretends a stub is a model.

Generation requires `USE_POLICY_GENERATION=1` and a reachable `POLICY_GENERATION_URL`. It only displays exact model-selected quotes verified against retrieved evidence, with application-generated citations; unsupported output falls back to full retrieved excerpts. This intentionally avoids unsupported free-form paraphrases. Optional embeddings use an OpenAI-compatible `POLICY_EMBEDDING_URL`; without it, retrieval uses BM25-style lexical scoring plus explicit synonym expansion. Embeddings and the index are cached per process and refreshed when the index file changes. Dense retrieval requires independent validation on your deployed embedding model.

For Teams deployment instructions see [api/TEAMS_DEPLOYMENT.md](api/TEAMS_DEPLOYMENT.md). Local tab checks do not constitute live Teams tenant UAT.
