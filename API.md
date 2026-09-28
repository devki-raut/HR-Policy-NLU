# Unified FastAPI service

This service hosts the browser/Teams chat UI at `/`, static assets at `/static`, policy administration and Rasa chat on port 8000. Rasa (5005) and its action server (5055) still run separately. No retraining is needed after PDF uploads: the action reads the latest index on every question.

## Start

From the project root, install dependencies with `make install`. Run each command in its own terminal:

```bash
make actions
make serve
make api
```

Open http://localhost:8000/docs to select a local PDF with a file picker, upload it, and try chat. The OpenAPI schema is at `/openapi.json`.

For local testing with `HR_API_KEY` enabled, enter your key in the chat page’s optional authentication field. The UI uses the protected `/chat` endpoint directly.

## Routes

| Method | Route | Input / result |
| --- | --- | --- |
| GET | `/health` | API liveness and whether an index exists; does not verify Rasa readiness |
| GET | `/policies` | Active filenames and chunk counts |
| POST | `/policies/upload` | Multipart `file` (PDF), `mode` (`upsert` or `replace`) |
| POST | `/chat` | JSON `sender`, `message`; returns joined `answer` and original Rasa `messages` |
| POST | `/ask` | Multipart `sender`, `message`, optional PDF `file`, optional `mode`; upload then chat |

## Replace demo policies with a PDF from your computer

```bash
curl http://localhost:8000/policies/upload \
  -F 'file=@/absolute/path/company-policy.pdf' \
  -F 'mode=upsert'
```

The first upload removes the known fictional demo corpus. Later `upsert` uploads preserve other documents and replace the previous document with the **same filename**. Renaming a document adds a new document; it does not remove the old version. Use `mode=replace` to replace the entire active library with this PDF. Previously indexed non-demo documents are preserved by upsert.

Original PDFs and processed Markdown are retained under content-addressed paths in `artifacts/documents/`. The index stores the active filename-to-document mapping; old versions are retained for recovery but are not publicly listed or downloadable through the active-document route. PDF headings are not reliably extracted; citations retain the filename and page number.

Local CLI import uses exactly the same update behavior:

```bash
.venv-runtime/bin/python -m hr_policy.store /absolute/path/company-policy.pdf
.venv-runtime/bin/python -m hr_policy.store /absolute/path/new-handbook.pdf --mode replace
```

Do not run `make ingest` after uploading company policies unless you intentionally want to restore the demo corpus. The legacy `engine ingest` command replaces the index independently; do not run it concurrently with API/local-import updates.

## Chat

```bash
curl http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"sender":"employee-123","message":"How many days of annual leave do I get?"}'
```

## Upload and ask in one request

```bash
curl http://localhost:8000/ask \
  -F 'sender=employee-123' \
  -F 'message=How many days of annual leave do I get?' \
  -F 'file=@/absolute/path/company-policy.pdf' \
  -F 'mode=replace'
```

`/ask` without `file` is multipart chat. Uploads change the **shared library for every employee**, not a private per-user attachment. Upload publication completes before chat: if Rasa fails, the PDF remains published and the error response includes `policy_update`. Concurrent updates may become visible before the answer is generated.

## Configuration

| Environment variable | Default | Use |
| --- | --- | --- |
| `POLICY_INDEX` | Project `artifacts/policies.json` | Set the same absolute path for FastAPI and the action server |
| `RASA_URL` | `http://127.0.0.1:5005` | Internal Rasa address |
| `HR_API_KEY` | Unset | When set, requires `X-API-Key` for chat and policy routes |

For example, export a secret `HR_API_KEY` before `make api`, then include `-H 'X-API-Key:YOUR_KEY'` in requests. The local default binds only to loopback. Before sharing this service, configure authentication and restrict policy-write access to administrators at your gateway; the built-in shared key does not implement user roles. Place an HTTP request-size limit at the gateway too: the application rejects files above 20 MiB after multipart parsing. PDFs must have extractable text; OCR and password-protected PDFs are not supported. Parsing runs in a worker thread; hostile or resource-heavy PDFs require separate process/resource isolation for public deployment.

The API does not implement a Teams activity adapter. The Teams integration described in `teams.md` remains separate.

## Verification

