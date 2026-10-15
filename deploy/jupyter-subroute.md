# Jupyter on a shared application hostname

The entire Jupyter server lives under `/employeeassist/jupyter/`. A redirect
alone is insufficient: Jupyter's own `base_url`, login form action, `next`
destination, cookies, static assets, APIs and kernel WebSockets must all use
this path. The chat UI does not contain a Jupyter link.

For Azure, configure the root of the application, then restart **all** processes:

```dotenv
PUBLIC_BASE_URL=https://dockerpoc-webapp01.azurewebsites.net/employeeassist/
WEB_PORT=8608
```

`APP_PUBLIC_URL` is supported when `PUBLIC_BASE_URL` is unset. If both are set,
`PUBLIC_BASE_URL` takes precedence. The launcher now passes these explicit
arguments to Jupyter as well as setting them in its config:

```text
--ServerApp.base_url=/employeeassist/jupyter/
--ServerApp.default_url=/employeeassist/jupyter/lab
```

Open `/employeeassist/jupyter`. Normal Lab/login navigation stays within:

```text
/employeeassist/jupyter/lab
/employeeassist/jupyter/login?next=%2Femployeeassist%2Fjupyter%2Flab
```

Requests must never escape to the shared host's root `/jupyter/`. The API proxy
supports a front-end that either preserves or strips `/employeeassist/`.
It preserves query strings and forwards WebSocket upgrades. Its compatibility
handling repairs both legacy upstream Location paths and their encoded `next`
target, while the correctly configured Jupyter server produces native prefixed
HTML, asset paths and login forms. No root `/jupyter` route is needed in the
shared host's nginx configuration.

## Reproduce the Azure mount on ngrok

`./deploy/ngrok_endpoints.sh root` retains the original root-hosted tunnel mode.
To test the shared-host layout, first stop the existing tunnel using its normal
process manager. Export the following environment for **both** deployment and
tunnel launch shells:

```bash
export PUBLIC_BASE_URL=https://flounder-sphere-dab.ngrok-free.dev/employeeassist/
export WEB_PORT=8610
```

Restart the complete local deployment so the API and Jupyter use that base
path. Then run:

```bash
./deploy/ngrok_endpoints.sh subroute
```

This renders an isolated nginx config from `nginx-subroute.conf.example`,
checks it, starts/reloads only that private nginx instance on localhost:18610,
and points ngrok at that port. It does not replace the system nginx config.
The proxy strips `/employeeassist/` before forwarding to the API, reproducing
the Azure front-end behaviour; `/` is a 404 stand-in for other hosted sites.
`NGROK_SUBROUTE_PORT` overrides 18610 and `NGROK_PUBLIC_URL` overrides the tunnel
domain. A running nginx/ngrok installation with local socket access is required.

Check the public URL and follow the full login flow:

```text
https://flounder-sphere-dab.ngrok-free.dev/employeeassist/jupyter
```

Verify that every Location, login form action, Lab static URL and WebSocket
request remains under `/employeeassist/jupyter/`. Once finished, stop the test
tunnel and isolated nginx instance, restore the Azure public base URL in the
Azure environment, and restart the Azure deployment.

## Verification without network sockets

```bash
PYTHON_DOTENV_DISABLED=1 .venv-jupyter/bin/python scripts/check_jupyter_handlers.py
```

This loads the actual Jupyter server config and Lab extension, then exercises
real Tornado handlers in memory for root ngrok, subroute ngrok and subroute
Azure URLs. It checks login/next/form paths, Lab page configuration, cookies,
a real static asset and the kernel API. The API regression suite separately
tests proxy redirects, both front-end rewrite modes and bidirectional kernel
WebSocket message forwarding. This is not a live tunnel or browser test.
