# Docker service logs

The Docker image starts `scripts/run_deployment.py` directly. The Makefile's
`nohup` redirection therefore does not create `deployment.log`. The supervisor
now writes each process's output to a file and continues sending it to the
container console and Azure App Service Log stream.

| File | Output |
| --- | --- |
| `deployment.log` | Supervisor startup, restarts and child exits |
| `actions.log` | Rasa action worker and Darwinbox configuration warnings |
| `rasa.log` | Rasa server and model output |
| `web.log` | FastAPI, Teams bot endpoint and web server |
| `jupyter.log` | JupyterLab server |
| `bot.log` | Standalone bot, only in bot or combined mode |
| `darwinbox-sync.log`, `employee-report-sync.log` | Optional sync services, when configured |
| `darwinbox.log` | Credential-free Darwinbox configuration, API and bot/web receipt events |

With `DATA_ROOT=/tmp` or `WEBSITE_INSTANCE_ID` on Azure, the primary log
directory is `/tmp/employeeassist/logs` and every entry is also written to
`/app/artifacts/logs`. In the source checkout that mirror is
`/home/ojas/HR-Policy-NLU/artifacts/logs`. Local runs write directly to the
source checkout's `artifacts/logs`. To print the primary path in a Jupyter Terminal:

```sh
cd /app
python -c "from actions.runtime_paths import runtime_path; from pathlib import Path; print(runtime_path('logs', Path('/app/artifacts/logs')))"
```

After deploying the updated image and restarting the container, inspect
`actions.log` for `Darwinbox holiday configuration:` or
`Darwinbox leave configuration:`. Requests that reach Darwinbox log a start,
completion time, HTTP status, connection exception type, invalid JSON, or an
API rejection. The logs omit URLs, API keys, request bodies and employee numbers.
For example, with the Azure `/tmp` default:

```sh
tail -n 100 /tmp/employeeassist/logs/actions.log
tail -f /tmp/employeeassist/logs/actions.log
```

Files are created automatically when the supervisor starts. Creating empty
files manually does not redirect the already running processes. A change to
`run_deployment.py` requires a container/App Service restart; `restart all`
only restarts its child services. Both `/tmp` and `/app` are container-local and
can disappear when the container is replaced. Use Azure Log stream or mounted
persistent storage for retention across replacement.

Cloud startup also creates matching `control`, `identity`, `jupyter`, `logs`
and `private` directories under `/tmp/employeeassist` and `/app/artifacts`.
Verified identities and Darwinbox employee state are mirrored after each write.
Durable Jupyter configuration/data is restored at startup and snapshotted during
supervisor-managed restarts and shutdown. Live control sockets, PID/lock files,
SQLite sidecar files and Jupyter kernel runtime files are excluded.

`darwinbox.log` is written directly by the action worker and shared chat
service after the updated files are copied into `/app` and those two services
are restarted. It does not depend on the supervisor's per-service log relay.
The action worker records whether leave/holiday settings are present, missing
setting names, mapping failures and API failure stages. The bot/web entry
records the integration status returned by Rasa. It never writes credentials,
request URLs, employee numbers, user IDs or raw response bodies. To check it:

```sh
tail -n 100 /tmp/employeeassist/logs/darwinbox.log
```

Use the active log directory printed above if it differs from `/tmp`.

`conversations.jsonl` is a separate audit file. Actions that call
`append_conversation` write the user's question, answer, routing and integration
status to it. The current policy, leave and holiday actions do this; the IT
answer action does not write every IT question there. Its path is
`/tmp/employeeassist/logs/conversations.jsonl` with a cloud runtime, mirrored to
`/app/artifacts/logs/conversations.jsonl`; local runs use the project
`artifacts/logs` path. `CONVERSATION_LOG_PATH` can override the primary path. It
may contain user questions, identifiers and returned leave data, so restrict
access to it.
