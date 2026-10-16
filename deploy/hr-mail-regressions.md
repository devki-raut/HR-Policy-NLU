# Holiday/leave responses and editable email drafts

The main checkout and `/home/ojas/HRv6/extracted/HR-Policy-NLU` received targeted
fixes for the reported symptoms. This does not replace HRv6's other features or
its older interactive mail authorization flow with the latest release.

- `Holiday calendar`, `My leave balance` and `Next holidays` are normalized to
  the canonical Rasa questions. A valid holiday/leave card can render even when
  the service returns structured metadata without accompanying text.
- The action package loads local settings at startup with `override=False`,
  including when an action worker is started separately. Existing Azure process
  settings take precedence. The leave endpoint default uses the documented
  `/leavesactionapi/leavebalance`; absolute URLs and configured paths still win.
- Configuration failures log the missing setting name. The general user-facing
  message is retained without exposing secret values.
- Holiday employee lookup now runs inside the error handler. A missing or invalid
  directory configuration produces an integration reply instead of aborting the
  holiday action before it can return any result.
- A generic Create IT ticket command produces actual editable subject/body
  values, rather than blank values with example placeholders. Existing issue
  descriptions and known user names remain in the generated text. HRv6's web
  preview now has editable fields and saves their edits before sending; its
  Outlook link follows the edits as well.

These are configuration and response-rendering fixes. The message “Darwinbox
leave-balance adapter is not configured” means the worker raised a configuration
error before receiving a valid balance response. It does not establish that
Darwinbox itself is down. The code cannot restore missing credentials or API
permissions. Keep the existing values in Azure App Service settings and restart
the complete deployment, especially the action worker.

From a deployment shell with the same environment as that worker, run:

```bash
python scripts/check_darwinbox_config.py
```

It reports auth mode and missing setting names only, without loading environment
files or printing values. In API-key mode, the leave key is
`DARWINBOX_LEAVE_API_KEY`. Without it the existing SHA-512 fallback expects
`DARWINBOX_ADMIN_EMAIL`, `DARWINBOX_SECRET_KEY` and `DARWINBOX_UID`. A partially
configured Basic Auth pair is rejected. Holidays need `DARWINBOX_HOLIDAY_API_KEY`
and either holiday-specific credentials or the leave username/password.
See `darwinbox-settings.example`; do not overwrite real credentials with examples.

The current release still requires each personal leave request to map to the
signed-in employee; it never substitutes a default employee's personal balance.
The company holiday calendar can use its configured default employee context.

The regression suite uses mocked API responses and never sends mail or requests
real employee balances. Run it with the standard `unittest discover` command in
the release guide. Live settings, API access and account mappings still require
verification in the deployed environment.