`make test` exercises real in-memory PDFs, demo removal, same-name updates, full replacement, malformed inputs, atomic preservation, concurrent imports, API keys and the combined route. `scripts/smoke.py` tests the Rasa pipeline with the original demo corpus; its expected values must be changed when using real company policies.

## Request and response reference

All examples below use base URL `http://localhost:8000`. Successful responses return HTTP 200 and JSON. Requests to `/chat` use `application/json`; uploads use `multipart/form-data` (let your HTTP client set its boundary). When `HR_API_KEY` is configured, pass `X-API-Key` on every route except health/docs. Never put the key in query parameters.

### GET /health

```json
{"status":"ok","policy_index_exists":true}
```

This reports API process health only. A missing index still returns 200 with `false`.

### GET /policies

```json
{
  "library": "company",
  "documents": [{"filename":"handbook.pdf","chunks":12}],
  "total_chunks":12
}
```

`library` is `empty` when no index exists and `legacy/demo` when an older index lacks the library marker. This field alone does not prove the policies are approved.

### POST /policies/upload

| Form field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `file` | PDF binary | Yes | Plain filename, max 200 characters; 1 byte–20 MiB; extractable PDF text |
| `mode` | String | No | `upsert` (default) or `replace` |

```json
{
  "filename":"handbook.pdf",
  "mode":"upsert",
  "demo_removed":true,
  "uploaded_chunks":12,
  "total_chunks":12
}
```

`demo_removed` indicates that the previous library consisted of the known sample corpus. Uploads affect every user. A successful upload is immediately active; active original PDFs can be downloaded through the authenticated `/policies/pdfs/{filename}` route.

### POST /chat

| JSON field | Type | Required | Limits |
| --- | --- | --- | --- |
| `sender` | String | Yes | 1–128 characters; nonblank; conversation identifier, not authenticated identity |
| `message` | String | Yes | 1–4000 characters; nonblank |

Request:

```json
{"sender":"employee-123","message":"How many annual leave days are available?"}
```

Response (illustrative; text depends on your library):

```json
{
  "sender":"employee-123",
  "answer":"Relevant policy excerpt…\n\nSource: handbook.pdf — handbook, page 1 [chunk …]",
  "messages":[{"recipient_id":"employee-123","text":"Relevant policy excerpt…"}]
}
```

`answer` joins textual Rasa messages with blank lines. `messages` preserves upstream Rasa payloads, including non-text fields if present. The response also includes `sources`, `chunk` (first source or null), and the Rasa action’s intent/status metadata when available. `/chat/stream` sends buffered SSE after the complete Rasa answer; it is not model-token streaming. Keep a stable sender per conversation; use a new sender to begin another conversation.

### POST /ask

| Form field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `sender` | String | Yes | Same limits as `/chat` |
| `message` | String | Yes | Same limits as `/chat` |
| `file` | PDF binary | No | Same validation as `/policies/upload` |
| `mode` | String | No | `upsert` (default) or `replace` |

Returns the `/chat` fields plus `policy_update`, which is the upload response or `null` if no file was provided. This is a sequential operation, not a transaction spanning upload and chat.

### Errors

| Status | Meaning |
| --- | --- |
| 401 | API key missing or incorrect when configured |
| 413 | Uploaded file exceeds 20 MiB |
| 422 | Invalid request fields, filename, PDF, mode, or no extractable text |
| 502 | Rasa cannot be reached, returns an error, or returns malformed data |
| 503 | `/policies` cannot read a valid index |
| 504 | Rasa request timed out (60 seconds) |
| 500 | Unexpected server/storage error; inspect server logs |

Application errors normally use `{"detail":"message"}`. FastAPI field-validation errors use a `detail` array with field locations and validation messages. `/ask` upstream errors use an object so clients can tell whether the upload committed:

```json
{
  "detail": {
    "error": "Rasa is unavailable or returned an invalid response. Start make serve and make actions.",
    "policy_update": {
      "filename":"handbook.pdf","mode":"replace","demo_removed":true,
      "uploaded_chunks":12,"total_chunks":12
    }
  }
}
```

Do not retry `mode=replace` blindly: upload may already have succeeded. Check `/policies` first and retry the question using `/chat`.

## OpenAPI and interactive documentation

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Live schema: `http://localhost:8000/openapi.json`

Download the current schema from `/openapi.json` when needed.

