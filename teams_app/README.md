# Microsoft Teams application

The deployment has two application entry points. Both use the shared Rasa model,
`actions/` package, and policy data from the repository root.

- `web_package`: Teams tab and FastAPI API on port `8610`
- `bot_package`: Azure Bot Service messaging endpoint on port `3978`
- Rasa REST API: port `5005`
- Rasa action server: port `5055`

## Local setup

```bash
cd /home/ojas/HR-Policy-NLU
make install
make train
make run_deployment
```

The single public application endpoint is `http://localhost:8610`:

- Web tab: `GET /`
- API docs: `GET /docs`
- Health: `GET /health`
- Teams/Azure Bot messaging: `POST /api/messages`
- Policy PDFs: `GET /policy-documents/...`

Rasa and the action server run as private dependencies on ports `5005` and
`5055`. Press `Ctrl+C` to stop the complete deployment.

## Docker

Build and run the single-endpoint deployment:

```bash
make docker_deployment
```

Equivalent commands:

```bash
docker build --target deployment -f Dockerfile -t hr-policy-deployment .

docker run --rm \
  -p 8610:8610 \
  -v "$(pwd)/data/policies:/app/data/policies" \
  --env-file .env \
  hr-policy-deployment
```

Only port `8610` is published. The policy mount preserves the index, PDFs, and
generated Markdown. Set `APP_PUBLIC_URL` to the public HTTPS URL so Adaptive
Card PDF links open outside the container.

For Microsoft Teams, configure the Azure Bot messaging endpoint as:

```text
https://YOUR-PUBLIC-DOMAIN/api/messages
```

Microsoft Teams cannot call a localhost endpoint. Use an HTTPS reverse proxy or
tunnel during development and put that public domain in the Teams manifest.

## JupyterLab on the same domain

The web and combined deployments start Jupyter privately on `127.0.0.1:8888`.
Open `/employeeassist/jupyter` directly on the Azure hostname. The whole Jupyter server, including login, Lab, static assets, APIs and WebSockets, uses `/employeeassist/jupyter/`. Root-hosted ngrok uses `/jupyter/`. Keep `PUBLIC_BASE_URL` or `APP_PUBLIC_URL` at the application root, restart both API and Jupyter after changes, and enter `JUPYTER_TOKEN` in Jupyter's login form. There is no Jupyter link in the chat UI. See [Jupyter subroute verification](../deploy/jupyter-subroute.md), [Azure setup](../deploy/azure-paths-auth.md) and [superadmin/broadcast setup](../deploy/broadcast-superadmins.md).
Do not put it in the Teams manifest or browser source.

Install Jupyter in a separate environment (Rasa requires older shared libraries):

```bash
python3 -m venv .venv-jupyter
.venv-jupyter/bin/pip install -r requirements-jupyter.txt
```

Docker builds this environment automatically. Set `JUPYTER_PYTHON` to override
its interpreter path. Install updated `requirements.txt` in the app runtime.
Startup refuses an empty key. Existing environment variables take precedence over
`.env`. Jupyter opens the repository root and retains its normal cookie, token,
and same-origin protections. No additional public port or domain is needed.

When using an HTTPS reverse proxy, forward `/` to port `8610`, preserve `Host`,
set `X-Forwarded-Proto`, and forward WebSocket Upgrade/Connection headers for
`/jupyter/`. Only trust forwarded headers from your proxy. Teams `validDomains`
should contain the existing webapp hostname; the Jupyter link opens a browser tab.

## Darwinbox employee directory and personal leave balance

EmployeeAssist uses a shared Darwinbox client for SHA-512 authentication and
separate adapters for each module. Set `DARWINBOX_BASE_URL` to the tenant origin,
for example `https://tenant.darwinbox.in`. The employee adapter uses
`/masterapi/employee`. The Emergys leave adapter uses
`/leavesactionapi/leavebalance` with `DARWINBOX_LEAVE_API_KEY`, an employee-number
array, configured leave names, and the rounding flag. SHA-512 authenticated leave
endpoints remain supported as a fallback.

At deployment startup, `scripts/sync_darwinbox_employees.py` fetches a full
employee dataset and stores a minimized directory under `artifacts/private`.
A worker then requests changes using `last_modified_att` at
`DARWINBOX_EMPLOYEE_SYNC_INTERVAL_SECONDS`. Invalid, failed, or empty full-load
responses never replace the last successful directory. Run a manual full sync
with `make sync-darwinbox`.

The stored directory contains employee number, official email, display name,
status, timestamps, verified Teams identity bindings, and pending enrollment
state. In Azure App Service it defaults to
`/tmp/employeeassist/darwinbox/employees.json`. Temporary data can be cleared on
restart and is not shared across instances. Set `DATA_ROOT` to a writable mounted
parent directory for persistent storage, or override `DARWINBOX_EMPLOYEE_STORE_PATH`.
See [Azure paths and authentication](../deploy/azure-paths-auth.md) for configuration.

