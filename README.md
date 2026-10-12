# EmployeeAssist

## Architecture

User question
-> DIET intent classification
-> `action_answer_policy`
-> filter enabled FAQ entries by predicted intent
-> SentenceTransformer semantic similarity
-> confident match: fixed FAQ answer + source
-> low confidence: PDF RAG fallback

## Source of truth

- `data/policy_intents.json`: reviewed policy intents and NLU examples.
- `data/faq_answers.json`: reviewed FAQ questions, utterances, fixed answers and sources.
- `scripts/sync_intents.py`: build-time generator for Rasa NLU/rules/domain data.

You do **not** need to run `sync_intents.py` for every chat. Run `make sync-intents` after changing either JSON registry, then train again.

## Embeddings

The FAQ matcher loads `sentence-transformers/all-MiniLM-L6-v2` directly inside the Rasa action server. No separate embedding HTTP service and no Rasa `LanguageModelFeaturizer` are required.

## Bot Service

Azure Bot Service ->
APP_ID/CLIENT_ID, CLIENT_SECRET, TENANT_ID

TERMS_URL, PRIVACY_URL -> dummy endpoints
WEBSITE_URL ->

32x32 , 192x192 logo
manifest
zip & upload on developer portal

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
are configured. It refreshes at 16:30 Asia/Kolkata by default. Each successful
refresh atomically replaces the active employee snapshot, so employees absent
from the report are removed. The configured report is treated as the active
employee dataset; generic report status fields are not interpreted as employment
status. The guest profile is retained. Adjust the minute
within the 4–5 PM window with `DARWINBOX_REPORT_SYNC_MINUTE`.

## IT helpdesk drafts

Use **Create IT ticket** in the web app or Teams welcome card, or ask to
contact support. An issue report offers confirmation before starting a Rasa
form. The form collects the problem, error or behaviour, and start
time; “not sure” is accepted. The preview contains separate subject and body
fields, addressed to `helpdesk@emergys.com`, with edit and cancel controls.
Templates retain supplied facts and add no generation model.
A small routing guard distinguishes support-address questions and clear
application failures; policy-marked messages retain policy routing or clarify. Long messages
remain editable. No mail is sent by the application.

Web quick actions send ordinary chat text. When the final details arrive, the
web app opens the prepared Outlook draft in a tab reserved during the user
gesture, without adding a draft-opened acknowledgement to chat. If popups are blocked, an in-app
preview dialog offers an explicit Open in Outlook button. Teams users open Outlook,
and use the draft card’s Open in Outlook, Edit subject, Edit body, and Cancel
buttons. The form has already ended when the preview is produced. Outlook's selected mailbox determines the sender.
Signatures use verified Microsoft identity; guests default to Ojas Kulkarni
and `ojas.kulkarni@emergys.com`. No Graph scopes are needed for compose links.

Run draft logic checks with:

```bash
.venv-runtime/bin/python -m unittest discover -s scripts/ticket_checks -p test_tickets.py
```

`scripts/ticket_checks/ticket_intents.json` includes routing probes for support, policies,
leave balances, holidays, typo examples and gibberish. Retrain Rasa after
changing `data/nlu_tickets.yml` or `data/rules_tickets.yml`.

With the local services running and guest chat enabled, run the routing and
conversation checks with:

```bash
.venv-runtime/bin/python scripts/ticket_checks/check_live.py
```

These tests prepare and discard local conversation drafts. They do not open
Outlook, send mail, or query the employee directory.
