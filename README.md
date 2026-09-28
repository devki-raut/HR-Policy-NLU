# HR Policy NLU

Rasa classifies HR questions; a local retrieval engine returns policy excerpts with filename, section, page and chunk references. The unified FastAPI server hosts chat, PDF upload and the Teams personal-tab UI. Answers are extractive: no LLM, embeddings or external model API is used.

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
.venv/bin/python -m hr_policy.store /absolute/path/company-policy.pdf
```

The first company upload removes the known demo corpus. Subsequent uploads replace by filename; `--mode replace` replaces the full library. Failed extraction preserves the active index. Updates need no retraining: actions read the index for each question. Original uploaded PDFs are not retained.

For PDF/DOCX/TXT/Markdown folder ingestion, use `.venv/bin/python -m hr_policy.engine ingest policies/company`. This replaces the index; do not run it concurrently with uploads. Scanned PDFs require OCR. PDF citations retain page numbers; DOCX/TXT/Markdown use logical page 1. Chunks are capped at 180 words. Category tagging is keyword based; PDF heading extraction and complex document layouts need review.

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

Run `make test` and `make validate`. With the original demo corpus and action server running, `.venv/bin/python scripts/smoke.py` tests six trained-model questions; its expected values are demo-specific.

Low-confidence intents and ambiguous retrieval ask for clarification. Missing evidence refers users to HR. Questions are standalone; follow-ups need the topic. Lexical retrieval can miss synonyms or return related excerpts that do not fully answer the question. It does not resolve conflicting policies, determine individual eligibility, or enforce region-specific access. Evaluate company questions before rollout.

PDF preprocessing lives in `hr_policy/processing.py` and is shared by uploads and ingestion. Preview a PDF without publishing it using `.venv/bin/python -m hr_policy.processing /path/to/policy.pdf`; see [processing details](API.md#pdf-processing-layer).
