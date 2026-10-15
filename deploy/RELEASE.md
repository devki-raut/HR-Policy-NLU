# EmployeeAssist 1.0.6

This release contains the current HR/IT classifier, FAQ data and trained Rasa
model, editable To/CC mail forms, Teams mailbox SSO, admin broadcasts, email
bootstrap and delegated superadmins, recipient popup reports, Azure writable
paths and the prefixed Jupyter proxy.

The Jupyter server's base URL and default Lab path are now explicit launcher
arguments, and the proxy repairs encoded login `next` targets as well as
Location paths. The chat UI Jupyter link has been removed. See
[jupyter-subroute.md](jupyter-subroute.md) for the isolated nginx/ngrok subroute
setup and real Jupyter handler tests that run without network sockets.

Deployment auth fixes:

- Native Teams messages use the shared application chat service after Bot
  Framework authentication. They no longer POST identity metadata to `/chat`.
  `EMPLOYEEASSIST_API_URL` is no longer used; `RASA_URL` points to the private
  Rasa service for both web and bot modes.
- Browser chat identity comes from a verified Microsoft token. JSON metadata
  cannot assert an employee, tenant or Teams identity. Tracker IDs are scoped
  by channel, tenant and object ID; optional guests have a separate namespace.
  Existing conversations begin fresh under these IDs.
- Microsoft sign-in is required by default. Explicit guest mode supports public
  policy/IT questions, but personal leave balances require sign-in and an
  employee mapping. Missing mappings never fetch a default employee's balance.
- Policy uploads through `/policies/upload` and multipart `/ask` require a
  currently authorized superadmin, even if guest chat is enabled. `/ask` without
  a file follows normal chat authentication. `/nlu/parse` follows chat auth.
- Superadmins configured by email must first message the bot in a personal
  Teams chat to bind their verified profile; that object ID can then publish
  policies with a verified browser token. Environment object IDs work directly.
- Rasa explicitly enables its REST connector and binds to localhost, independent
  of credential files. Jupyter config/data/runtime and IPython writes use the
  writable runtime directory in Azure.

## Configure and start

1. Extract the code bundle to the project directory. Keep credentials and the
   runtime employee directory in the deployment environment; they are excluded
   from the bundle. The original IT report is included for FAQ regeneration and
   source-coverage tests; Docker excludes that report from the runtime image.
2. Apply [azure-settings.example](azure-settings.example) as Azure App Service
   settings. Replace placeholders, configure the actual tenant UUID, retain the
   existing Darwinbox settings, and set the Azure container target port to 8608.
   Keep the public base URL at `/employeeassist/`, without `/jupyter/lab`.
3. Configure Entra redirect URIs, delegated Graph consent and the Azure Bot OAuth
   connection as described in [teams-mail-sso.md](teams-mail-sso.md). Set the Bot
   messaging endpoint to the public `/employeeassist/api/messages` URL.
4. In the deployment environment, run `python deploy/check_deployment.py`. This
   reads process environment variables only, does not load credential files and
   prints checks without secret values. All checks should pass.
5. Build the existing deployment target:
   `docker build --target deployment -t employeeassist:1.0.6 .`
   Push that image through the existing Azure deployment pipeline and restart
   the complete deployment, including Jupyter. The Docker entry point is
   `python scripts/run_deployment.py web`.
6. Upload `teams_app/app_package/upload/EmployeeAssist-1.0.6.zip` in Teams.

The bundle includes `models/20261015-182438-symmetric-patch.tar.gz`. No NLU data
changed in these auth fixes, so retraining that model is unnecessary. Startup
still needs the SentenceTransformer model cache or access to download
`sentence-transformers/all-MiniLM-L6-v2`; it is not bundled in the source archive.
The Jupyter Python environment is installed separately by the Dockerfile.

`DATA_ROOT=/tmp` uses `/tmp/employeeassist`. Runtime grants, user registrations,
drafts, logs and edited policies can be lost when an instance is replaced and
are not shared across instances. Use a writable persistent mount when required.
Only the public web port should be exposed; keep Rasa, actions and Jupyter private.

## Validation

Offline regression command:

```bash
PYTHON_DOTENV_DISABLED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv-runtime/bin/python -m unittest discover -s scripts/ticket_checks -p 'test_*.py'
```

The suite covers classifier/FAQ fixtures, drafts, Teams SSO handlers, broadcasts,
role grants/revocations, recipient reports, writable paths, Azure/ngrok redirects,
guest identity spoofing, upload authorization and native Teams chat under
mandatory sign-in. Tests do not send real emails or broadcasts.

After deployment, verify authenticated HR/IT chat, leave mapping, ticket sending
with edited To/CC, `/employeeassist/jupyter` and `/employeeassist/jupyter/lab`,
superadmin grant/revocation and a broadcast recipient popup with test users.
Confirm anonymous and nonadmin policy uploads return 401/403. A successful
`/health` response alone does not verify these integrations.

The code bundle is prepared locally. The container build, live Azure redirect,
Teams consent/SSO, real mail delivery and popup rendering could not be verified
in the restricted development environment.

Generate the source archive with `python scripts/package_release.py`. It includes
a per-file SHA-256 manifest and writes a checksum beside the archive under
`artifacts/releases`.
