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
Open `/jupyter/lab` directly on the same HTTPS domain as the webapp. Enter `JUPYTER_TOKEN` from the root `.env` in Jupyter's login form. The token protects only Jupyter and grants code execution access; application APIs do not require it.
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
`/home/data/EmployeeAssist/darwinbox/employees.json`, which survives a container
restart. Override this with `DARWINBOX_EMPLOYEE_STORE_PATH` when required.

For “What is my leave balance?”, the action maps the authenticated Teams email to
one Darwinbox employee record. On a cache miss it performs an incremental refresh
and then a full refresh. If no record matches, it asks for an employee code. A
submitted code is checked against the synchronized record and its official email.
When Employee Core data is unavailable, the code is remembered for the verified
Teams email only after the leave-balance API accepts it. Browser and anonymous
callers cannot create bindings. API failures retain the last successful cache and
do not write an unverified mapping.

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
