# EmployeeAssist

EmployeeAssist is a Rasa-based HR and IT assistant for the web and Microsoft
Teams. It answers reviewed HR policy questions with source evidence, retrieves
IT troubleshooting guidance, shows Darwinbox leave balances and holidays,
creates editable IT email requests, and supports controlled Teams broadcasts.

The current release is **1.0.10**. Deployment configuration and the release
procedure are documented in [deploy/RELEASE.md](deploy/RELEASE.md).

## Current features

- DIET intent classification followed by intent-scoped semantic FAQ retrieval.
- Reviewed HR answers with policy excerpts and PDF source references.
- Ten IT routing topics with cosine-based FAQ retrieval, troubleshooting tips,
  historical incident references and clarification for ambiguous symptoms.
- Personal Darwinbox leave balances, holiday lists and employee mapping.
- Immediate and scheduled Darwinbox employee-directory synchronization.
- Editable Teams and web IT-request drafts with Microsoft mailbox SSO.
- Teams superadmin broadcasts with preview, confirmation, cancellation and
  per-recipient delivery status.
- Teams manifest schema 1.30 with ten native prompt suggestions.
- Same-domain web, Teams bot and JupyterLab deployment behind one supervisor.
- Local/cloud runtime detection, automatic state-file creation and cloud backup
  copies for the employee directory, ticket database, conversations and logs.

## Architecture

User question
-> DIET intent classification
-> `action_answer_policy`
-> filter enabled FAQ entries by predicted intent
-> SentenceTransformer semantic similarity
-> confident match: fixed FAQ answer + source
-> low confidence: PDF RAG fallback

IT questions use ten separate routing intents and `action_answer_it`, with
source-backed incident/resolution FAQs and clarification for ambiguous symptoms.
They are excluded from HR policy evidence retrieval.

## Source of truth

- `data/policy_intents.json`: reviewed policy intents and NLU examples.
- `data/faq_answers.json`: reviewed FAQ questions, utterances, fixed answers and sources.
- `data/it_faq_additional_test_questions.md`: reviewed IT troubleshooting questions and answers.
- `data/it_faqs.csv`: generated consolidated IT FAQ catalog.
- `scripts/sync_intents.py`: build-time generator for Rasa NLU/rules/domain data.
- `scripts/import_it_report.py`: generator for incident-backed IT FAQ and NLU data.

You do **not** need to run `sync_intents.py` for every chat. Run `make sync-intents` after changing either JSON registry, then train again.

## Embeddings

The HR FAQ matcher loads `sentence-transformers/all-MiniLM-L6-v2` directly
inside the Rasa action server. IT retrieval filters candidates with the DIET
intent and ranks matching phrases using cosine similarity. No separate
embedding HTTP service and no Rasa `LanguageModelFeaturizer` are required.

## Microsoft Teams and Bot Service

The same Entra/Bot application ID is used by Azure Bot Service and the Teams
package. Configure the bot messaging endpoint as:

```text
https://dockerpoc-webapp01.azurewebsites.net/employeeassist/api/messages
```

The Teams package is
`teams_app/app_package/upload/EmployeeAssist-1.0.10.zip`. Its manifest uses
schema 1.30 and contains ten prompt commands, each with the exact text Teams
sends when selected. After changing a manifest, increment its application
version, upload the new ZIP in Developer Portal, publish/distribute it, and
update or reinstall the app in Teams. Restarting the bot alone does not replace
an installed Teams manifest.

See [Teams SSO and mail setup](deploy/teams-mail-sso.md) and
[Teams application details](teams_app/README.md).

## Install

```bash
make install
```

## Build and run

```bash
make sync-intents
make train
```

Start the Rasa action server and HTTP API together:

```bash
make start
```

This exposes the action server on port `5055` and the Rasa API on port `5005`.
Both processes log to the same terminal, and `Ctrl+C` stops both.

For an interactive shell, run `make actions` in one terminal and `make chat`
in another.

## Threshold

Default semantic-match threshold is `0.75`.

You can override it through `.env`:

```env
FAQ_EMBEDDING_THRESHOLD=0.80
```

## PDF RAG

