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
`/masterapi/employee`; the leave-balance path remains configurable because
Darwinbox provisions it per tenant.

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
submitted code is remembered only after its Darwinbox official email matches the
verified Teams email; browser/anonymous callers cannot create bindings. API
failures retain and use the last successful cache, while balance failures return a
retry message without exposing credentials or another employee's data.

Required tenant values are documented in the root `.env.example`. Existing
absolute `DARWINBOX_LEAVE_BALANCE_URL` values remain supported, while new
functions should use a dedicated `*_PATH` and adapter under
`actions/darwinbox_adapters/`.
