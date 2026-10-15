Configure the first superadmin by object ID, email, or both:

```dotenv
MICROSOFT_APP_ID=<bot-application-client-uuid>
MICROSOFT_TENANT_ID=<directory-tenant-uuid>
TEAMS_SUPERADMIN_EMAILS=admin@example.com,second-admin@example.com
# Optional existing object-ID allowlist remains supported:
TEAMS_SUPERADMIN_OBJECT_IDS=<admin-user-object-uuid>
TEAMS_BROADCAST_DB=teams/broadcast.sqlite3
```

`TEAMS_BROADCAST_TENANT_ID` can explicitly override the broadcast tenant;
otherwise `MICROSOFT_TENANT_ID` is used. This must be the actual tenant UUID,
not `organizations`. Do not put email addresses in `TEAMS_SUPERADMIN_OBJECT_IDS`.

Each email-configured administrator should message the bot in a personal Teams
chat. The bot retrieves the sender's member profile from Teams, matches their
verified email or UPN within the configured tenant, and records their object ID.
An email typed into a message or supplied by a guest cannot grant privileges.
The first binding stays attached to that object ID; email reassignment to a
different object ID does not automatically transfer administrator access.
If the member lookup is unavailable, email bootstrap fails closed; an explicit
object-ID allowlist remains available.

An existing superadmin can then send these commands in the same personal chat:

```text
/superadmin-add colleague@example.com
/superadmin-list
/superadmin-remove colleague@example.com
```

The colleague must have messaged this bot in a personal chat first, so their
verified profile is known. Commands also accept a registered user's object ID.
Ambiguous email matches are rejected; use the exact object ID instead.
Each delegated grant/revocation is recorded with the acting administrator.
Delegated superadmins have the same management and broadcast privileges.
Environment-configured superadmins must be removed from application settings;
chat commands cannot overwrite or remove those bootstrap accounts.

Broadcast workflow:

```text
/broadcast Company announcement text
/broadcast-confirm <preview-id>
/broadcast-status <preview-id>
/broadcast-recipients <preview-id> 1
```

Preview never sends. Confirmation must come from the original superadmin and
chat within ten minutes. The audience is the set of registered personal Teams
conversation references for this tenant, captured when users interact with the
bot. It does not enumerate all tenant employees or send merely from an email
list. Users who have not interacted with the bot are not yet in the audience.

On completion, a report lists recipient names and delivery states. Up to ten
recipients appear directly; **View recipient names and delivery status** opens
a Teams dialog, with twenty rows per page and Previous/Next controls. The
`/broadcast-recipients` command provides equivalent pagination in chat if a
client cannot open the popup. Names/emails are snapshotted at preview time;
older records without profile data show their object ID. Each report request
rechecks the administrator's current privileges, original tenant, owner and chat.

`accepted` means Teams accepted the send, not that the recipient read it.
`failed_or_unknown` includes errors whose delivery outcome is uncertain; the bot
does not automatically resend them. `pending`/`sending` retain their distinct
states while a broadcast is in progress.

This uses [Teams dialog fetch/submit responses](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/task-modules/task-modules-bots).
No public report URLs or bearer tokens embedded in links are needed.

The database also holds delegated grants, email-to-object-ID bindings and the
recipient registry. With `/tmp` storage these can be lost when an instance is
replaced and are not shared across instances. Use a writable persistent
`TEAMS_BROADCAST_DB` location when this state must survive restarts.