Replace the fallback block in `actions/actions.py` with your existing PDF retrieval function/service. The FAQ matcher only returns reviewed fixed answers from `faq_answers.json`; it does not invent policy content.
## Teams deployment

Run the single public application endpoint, including the Teams tab and bot handler:

```bash
make run_deployment
```

See `teams_app/README.md` for separate local and Docker commands.

The supervisor supports child-service restarts after it is running:

```bash
.venv-runtime/bin/python scripts/run_deployment.py restart actions
.venv-runtime/bin/python scripts/run_deployment.py restart rasa
.venv-runtime/bin/python scripts/run_deployment.py restart web
.venv-runtime/bin/python scripts/run_deployment.py restart jupyter
```

Changes to `scripts/run_deployment.py` require one complete container or App
Service restart. See [in-container restart](deploy/in-container-restart.md).

### Release files

Generate the application and runtime packages with:

```bash
.venv-runtime/bin/python scripts/package_docker_upload.py
```

Version 1.0.10 produces a loose deployment tree under
`artifacts/releases/1.0.10/`, with separate `app` and
`tmp/employeeassist` directories. The `app` directory is a complete Docker and
local deployment checkout containing the Makefile, Dockerfiles, requirements,
deployment helpers, trained model and a credential-free `ENVIRONMENT.example`.
Copy that template to `.env` and replace its placeholders locally, or configure
the same settings in the hosting platform. ZIP files and real credentials are
excluded from the release tree. The separately maintained Teams Developer
Portal package remains under `teams_app/app_package/upload`.

## Runtime files, backups and logs

Runtime paths are selected from the environment:

| Condition | Mode | Primary location |
| --- | --- | --- |
| `DATA_ROOT` is set | Configured runtime root | `<DATA_ROOT>/employeeassist` |
| Azure supplies `WEBSITE_INSTANCE_ID` | Azure | `/tmp/employeeassist` |
| Neither is set | Local | Project `data` and `artifacts` directories |

The recommended portable employee-map setting is:

```env
DARWINBOX_EMPLOYEE_MAP_FILE=data/employee_directory.json
```

It resolves to the project `data` directory locally and under the runtime root
in cloud mode. At startup and before supervisor-managed service restarts, the
runtime bootstrap creates missing files and restores a missing cloud primary
from its project backup.

| State | Local primary | Cloud primary | Cloud backup |
| --- | --- | --- | --- |
| Employee map | `data/employee_directory.json` | runtime `data/employee_directory.json` | `/app/data/employee_directory.json` |
| Ticket drafts | `artifacts/private/teams_ticket_mail.sqlite3` | runtime `private/teams_ticket_mail.sqlite3` | `/app/artifacts/private/teams_ticket_mail.sqlite3` |
| Verified identities | `artifacts/identity/microsoft_identities.json` | runtime `identity/microsoft_identities.json` | `/app/artifacts/identity/microsoft_identities.json` |
| Darwinbox state | `artifacts/private/darwinbox_employees.json` | runtime `darwinbox/employees.json` | `/app/artifacts/private/darwinbox_employees.json` |
| Jupyter state | `artifacts/jupyter` | runtime `jupyter` | `/app/artifacts/jupyter` |
| Conversations | `artifacts/logs/conversations.jsonl` | runtime `logs/conversations.jsonl` | `/app/artifacts/logs/conversations.jsonl` |
| Service logs | `artifacts/logs/*.log` | runtime `logs/*.log` | `/app/artifacts/logs/*.log` |
| Process control | `artifacts/control` | runtime `control` | Directory only; live files are not restored |

SQLite backups use SQLite's backup API after committed changes. Employee-map,
identity, Darwinbox state, conversation and log writes are mirrored to their
cloud backup. Durable Jupyter configuration/data is copied during startup,
service restarts and supervisor shutdown. Control sockets, PID/lock files and
Jupyter kernel runtime files are recreated instead of restored. Both `/tmp`
and `/app` remain container-local unless backed by persistent storage. See
[container logging](deploy/container-logs.md).

## Darwinbox employee report sync

