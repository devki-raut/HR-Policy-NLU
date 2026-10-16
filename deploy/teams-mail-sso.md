# Deploy the editable request form and Teams mailbox SSO

The request flow is now a single mail composer with editable To, CC, Subject and
Body. Existing issue text prepopulates the message. No error/start-time chat
steps are required. Outside the ticket flow, IT questions continue to receive
FAQ guidance. The bot sends only after the user submits Raise a request.

In the web composer, Enter in a single-line field submits; Ctrl+Enter or
Command+Enter submits from Body. Plain Enter adds a line in Body. In a native
Teams adaptive card, use Raise a request; Teams controls keyboard behaviour.
Open in Outlook uses the latest edited values and never sends automatically.

## Microsoft configuration required before live SSO testing

This repository contains a combined tab and bot. Configure the app registration
used by `webApplicationInfo.id` as follows:

1. Expose the API resource matching both version 1.0.10 manifests:
   `api://dockerpoc-webapp01.azurewebsites.net/botid-9e65e729-0e91-4a00-a03d-e8b9d3e9e841`.
   Expose delegated `access_as_user`. The `botid-` prefix is required for this
   combined Teams bot/tab setup. If the deployment uses another app or hostname,
   update both manifests and `ENTRA_API_RESOURCE` consistently.
2. Pre-authorize Teams desktop/mobile client
   `1fec8e78-bce4-4aaf-ab1b-5451cc387264` and Teams web client
   `5e3ce6c0-2b1f-4285-8d4b-75ee78787346` for `access_as_user`.
3. Add Microsoft Graph **delegated** `User.Read` and `Mail.Send` permissions and
   arrange consent under your tenant's policy. No application Mail.Send or
   permission to send from other users is used by this code.
4. Add the Web redirect URI
   `https://token.botframework.com/.auth/web/redirect`.
5. In the Azure Bot resource, create/update the Microsoft Entra ID v2 OAuth
   connection. Use the same app client ID, valid client-secret **value**, real
   tenant UUID and scopes `User.Read Mail.Send`. Set **Token Exchange URL** to
   the API resource in step 1 (without `/access_as_user`).
6. Set `TEAMS_MAIL_OAUTH_CONNECTION` to the exact Azure Bot connection name.
   First-time consent may still be required. Subsequent approved sends reuse
   Teams SSO and do not ask the user to click Raise a request a second time.

References:
- https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/authentication/bot-sso-register-aad
- https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/authentication/bot-sso-overview
- https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow

## Deployment environment

Keep credentials in the hosting environment; do not put them in the app package.
These are placeholders, not valid credentials:

```dotenv
MICROSOFT_APP_ID=<bot-client-id>
MICROSOFT_APP_PASSWORD=<bot-client-secret-value>
MICROSOFT_TENANT_ID=<tenant-uuid>
ENTRA_TENANT_ID=<same-tenant-uuid>
TEAMS_MAIL_OAUTH_CONNECTION=<exact-Azure-Bot-OAuth-connection-name>
ENTRA_API_RESOURCE=api://dockerpoc-webapp01.azurewebsites.net/botid-9e65e729-0e91-4a00-a03d-e8b9d3e9e841
JUPYTER_TOKEN=<existing-strong-token>
```

If ENTRA_CLIENT_ID is a separate app registration, configure its corresponding
ENTRA_CLIENT_SECRET for web Teams SSO's on-behalf-of exchange. Otherwise the
existing MICROSOFT_APP_PASSWORD is used. The SSO assertion and Graph tokens are
not persisted in application draft storage or audit logs.

## Release

- Retrained model: `models/20261015-182438-symmetric-patch.tar.gz`.
- Upload/update the Teams app with
  `teams_app/app_package/upload/EmployeeAssist-1.0.10.zip`.
- From a deployment shell with the real environment available, run
  `make restart_deployment` (or restart the configured hosting service).
- Verify `/health`, then open a fresh chat card using `help` and Create IT ticket.
- Review an edited To/CC/Subject/Body, submit Raise a request, and verify the
  same user's Sent Items and recipients. First verify tenant consent/SSO setup;
  automated tests do not send a real message.

The Codex session cannot read the restricted `.env`, has no process credentials,
and cannot access Docker or systemd. It cannot configure Entra/Azure Bot or
confirm a production restart. The release is prepared, not deployed.
