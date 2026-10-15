"""Tenant-scoped, superadmin-only broadcasts over authenticated Teams turns."""
import asyncio
import json
import os
from actions.runtime_paths import runtime_path
from pathlib import Path
import secrets
import sqlite3
import time
import re
from contextlib import contextmanager

from botbuilder.core import TurnContext, CardFactory, MessageFactory
from botbuilder.schema import ConversationReference, Activity, InvokeResponse

ROOT = Path(__file__).resolve().parents[2]

def database_path():
    return runtime_path('teams/broadcast.sqlite3', ROOT/'artifacts/teams/broadcast.sqlite3', 'TEAMS_BROADCAST_DB', ROOT)

@contextmanager
def database():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS recipients (tenant TEXT, oid TEXT, reference TEXT,
        PRIMARY KEY (tenant, oid));
    CREATE TABLE IF NOT EXISTS broadcasts (id TEXT PRIMARY KEY, tenant TEXT, owner TEXT,
        conversation TEXT, message TEXT, created REAL, status TEXT);
    CREATE TABLE IF NOT EXISTS deliveries (broadcast TEXT, oid TEXT, reference TEXT,
        status TEXT, PRIMARY KEY (broadcast, oid));
    CREATE TABLE IF NOT EXISTS recipient_profiles (tenant TEXT, oid TEXT,
        name TEXT, email TEXT, upn TEXT, verified INTEGER, PRIMARY KEY (tenant, oid));
    CREATE TABLE IF NOT EXISTS superadmins (tenant TEXT, oid TEXT, source TEXT,
        email TEXT, granted_by TEXT, created REAL, PRIMARY KEY (tenant, oid));
    CREATE UNIQUE INDEX IF NOT EXISTS environment_email_binding
        ON superadmins(tenant,email) WHERE source='environment_email';
    CREATE TABLE IF NOT EXISTS admin_audit (tenant TEXT, actor TEXT, target TEXT,
        action TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS delivery_profiles (broadcast TEXT, oid TEXT,
        name TEXT, email TEXT, PRIMARY KEY (broadcast, oid));
    ''')
    try:
        with db:
            yield db
    finally:
        db.close()

def tenant():
    return os.getenv('TEAMS_BROADCAST_TENANT_ID', os.getenv('MICROSOFT_TENANT_ID', '')).strip().casefold()

def configured_values(name):
    return {v.strip().casefold() for v in os.getenv(name, '').split(',') if v.strip()}

def environment_admin(oid, row=None):
    return oid in configured_values('TEAMS_SUPERADMIN_OBJECT_IDS') or bool(
        row and row['source'] == 'environment_email'
        and row['email'] in configured_values('TEAMS_SUPERADMIN_EMAILS'))

def is_superadmin(identity):
    oid = str(identity.get('aad_object_id') or '').casefold()
    if not (oid and os.getenv('MICROSOFT_APP_ID', '').strip() and tenant()
            and str(identity.get('tenant_id') or '').casefold() == tenant()):
        return False
    if environment_admin(oid):
        return True
    with database() as db:
        row = db.execute('SELECT * FROM superadmins WHERE tenant=? AND oid=?', (tenant(), oid)).fetchone()
    return bool(row and (row['source'] == 'delegated' or environment_admin(oid, row)))

def remember(context, identity):
    activity = context.activity
    if (not os.getenv('MICROSOFT_APP_ID', '').strip() or activity.channel_id != 'msteams' or activity.conversation.conversation_type != 'personal'
            or not tenant() or str(identity.get('tenant_id') or '').casefold() != tenant()
            or not identity.get('aad_object_id') or not activity.service_url):
        return
    reference = TurnContext.get_conversation_reference(activity).serialize()
    with database() as db:
        db.execute('INSERT OR REPLACE INTO recipients VALUES (?, ?, ?)',
                   (tenant(), identity['aad_object_id'].casefold(), json.dumps(reference)))
        oid = identity['aad_object_id'].casefold()
        verified = identity.get('profile_verified') is True
        email = str(identity.get('email') or '').strip().casefold() if verified else ''
        upn = str(identity.get('user_principal_name') or '').strip().casefold() if verified else ''
        name = str(identity.get('display_name') or '').strip()[:160]
        # Failed member lookups must not erase a previously verified profile.
        db.execute('''INSERT INTO recipient_profiles VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant,oid) DO UPDATE SET
            name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE name END,
            email=CASE WHEN excluded.verified=1 THEN excluded.email ELSE email END,
            upn=CASE WHEN excluded.verified=1 THEN excluded.upn ELSE upn END,
            verified=MAX(verified,excluded.verified)''', (tenant(), oid, name, email, upn, int(verified)))
        seed = next((v for v in [email, upn] if v and v in configured_values('TEAMS_SUPERADMIN_EMAILS')), None)
        if seed:
            inserted = db.execute('INSERT OR IGNORE INTO superadmins VALUES (?, ?, ?, ?, ?, ?)',
                (tenant(), oid, 'environment_email', seed, 'environment', time.time())).rowcount
            if inserted:
                db.execute('INSERT INTO admin_audit VALUES (?, ?, ?, ?, ?)',
                    (tenant(), 'environment', oid, 'bind_email', time.time()))


async def manage_admins(context, identity, command, argument):
    actor = identity['aad_object_id'].casefold()
    with database() as db:
        if command == '/superadmin-list':
            rows = db.execute('''SELECT a.*, p.name FROM superadmins a LEFT JOIN recipient_profiles p
                ON p.tenant=a.tenant AND p.oid=a.oid WHERE a.tenant=? ORDER BY a.created''', (tenant(),)).fetchall()
            lines = ['Configured object IDs: ' + (', '.join(sorted(configured_values('TEAMS_SUPERADMIN_OBJECT_IDS'))) or '(none)'),
                     'Configured bootstrap emails: ' + (', '.join(sorted(configured_values('TEAMS_SUPERADMIN_EMAILS'))) or '(none)')]
            lines += [f"{r['name'] or r['email'] or r['oid']} — {r['oid']} ({r['source']})" for r in rows
                      if r['source'] == 'delegated' or environment_admin(r['oid'], r)]
            reply = '\n\n'.join(lines)
        elif not argument or len(argument) > 320 or any(v.isspace() for v in argument):
            reply = f'Use {command} <email-or-object-id>.'
        else:
            matches = db.execute('''SELECT * FROM recipient_profiles WHERE tenant=? AND
                (oid=? OR (verified=1 AND (email=? OR upn=?)))''',
                (tenant(), argument.casefold(), argument.casefold(), argument.casefold())).fetchall()
            if len(matches) != 1:
                reply = 'No unique verified Teams user matches. Ask the user to message this bot in a personal chat, then retry with their email or object ID.'
            else:
                target = matches[0]
                current = db.execute('SELECT * FROM superadmins WHERE tenant=? AND oid=?', (tenant(), target['oid'])).fetchone()
                if environment_admin(target['oid'], current):
                    reply = ('This user is already an environment-configured superadmin.' if command == '/superadmin-add'
                             else 'This is an environment-configured superadmin. Remove its object ID or bootstrap email from the application settings to revoke it.')
                else:
                    if command == '/superadmin-add':
                        db.execute('INSERT OR REPLACE INTO superadmins VALUES (?, ?, ?, ?, ?, ?)',
                            (tenant(), target['oid'], 'delegated', target['email'], actor, time.time()))
                        action = 'grant'
                    else:
                        db.execute('DELETE FROM superadmins WHERE tenant=? AND oid=?', (tenant(), target['oid']))
                        action = 'revoke'
                    db.execute('INSERT INTO admin_audit VALUES (?, ?, ?, ?, ?)', (tenant(), actor, target['oid'], action, time.time()))
                    reply = f"Superadmin access {'granted to' if action == 'grant' else 'removed for'} {target['name'] or target['email'] or target['oid']} ({target['oid']})."
    await context.send_activity(reply)


def authorized_context(context, identity):
    return (context.activity.channel_id == 'msteams' and is_superadmin(identity)
            and context.activity.conversation.conversation_type == 'personal')


def report_rows(context, identity, key):
    if not authorized_context(context, identity):
        return None, []
    with database() as db:
        row = db.execute('SELECT * FROM broadcasts WHERE id=? AND tenant=? AND owner=? AND conversation=?',
            (key, tenant(), identity['aad_object_id'].casefold(), context.activity.conversation.id)).fetchone()
        rows = db.execute('''SELECT d.oid,d.status,COALESCE(s.name,p.name,'') AS name,
            COALESCE(s.email,p.email,'') AS email FROM deliveries d
            LEFT JOIN delivery_profiles s ON s.broadcast=d.broadcast AND s.oid=d.oid
            LEFT JOIN recipient_profiles p ON p.tenant=? AND p.oid=d.oid
            WHERE d.broadcast=? ORDER BY name COLLATE NOCASE, d.oid''', (tenant(), key)).fetchall() if row else []
    return row, rows


def display_recipient(row):
    # Teams TextBlocks support Markdown: escape names instead of creating links.
    value = (row['name'] or row['email'] or row['oid']).replace('\n', ' ').replace('\r', ' ')
    return re.sub(r'([\\`*_{}\[\]()<>!#|])', r'\\\1', value)[:240]


def recipient_card(key, rows, page=0, popup=True):
    size = 20
    pages = max(1, (len(rows)+size-1)//size)
    page = max(0, min(page, pages-1))
    body = [{'type': 'TextBlock', 'text': f'Broadcast recipients — page {page+1}/{pages} ({len(rows)} total)', 'weight': 'Bolder', 'wrap': True},
            {'type': 'TextBlock', 'text': 'Accepted = accepted by Teams for sending; it does not confirm that the user read it.', 'wrap': True, 'isSubtle': True}]
    body += [{'type': 'TextBlock', 'text': f"{display_recipient(r)} — {r['status']}", 'wrap': True} for r in rows[page*size:(page+1)*size]]
    if not rows:
        body.append({'type': 'TextBlock', 'text': 'No registered recipients.'})
    actions = []
    for label, next_page in [('Previous', page-1), ('Next', page+1)]:
        if 0 <= next_page < pages:
            data = {'action': 'broadcast_recipients', 'broadcast_id': key, 'page': next_page} if popup else {
                'msteams': {'type': 'messageBack', 'text': f'/broadcast-recipients {key} {next_page+1}', 'displayText': label}}
            actions.append({'type': 'Action.Submit', 'title': label, 'data': data})
    return {'type': 'AdaptiveCard', 'version': '1.4', 'body': body, 'actions': actions}


async def send_report(context, identity, key):
    row, rows = report_rows(context, identity, key)
    if row is None:
        await context.send_activity('Broadcast not found for your account and chat.')
        return
    counts = {status: sum(r['status'] == status for r in rows) for status in sorted({r['status'] for r in rows})}
    body = [{'type': 'TextBlock', 'text': f"Broadcast {key}: {row['status']}", 'weight': 'Bolder', 'wrap': True},
            {'type': 'TextBlock', 'text': ', '.join(f'{k}={v}' for k,v in counts.items()) or 'No registered recipients.', 'wrap': True},
            {'type': 'TextBlock', 'text': 'Accepted means Teams accepted the send; read receipts are not tracked.', 'wrap': True, 'isSubtle': True}]
    if len(rows) <= 10:
        body += [{'type': 'TextBlock', 'text': f"{display_recipient(r)} — {r['status']}", 'wrap': True} for r in rows]
    card = {'type': 'AdaptiveCard', 'version': '1.4', 'body': body, 'actions': [{
        'type': 'Action.Submit', 'title': 'View recipient names and delivery status',
        'data': {'msteams': {'type': 'task/fetch'}, 'action': 'broadcast_recipients', 'broadcast_id': key, 'page': 0}}]}
    await context.send_activity(MessageFactory.attachment(CardFactory.adaptive_card(card)))


async def recipient_dialog(context, identity):
    value = context.activity.value or {}
    data = value.get('data', {}) if isinstance(value, dict) else {}
    if not isinstance(data, dict) or data.get('action') != 'broadcast_recipients':
        return InvokeResponse(status=200, body={'task': {'type': 'message', 'value': 'Unknown dialog request.'}})
    key = data.get('broadcast_id')
    row, rows = report_rows(context, identity, key) if isinstance(key, str) else (None, [])
    if row is None:
        return InvokeResponse(status=200, body={'task': {'type': 'message', 'value': 'This report is unavailable for your account and chat.'}})
    try:
        page = int(data.get('page', 0))
    except (ValueError, TypeError, OverflowError):
        page = 0
    card = recipient_card(key, rows, page)
    return InvokeResponse(status=200, body={'task': {'type': 'continue', 'value': {
        'title': 'Broadcast recipients', 'height': 'large', 'width': 'large',
        'card': CardFactory.adaptive_card(card).serialize()}}})

async def handle(context, identity, text):
    parts = text.split(maxsplit=1)
    command = parts[0].casefold() if parts else ''
    if command not in {'/broadcast', '/broadcast-confirm', '/broadcast-cancel', '/broadcast-status', '/broadcast-recipients',
                       '/superadmin-add', '/superadmin-remove', '/superadmin-list'}:
        return False
    if not authorized_context(context, identity):
        await context.send_activity('This command requires superadmin access in a personal Teams chat.')
        return True
    argument = parts[1].strip() if len(parts) > 1 else ''
    owner = identity['aad_object_id'].casefold()
    conversation = context.activity.conversation.id
    if command.startswith('/superadmin-'):
        await manage_admins(context, identity, command, argument)
        return True
    if command == '/broadcast-status':
        await send_report(context, identity, argument)
        return True
    if command == '/broadcast-recipients':
        args = argument.split()
        row, rows = report_rows(context, identity, args[0]) if args else (None, [])
        if row is None:
            await context.send_activity('Broadcast not found for your account and chat.')
        else:
            try:
                page = int(args[1])-1 if len(args)>1 else 0
            except (ValueError, OverflowError):
                page = 0
            await context.send_activity(MessageFactory.attachment(CardFactory.adaptive_card(recipient_card(args[0], rows, page, popup=False))))
        return True
    if command == '/broadcast':
        if not argument or len(argument) > 4000:
            await context.send_activity('Use /broadcast <message> (1–4000 characters).')
            return True
        token = secrets.token_hex(8)
        with database() as db:
            db.execute('INSERT INTO broadcasts VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (token, tenant(), owner, conversation, argument, time.time(), 'draft'))
            db.execute('INSERT INTO deliveries SELECT ?, oid, reference, ? FROM recipients WHERE tenant = ?',
                       (token, 'pending', tenant()))
            db.execute('''INSERT INTO delivery_profiles SELECT ?, d.oid, COALESCE(p.name,''), COALESCE(p.email,'')
                FROM deliveries d LEFT JOIN recipient_profiles p ON p.tenant=? AND p.oid=d.oid WHERE d.broadcast=?''',
                (token, tenant(), token))
            count = db.execute('SELECT count(*) FROM deliveries WHERE broadcast = ?', (token,)).fetchone()[0]
        await context.send_activity(f'Broadcast preview ({count} registered Teams recipients):\n\n{argument}\n\nSend within 10 minutes: /broadcast-confirm {token}\nCancel: /broadcast-cancel {token}')
        return True
    with database() as db:
        row = db.execute('SELECT * FROM broadcasts WHERE id = ? AND tenant = ? AND owner = ? AND conversation = ?',
                         (argument, tenant(), owner, conversation)).fetchone()
        if row is None:
            reply = 'Broadcast not found for your account and chat.'
        elif row['status'] != 'draft':
            reply = 'This broadcast is already ' + row['status'] + '. Use /broadcast-status ' + argument
        elif command == '/broadcast-cancel':
            db.execute("UPDATE broadcasts SET status = 'cancelled' WHERE id = ?", (argument,))
            reply = 'Broadcast cancelled.'
        elif time.time() - row['created'] > 600:
            db.execute("UPDATE broadcasts SET status = 'expired' WHERE id = ?", (argument,))
            reply = 'Preview expired. Prepare a new broadcast.'
        elif not os.getenv('MICROSOFT_APP_ID', '').strip():
            reply = 'Broadcast sending requires MICROSOFT_APP_ID.'
        else:
            claimed = db.execute("UPDATE broadcasts SET status = 'sending' WHERE id = ? AND status = 'draft'", (argument,)).rowcount
            reply = None if claimed else 'Broadcast already claimed.'
    if reply is not None:
        await context.send_activity(reply)
        return True
    await context.send_activity('Sending broadcast ' + argument + '. Use /broadcast-status ' + argument + ' for delivery counts.')
    with database() as db:
        recipients = db.execute('SELECT * FROM deliveries WHERE broadcast = ?', (argument,)).fetchall()
    for recipient in recipients:
        with database() as db:
            db.execute("UPDATE deliveries SET status = 'sending' WHERE broadcast = ? AND oid = ?", (argument, recipient['oid']))
        async def send(turn):
            await turn.send_activity(Activity(type='message', text=row['message'], text_format='plain'))
        try:
            await context.adapter.continue_conversation(
                ConversationReference().deserialize(json.loads(recipient['reference'])), send,
                os.environ['MICROSOFT_APP_ID'])
            status = 'accepted'
        except Exception:
            # A timeout can occur after acceptance. Never automatically resend.
            status = 'failed_or_unknown'
        with database() as db:
            db.execute('UPDATE deliveries SET status = ? WHERE broadcast = ? AND oid = ?', (status, argument, recipient['oid']))
        await asyncio.sleep(0.1)
    with database() as db:
        db.execute("UPDATE broadcasts SET status = 'complete' WHERE id = ?", (argument,))
    await context.send_activity('Broadcast attempt complete. Check /broadcast-status ' + argument + ' for accepted and failed/unknown counts.')
    await send_report(context, identity, argument)
    return True
