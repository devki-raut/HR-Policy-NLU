# Deploy this personal-tab app to Teams

1. Run `make deploy-setup` and `make local-start`. Verify the chat UI at `http://localhost:8000/` and API at `/docs`.
2. Host port 8000 behind your organization's HTTPS gateway at a stable domain root. Keep Rasa 5005, actions 5055 and model ports private. Implement validated employee authentication and separate policy-write authorization before publishing real company policies. Teams embedding is not authentication; the shared API key is not employee SSO.
3. Copy `teams_app/config.example.json` to `teams_app/config.local.json`. Set `base_url` to the public API origin, provide real developer/privacy/terms URLs, and generate a stable package UUID with `python3 -c 'import uuid; print(uuid.uuid4())'`.
4. Run `python3 teams_app/package.py --config teams_app/config.local.json`. Inspect `teams_app/dist/manifest.json`; ensure no placeholders remain and `contentUrl` is your HTTPS origin.
5. Validate/import `teams_app/dist/hr-policy-teams.zip` in Teams Developer Portal. In Teams choose Apps → Manage your apps → Upload an app → Upload a custom app (labels vary by client). If unavailable, ask the Teams administrator to permit custom apps or publish through the organization catalog.
6. Open HR Policy → Ask HR. Test questions for each document, citations, unknown questions, unavailable backend, new conversation and Teams themes. Review the checklist in `API.md`.

The ZIP contains manifest/icons only; the HTTPS site hosts HTML/JS. This is a personal tab, not a conversation bot; no Azure Bot registration is needed for this tab. Increment manifest `version` when changing the package, keep the same app ID, rebuild and update the tenant installation. Hosted JS changes only require redeploying the server assets and refreshing the tab.

Live tenant installation and SSO are not completed by the local scripts.

References: [Microsoft custom-app upload](https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/deploy-and-publish/apps-upload), [Teams tabs](https://learn.microsoft.com/microsoftteams/platform/tabs/what-are-tabs), [custom-app policies](https://learn.microsoft.com/en-us/microsoftteams/teams-custom-app-policies-and-settings).