EmployeeAssist can rebuild `data/employee_directory.json` from a Darwinbox
Report Builder report. Configure `DARWINBOX_REPORT_API_KEY`,
`DARWINBOX_REPORT_ID`. It uses the shared Darwinbox Basic Auth credentials in
`DARWINBOX_LEAVE_USERNAME` and `DARWINBOX_LEAVE_PASSWORD`, just like the leave
and holiday APIs. The report must include an employee ID/code,
an official/work email, and preferably an employee name. The directory report
fields `Employee Id`, `First Name`, `Full Name`, `Last Name`, and
`Official Email Id` are supported: the email becomes the lowercase directory key
and `email`, the employee ID becomes `code` and `darwinbox_code`, and `Full Name`
becomes `name` (falling back to first and last name). Company is included only
when returned by the report. Rows without an employee ID or email are skipped.

Run a manual refresh with:

```bash
make sync-employee-report
```

The normal local and Docker deployment starts a scheduler when these variables
are configured. It refreshes immediately when the scheduler starts and then at
16:30 Asia/Kolkata each day. Supervisor-managed Rasa, actions, web, bot and
Jupyter restarts also request a one-time refresh. Each successful
refresh atomically replaces the active employee snapshot, so employees absent
from the report are removed. The configured report is treated as the active
employee dataset; generic report status fields are not interpreted as employment
status. The guest profile is retained. Adjust the minute
within the 4–5 PM window with `DARWINBOX_REPORT_SYNC_MINUTE`.

Inspect the scheduler without exposing credentials:

```bash
tail -f artifacts/logs/employee-report-sync.log
# Azure/container primary:
tail -f /tmp/employeeassist/logs/employee-report-sync.log
```

## IT support request composer

Choose **Create IT ticket** to open one editable mail form with To, optional CC,
Subject and Body. A supplied issue such as `internet issue` prepopulates the
message without asking separate error or start-time chat questions. Outside a
ticket flow, ordinary IT questions continue to receive FAQ guidance. Older
conversations waiting for the issue description move straight to a mail preview.

Raise a request sends the edited fields from the verified user's Microsoft
mailbox. Teams uses Bot Framework SSO/token exchange; the web tab uses Teams
SSO with a delegated on-behalf-of Graph exchange. Successful first-time consent
resumes the approved Teams send automatically. Normal browser users use Microsoft
mail authorization. To and CC accept comma- or semicolon-separated addresses.

Open in Outlook uses the current edited fields for manual review/sending. Native
Teams cards request a fresh Outlook link from the server so it includes card
edits. The web composer submits on Enter in a single-line field or Ctrl+Enter in
Body; Enter in Body adds a line. Native Teams card submissions use Raise a request.

Sending checks the Graph account against the verified sender, binds drafts to
the owner and conversation, and atomically prevents duplicate sends. Unknown
send outcomes are retained for Sent Items review and never automatically retried.

See [deployment and SSO configuration](deploy/teams-mail-sso.md) before enabling
live sending. The package is
`teams_app/app_package/upload/EmployeeAssist-1.0.10.zip`.

```bash
PYTHON_DOTENV_DISABLED=1 .venv-runtime/bin/python -m unittest discover -s scripts/ticket_checks -p 'test_*.py'
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHON_DOTENV_DISABLED=1 .venv-runtime/bin/python scripts/evaluate_mail_flow.py
```

## IT incident FAQs and routing

`data/Report_15_Oct_26_092938.xls` is a UTF-16 HTML incident export.
Run `.venv-runtime/bin/python scripts/import_it_report.py` to regenerate ten
IT intents in `data/nlu_it.yml`, their rules, and the generated FAQs in the
existing `data/faq_answers.json`. The importer is idempotent. It omits caller
columns, email addresses and IP addresses from training examples. Historical
resolutions are not presented as diagnoses. IT FAQs use `action_answer_it`;
HR FAQs retain policy evidence validation and semantic matching. Run
`make train` after importing. Rasa training reads only `data/*.yml` so that
employee-directory records are never scanned as training data.

## Superadmin broadcast in Teams personal chat

Configure these **deployment environment variables** (no roles are accepted
from user messages or web chat metadata):

```text
TEAMS_BROADCAST_TENANT_ID=<Microsoft Entra tenant UUID>
TEAMS_SUPERADMIN_OBJECT_IDS=<admin Entra object UUID>,<another admin UUID>
TEAMS_BROADCAST_DB=artifacts/teams/broadcast.sqlite3
```

