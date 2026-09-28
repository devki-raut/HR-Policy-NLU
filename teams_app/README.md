# Teams personal-tab package

The Teams app now uses the unified server in `api/main.py`. There is no separate Teams server or port 8001. The UI assets live in `api/static/`; this folder contains only Teams packaging code and configuration.

## Local run

From the repository root run `make local-start`, or run `make actions`, `make serve`, and `make api` in separate terminals.

- Chat screen: http://localhost:8000/
- API documentation: http://localhost:8000/docs

Message flow: Teams tab/browser → API `/chat` → Rasa :5005 → actions :5055 → policy index. PDF uploads and policy listing are on the same API. The chat UI does not itself offer policy administration controls.

## Authentication

When `HR_API_KEY` is configured, the chat route still requires it. For local testing, expand **API authentication** on the page and enter your key; it is used only in request headers and not persisted. The server never embeds its configured key in the page. Do not distribute the shared admin API key to employees: production requires validated employee authentication and separate authorization for policy writes. Teams context is not proof of identity.

## Package for Teams

1. Host the unified API behind HTTPS at the root of your domain, forwarding to port 8000. Configure employee authentication and policy-admin authorization before using confidential policies.
2. Copy `config.example.json` to `config.local.json` in this folder.
3. Generate a stable package ID with `python3 -c 'import uuid; print(uuid.uuid4())'`.
4. Set `base_url` to the unified API's public HTTPS origin. Fill in the developer name and real website, privacy and terms URLs. No Azure Bot registration is required for this personal tab.
5. From the repository root run:

```bash
python3 teams_app/package.py --config teams_app/config.local.json
```

The ZIP at `teams_app/dist/hr-policy-teams.zip` contains a personal-tab manifest and 192×192 color / 32×32 outline icons. It points directly to the API's `/` chat page. If you already built a package with the old host, rebuild it using the new API origin.

Validate/import the ZIP through Teams Developer Portal and install it through your tenant's custom-app process. HTTPS hosting and tenant installation are still required; local preview does not install the app in Teams. The package contains metadata and icons, not the backend.

This is a personal tab, not a native Bot Framework conversation bot. See the native-bot notes below for that separate integration.

References: [Teams tab requirements](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/tab-requirements), [manifest schema](https://learn.microsoft.com/en-us/microsoftteams/platform/resources/schema/manifest-schema).

# Microsoft Teams integration

The project uses Rasa's built-in Microsoft Bot Framework channel. This connector is a legacy integration; validate compatibility with your Azure registration before deployment, especially tenant-specific authentication. New Azure registrations may require a single-tenant setup that the stock Rasa 3.6 connector does not support. Such deployments require a tenant-aware connector or an authenticated Microsoft Agents SDK bridge; do not disable authentication to work around this.

1. Obtain an Azure bot registration with a compatible identity and enable its Microsoft Teams channel. Record its application ID and secret through your secret manager.
2. Copy `credentials.example.yml` to the gitignored `credentials.yml`. Enable `botframework` and its environment variable placeholders. Export `MICROSOFT_APP_ID` and `MICROSOFT_APP_PASSWORD` in the Rasa server environment.
3. Ingest approved policies, train the Rasa model, start the action server, and start Rasa with `rasa run --credentials credentials.yml --interface 127.0.0.1`.
4. Put a TLS reverse proxy in front of Rasa. Route only `/webhooks/botframework/webhook` externally; keep REST and action-server endpoints private. Set the Azure messaging endpoint to `https://YOUR_HOST/webhooks/botframework/webhook`.
5. In Teams Developer Portal create an organization app, configure a personal-scope bot using the same application ID, and supply your organization-owned icons, descriptions, privacy URL and terms URL. Validate and export its app package, then install through your tenant's permitted custom-app process.
6. Run the UAT checklist in `API.md` in Teams. Check token validation, tenant compatibility, response delivery, citations and unauthorized-request rejection before enabling employee access.

No credentials, organization branding or tenant IDs are fabricated or committed. A deployable Teams package must be generated using your actual registration and organization metadata.

References:
- [Rasa Microsoft Bot Framework connector](https://legacy-docs-oss.rasa.com/docs/rasa/connectors/microsoft-bot-framework/)
- [Microsoft bot authentication types](https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-concept-authentication-types)

## Manifest, icons and JavaScript placement

The source package files are now explicit:

```text
teams_app/
  appPackage/
    manifest.json       # Template; ${...} placeholders are resolved by package.py
    color.png           # 192×192 color icon
    outline.png         # 32×32 transparent outline icon
  package.py
  config.example.json
api/
  main.py               # Serves the UI and REST API on one HTTPS origin
  static/
    index.html          # Hosted tab page
    app.js              # Chat UI and TeamsJS initialization
    style.css
```

The manifest's `staticTabs[0].contentUrl` points to the hosted HTML page, not to a JavaScript file. `validDomains` lists the host. The packaging command resolves the template using `config.local.json` and writes both `dist/manifest.json` for inspection and `dist/hr-policy-teams.zip`. Do not upload the placeholder template directly.

The ZIP contains only the resolved manifest and icons at its root. HTML, CSS, JavaScript and Python remain on your web server. Teams loads the tab as a hosted webpage. Keeping JS under `api/static/` is our single-server layout choice, not a Teams requirement; FastAPI serves those files while the browser executes them. This preserves the combined deployment requested for this project.

### How JavaScript is loaded

`api/static/index.html` loads the SDK first, followed by our script:

```html
<script defer src="https://res.cdn.office.net/teams-js/2.19.0/js/MicrosoftTeams.min.js"></script>
<script defer src="/static/app.js"></script>
```

Both are classic deferred scripts, so they execute in document order after HTML parsing. `app.js` calls `microsoftTeams.app.initialize()` before `app.getContext()` and theme handling. It uses `fetch('/chat', ...)` against the same host. Teams context is used for presentation, not authentication.

To add a new script:

1. Save it under `api/static/`, for example `policy-help.js`.
2. Add `<script defer src="/static/policy-help.js"></script>` to `index.html`, after any scripts it depends on.
3. Verify it loads at `http://localhost:8000/static/policy-help.js` and check the browser console/network panel.
4. Deploy the changed static files with the API. No JS filename is added to the Teams manifest or ZIP. Rebuild the package when the host or manifest changes.

Do not mix bare npm imports with this plain-script approach. A frontend bundler would be needed for `import ... from '@microsoft/teams-js'`; this small app uses the browser CDN SDK and requires no Node build step. Never put server credentials in JavaScript.

The UI response has a Teams-compatible `frame-ancestors` policy, including `*.cloud.microsoft`. Production Teams tabs require HTTPS. Local browser preview works without a Teams host; initialization failure displays the browser-preview status.

Official references:
- [Package a Teams app](https://learn.microsoft.com/en-gb/microsoftteams/platform/concepts/build-and-test/apps-package)
- [Tabs and contentUrl](https://learn.microsoft.com/microsoftteams/platform/tabs/what-are-tabs)
- [TeamsJS client library](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/using-teams-client-library)
- [Tab requirements](https://learn.microsoft.com/en-us/microsoftteams/platform/tabs/how-to/tab-requirements)