Upload implementation follows [FastAPI multipart file handling](https://fastapi.tiangolo.com/tutorial/request-files/).

# UAT and demo checklist

Run `make test`, `make validate`, then `make train`. Start the action server and Rasa shell. Repeat the same questions in Teams after configuring the channel. Record actual results, source correctness, latency, tester and pass/fail; this checklist is not a claim that live Teams testing has occurred.

| Question / condition | Expected result with sample corpus |
| --- | --- |
| How many days of annual leave do I get? | 20-day fictional policy excerpt and Annual leave citation |
| How many sick leave days are available? | 10-day excerpt and Sick leave citation |
| How many remote work days are allowed? | Two-day excerpt and Remote work citation |
| When do health insurance benefits begin? | First-day excerpt and Health benefits citation |
| When should I submit travel expenses? | 30-day excerpt and Travel expenses citation |
| How do I report workplace harassment? | Confidential HR reporting excerpt |
| How long is the probation period? | 90-day excerpt |
| leave | Clarification |
| Tell me about leave and remote work | Clarification for topic or low NLU confidence |
| What is the weather? | Out-of-scope or clarification |
| What is the parental leave allowance? | No unsupported allowance invented; excerpt may be related, not sufficient |
| Missing/corrupt policy index | Library unavailable message |
| Re-ingest changed policy | New answer and updated chunk reference |
| Scanned PDF | Explicit OCR-required ingestion error |
| Invalid Teams bearer token | Request rejected; no answer |
| Teams fresh conversation | Greeting and normal question flow |

Before rollout: HR approves the corpus and excerpts; evaluate at least 10 unseen questions per intent; inspect confusion and fallback rates; verify conflicting, regional and superseded policies are removed or explicitly scoped. Current retrieval does not enforce employee/region-specific eligibility.

## PDF processing layer

`hr_policy/processing.py` handles PDF validation, extraction, Unicode/whitespace normalization, page-edge cleanup and page-level quality reporting. Both uploaded PDFs and local folder ingestion use it before chunking, category tagging and index publication.

Limits: 20 MiB and 500 pages. Encrypted, malformed and entirely text-free PDFs are rejected. Blank/image-only pages in mixed PDFs are retained as empty page positions so citations do not shift. OCR is not included. Repeated-page-edge cleanup is heuristic and should be reviewed for complex layouts; tables and headings are not reconstructed.

Preview processing without modifying the active policies:

```bash
.venv-runtime/bin/python -m hr_policy.processing /path/to/policy.pdf --output artifacts/processing-report.json
```

The report contains `source`, `page_count`, `pages` (`number`, `text`, `word_count`) and `warnings`. Warnings identify text-free pages and cases where cleaning would erase an entire page. The current upload response remains unchanged; use this preview to inspect page-level warnings before publication. Reports contain policy text and should be handled with the same access restrictions as the source documents.

## Rasa intent inspection and evidence

`GET /policies/intents` (API-key protected) returns the reviewed 30 intent mappings. `POST /nlu/parse` accepts `{"message":"How many days of bereavement leave are allowed?"}` and returns Rasa's `intent`, `intent_ranking` and `policy_mapping`. Rasa must run with `--enable-api` on its private interface; the launcher handles this.

`POST /chat` returns the exact Rasa action's `sources` array, `chunk` (first source or null), `intent` and `status` where applicable. Greeting/fallback responses do not get unrelated evidence attached. PDF URLs are only provided for active retained documents. `GET /policies/pdfs/{filename}` requires the same API authentication; URLs alone do not authorize access.

Uploads additionally return `page_count`, `warnings`, `intents`, and `intent_mapping_status` (`mapped` or `needs_review`). New intent labels need reviewed examples and Rasa retraining; document ingestion alone cannot teach the classifier new labels.

Bulk rebuild via `make reindex` validates all five registered PDFs before atomically replacing the index. A failed or empty rebuild preserves the prior library. Metadata-only cover/contents/version sections are omitted from searchable chunks, physical page numbers are retained, and chunks have a hard 180-word limit. Review layout/table extraction and warnings before relying on answers.

Optional model/embedding configuration is described in `.env.example` and the main README. Generation is optional and unavailable model services fall back to excerpts. No model quality claim is implied by that fallback. `/chat/stream` uses buffered SSE, not live token generation.