`TEAMS_BROADCAST_TENANT_ID` defaults to `MICROSOFT_TENANT_ID`. The object ID
allowlist defaults to empty, disabling privileged access. The normal authenticated
Bot Framework setup (`MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`, tenant) is
required. Set variables in the hosting environment and restart the deployment.
The database defaults to `artifacts/teams/broadcast.sqlite3`; preserve it on a
persistent volume and restrict filesystem access to the service account.
Relative database paths resolve from the project directory automatically; absolute
paths are also supported. Omitting the variable uses the same default path.

In the same personal Teams chat used for HR/IT questions:

1. `/broadcast <message>` prepares a preview and snapshots the recipient count.
2. `/broadcast-confirm <id>` sends the preview within ten minutes.
3. `/broadcast-cancel <id>` cancels a draft.
4. `/broadcast-status <id>` reports accepted and failed/unknown deliveries.

Only the owning superadmin in the original chat can confirm, cancel or inspect
that broadcast. Other users and group/channel chats cannot use these commands.
Recipients are unique users in the configured tenant whose **personal Teams
conversation references** have been captured after this feature is deployed
(on messages or installation events). Employee codes, email addresses or Entra
object IDs alone do not supply a deliverable conversation reference. Install
this Teams app for intended users and have them open/message the bot first.
Organisation-wide app installation requires separate Teams/Graph administration.

Sends are paced and recorded per recipient. Confirmation is claimed atomically
and cannot resend the same broadcast. `accepted` means the Teams connector
accepted the message, not that the user read it. Failed/ambiguous sends are not
automatically retried. If the process restarts mid-send, `sending` records require
operator review; there is no automatic resume that could duplicate messages.
No broadcast is sent by tests or during setup.

## Full incident-report FAQ coverage and evaluation

Run the importer after replacing the report:

```bash
.venv-runtime/bin/python scripts/import_it_report.py
make train
PYTHON_DOTENV_DISABLED=1 .venv-runtime/bin/python scripts/evaluate_xls_faqs.py
```

The importer keeps the existing HR FAQs and generates ten IT routing topics,
230 individually cited incident FAQs and 29 reusable resolution FAQs. It writes
`data/it_faqs.csv`, `data/it_faqs.md`, and `data/it_report_coverage.json` for review.
`data/nlu_it.yml` contains the sanitised symptoms, question forms and reviewed
training paraphrases. The runtime uses the same IT FAQ registry for exact
incident lookup, distinguishing symptom checks and TF-IDF similarity; it asks
for clarification when the description cannot support one historical answer.
IT cases remain separate from policy evidence retrieval.

Historical report notes describe work by IT, not a diagnosis or authorisation
for new infrastructure changes. Missing solution fields and administrative
closure notes are explicitly labelled. Ask `What happened in incident 346506?`
for a specific historical record. A question such as `GEMS unavailable while
Outlook worked` can retrieve the DNS case; `Citrix is not working` can match
multiple incompatible incidents and should request clarification.

The evaluation writes `artifacts/evaluations/xls/report.md`, `results.json`,
and `responses.csv`, with the actual question, answer, selected source IDs,
expected result, and routing result. Results are split into:

- Source incident-ID reconstruction (230): an integrity check, not unseen accuracy.
- Source symptom replay (230): report wording, including ambiguous duplicates.
- Development paraphrases (58): excluded from training but used during development.
- Validation questions (36): excluded from NLU and retrieval candidates. The first results are preserved as baseline files; after inspection, reruns are regression checks rather than fresh unseen estimates.
- HR/ticket regression and contrast controls (19).
- Fresh final holdout (20): added after development and excluded from training. The first run found one contrast-wording bug; `fresh_baseline_*` preserves that result and subsequent runs are regression checks.

The test fixtures are in `scripts/ticket_checks/xls_test_set.json` and
`xls_validation_set.json`, plus `xls_fresh_validation.json`. Keep frozen validation questions out of training;
add fresh validation questions after using any existing validation failures to
tune behaviour. Report exact response matches separately from clarifications.
These automated checks do not replace human review of incident resolution notes.