For “What is my leave balance?”, the action maps the authenticated account to
one Darwinbox employee record. If no record matches, it asks the user to contact
HR or the application administrator to link their account. Personal balances
never fall back to a sample/default employee. Guest users must sign in, and
employee codes typed into chat cannot change account mappings. The optional
default employee remains available for the company holiday calendar.

Required tenant values are documented in the root `.env.example`. Existing
absolute `DARWINBOX_LEAVE_BALANCE_URL` values remain supported, while new
functions should use a dedicated `*_PATH` and adapter under
`actions/darwinbox_adapters/`.

### Leave-balance test mode

The current test deployment sends every personal leave-balance request to the
Darwinbox leave API with employee number `Ell0769`. It does not accept an
employee code from chat and does not use the employee-directory API. Test the
same path used by the web interface with:

```bash
curl -X POST http://localhost:8610/chat \
  -H 'Content-Type: application/json' \
  -d '{"sender":"leave-api-test","message":"What is my leave balance?"}'
```

Darwinbox must accept `DARWINBOX_LEAVE_API_KEY` for
`/leavesactionapi/leavebalance`; otherwise the response reports that the
integration is temporarily unavailable. When Darwinbox provisions the endpoint
with HTTP Basic Auth, also configure `DARWINBOX_LEAVE_USERNAME` and
`DARWINBOX_LEAVE_PASSWORD`; the API key remains in the JSON request body.

### Holiday-list API

Holiday requests use `POST /leavesactionapi/holidaylist` with the separately
provisioned holiday `api_key`, the documented `employee_no`, and an automatically
selected year. They reuse the leave-balance HTTP Basic Auth credentials. An
explicit year in the user's question overrides the current year:

```dotenv
DARWINBOX_HOLIDAY_URL=https://emergys.darwinbox.in/leavesactionapi/holidaylist
DARWINBOX_HOLIDAY_API_KEY=<holiday-api-key>
```

The current test deployment requests holidays for employee `Ell0769`. The bot
returns the next five holidays by default, accepts a requested count, and can
return the complete calendar for a year.

## Send IT requests directly from Teams

Teams and web ticket composers have editable **To**, **CC**, **Subject** and
**Body** fields. Raise a request sends the submitted fields from the verified
user's mailbox through delegated Microsoft Graph `/me/sendMail`. The default To
is `helpdesk@emergys.com`; users may edit it and add CC recipients.

Native Teams chat uses Bot Framework SSO with `signin/tokenExchange`. The Teams
web tab uses its existing SSO token in a server-side on-behalf-of exchange.
Normal browser users authorize delegated mailbox access with Microsoft sign-in.
Guest users may open Outlook for manual sending but cannot call the send API.

[Configure Entra, the Azure Bot OAuth connection and deployment environment](../deploy/teams-mail-sso.md)
before testing live SSO. First-time consent can resume a request that the same
user already approved by clicking Raise a request. Unsolicited sign-in events
never send mail. Pending approvals expire after ten minutes and are invalidated
by edits or cancellation. A completed approval cannot be replayed.

The Graph account is checked against the verified sender. Drafts are bound to
the tenant/user/conversation and persisted for up to seven days. Duplicate sends
are blocked. Ambiguous network outcomes require Sent Items review and are not
retried automatically. `202 Accepted` means Outlook accepted the message for
sending, not confirmed delivery or helpdesk ticket creation.

Open in Outlook uses the current edits and does not call Graph sendMail. For
native cards, it first submits the fields and returns an updated Outlook link.

```bash
PYTHON_DOTENV_DISABLED=1 .venv-runtime/bin/python -m unittest discover -s scripts/ticket_checks -p 'test_*.py'
```

## Teams prompt suggestions

The welcome/help card includes a **Prompt suggestions** button. Users can also
send `Prompt suggestions`, `show suggestions`, or `show prompt suggestions` in
the same bot chat. The handler returns **Prompt Suggestions from EmployeeAssist**
with grouped HR questions, all ten IT topics, and source-backed incident examples.
Selecting a question sends it through the normal conversation flow. The list
also includes Create IT ticket.

The personal Teams command menu has ten entries, including Prompt suggestions.
The prepared update is `app_package/upload/EmployeeAssist-1.0.10.zip`; update the
installed Teams app package using this archive to refresh the native menu.
The handler changes require restarting the application deployment. Existing
chat cards remain unchanged; send `help` to obtain a new welcome card.


## Editable mail and Teams SSO

Create IT ticket now opens a single editable To/CC/Subject/Body form. Existing
issue text is retained; there are no separate error/start-time questions.
Raise a request uses Teams SSO and automatically resumes the approved send after
consent. Open in Outlook creates a link from the current edited card fields.
See [SSO setup and deployment](../deploy/teams-mail-sso.md) for the matching Entra
API resource, Azure Bot token-exchange URL, delegated consent and app package.
