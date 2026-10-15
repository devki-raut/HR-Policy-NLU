Configure Azure App Service application settings as follows (use your actual
tenant UUID and the application registration's client UUID):

```dotenv
PUBLIC_BASE_URL=https://dockerpoc-webapp01.azurewebsites.net/employeeassist
DATA_ROOT=/tmp
WEB_PORT=8608
ENTRA_AUTH_REQUIRED=true
ENTRA_TENANT_ID=<directory-tenant-uuid>
MICROSOFT_TENANT_ID=<same-directory-tenant-uuid>
ENTRA_CLIENT_ID=<application-client-uuid>
MICROSOFT_APP_ID=<same-application-client-uuid>
ENTRA_API_RESOURCE=api://dockerpoc-webapp01.azurewebsites.net/botid-<application-client-uuid>
ENTRA_REDIRECT_URI=https://dockerpoc-webapp01.azurewebsites.net/employeeassist
```

`ENTRA_REDIRECT_URI` is optional: the browser derives this URL from the page's
public base path, without a trailing slash. Register that exact URL as a
**Single-page application** redirect URI in Microsoft Entra. Browser sign-in
uses redirect handling and a validated ID token; the Teams tab first requests
Teams SSO. Mail authorization still uses the current Teams SSO flow or delegated
Graph consent in a normal browser; see [Teams mail SSO](teams-mail-sso.md).

`ENTRA_*` settings configure web API sign-in. `MICROSOFT_*` settings configure
the Bot Framework adapter. For this deployment they refer to the same app and
tenant, but tenant ID and application/client ID are different IDs.
`organizations` is a multitenant authority selector, not a tenant UUID. Use the
actual tenant UUID for a single organization and for superadmin broadcasts.

With `WEBSITE_INSTANCE_ID` present, runtime files automatically default to
`/tmp/employeeassist` even if `DATA_ROOT` is omitted. `DATA_ROOT` sets the parent
directory; `DATA_ROOT=/tmp` produces the same location. Local development keeps
the existing project-relative paths unless `DATA_ROOT` is set. Web port defaults
to 8608 on Azure and 8610 locally; `WEB_PORT`, `PORT`, then `WEBSITES_PORT` override
it in that order. Configure the Azure proxy/container target port to match.

Runtime directories include `identity`, `logs`, `darwinbox`, `private` (mail
drafts), `teams` (broadcasts), `cache`, `jupyter/runtime`, and `policies`.
Bundled policy assets are copied into the writable policy directory once;
subsequent starts preserve runtime changes. The bundled FAQ embedding cache
remains a read-only fallback until a runtime cache is needed.

Remove obsolete absolute `/home/data/...` overrides from application settings,
or replace them with writable locations. Explicit per-file settings continue to
take precedence: `IDENTITY_STORE_PATH`, `CONVERSATION_LOG_PATH`,
`DARWINBOX_EMPLOYEE_STORE_PATH`, `DARWINBOX_EMPLOYEE_MAP_FILE`, `TEAMS_TICKET_DB`,
`TICKET_MAIL_LOG_PATH`, `TEAMS_BROADCAST_DB`, `FAQ_EMBEDDINGS_PATH`, `POLICY_INDEX`,
`POLICY_PDF_DIR`, `POLICY_MARKDOWN_DIR`, and `POLICY_DOCUMENTS_DIR`. Relative overrides resolve beneath
the runtime directory on Azure. For example:

```dotenv
TEAMS_BROADCAST_DB=teams/broadcast.sqlite3
```

This resolves to `/tmp/employeeassist/teams/broadcast.sqlite3` without a hardcoded
home directory. `/tmp` data is temporary and is not shared across instances;
use a writable persistent mount or external storage when that is required.

The reverse proxy can preserve or strip `/employeeassist/`; page assets, API
calls, legal links, policy links, and Jupyter forwarding support both forms.
The Teams upload package is
`teams_app/app_package/upload/EmployeeAssist-1.0.6.zip`; its tab and legal URLs
use lowercase `/employeeassist/`, preserving current commands and the SSO resource.
Update the Azure Bot messaging endpoint to
`https://dockerpoc-webapp01.azurewebsites.net/employeeassist/api/messages`.

Configuration files containing credentials were not changed by this update.

JupyterLab's URL is computed by appending `/jupyter/lab` to the configured public
base URL. Keep `PUBLIC_BASE_URL`/`APP_PUBLIC_URL` at the application root; do not
include `/jupyter/lab` in these settings, because the same base serves chat,
authentication, policy links and the bot. `PUBLIC_BASE_URL` takes precedence when
both are set. For Azure the resulting URL is
`https://dockerpoc-webapp01.azurewebsites.net/employeeassist/jupyter/lab`;
for a root-hosted ngrok URL it is
`https://flounder-sphere-dab.ngrok-free.dev/jupyter/lab`.
There is no Jupyter link in the chat UI. The launcher and Jupyter config both
set the full server base path. Login forms, assets, cookies, API and WebSocket
paths remain beneath that prefix; legacy upstream redirects and encoded `next`
targets are normalized as well. Restart the entire deployment, including the
private Jupyter server, after changing the public base URL. See
[Jupyter subroute verification](jupyter-subroute.md) for the ngrok/nginx setup.

See [superadmin configuration and broadcast recipient reports](broadcast-superadmins.md)
for email bootstrap, delegated administrator commands and the Teams popup.
