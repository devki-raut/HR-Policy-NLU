"""Owned Teams drafts and duplicate-resistant delegated Graph sends."""
import os
from actions.runtime_paths import runtime_path
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
import uuid
import re
from urllib.parse import quote, urlencode
import httpx

ROOT = Path(__file__).resolve().parents[2]


class MailError(RuntimeError):
    pass


def _db():
    path = runtime_path('private/teams_ticket_mail.sqlite3', ROOT/'artifacts/private/teams_ticket_mail.sqlite3', 'TEAMS_TICKET_DB', ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, owner TEXT, subject TEXT, body TEXT, status TEXT, created REAL)')
    columns = {row[1] for row in db.execute('PRAGMA table_info(drafts)')}
    if 'to_list' not in columns:
        db.execute("ALTER TABLE drafts ADD COLUMN to_list TEXT NOT NULL DEFAULT 'helpdesk@emergys.com'")
    if 'cc_list' not in columns:
        db.execute("ALTER TABLE drafts ADD COLUMN cc_list TEXT NOT NULL DEFAULT ''")
    db.execute('CREATE TABLE IF NOT EXISTS pending_auth (owner TEXT PRIMARY KEY, draft_id TEXT, connection TEXT, exchange_id TEXT, expires REAL, status TEXT)')
    return db


def owner(identity, conversation_id):
    if not identity.get('aad_object_id') or not identity.get('tenant_id'):
        raise MailError('A verified Teams identity is required to send mail.')
    return ':'.join([identity['tenant_id'], identity['aad_object_id'], conversation_id])


def addresses(value, *, required=False):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MailError('Enter email addresses separated by commas or semicolons, without line breaks.')
    result = []
    for address in re.split(r'[,;]', value):
        address = address.strip()
        if not address:
            continue
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", address):
            raise MailError('Enter valid email addresses in To and CC, separated by commas or semicolons.')
        if address.casefold() not in {a.casefold() for a in result}:
            result.append(address)
    if len(result) > 20 or required and not result:
        raise MailError('To requires at least one email address; each field supports up to 20 recipients.')
    return ', '.join(result)


def create_draft(identity_owner, subject, body, to='helpdesk@emergys.com', cc=''):
    to, cc = addresses(to, required=True), addresses(cc)
    key = uuid.uuid4().hex
    with _db() as db:
        db.execute('DELETE FROM drafts WHERE created < ?', (time.time() - 7 * 86400,))
        db.execute('INSERT INTO drafts (id,owner,subject,body,status,created,to_list,cc_list) VALUES (?,?,?,?,?,?,?,?)',
                   (key, identity_owner, subject, body, 'draft', time.time(), to, cc))
    return key


def get_draft(key, identity_owner):
    with _db() as db:
        row = db.execute('SELECT * FROM drafts WHERE id=? AND owner=?', (key, identity_owner)).fetchone()
    if not row:
        raise MailError('This draft is unavailable or belongs to another user or conversation.')
    return dict(row)


def update_draft(key, identity_owner, subject, body, to=None, cc=None):
    if not isinstance(subject, str) or not 1 <= len(subject.strip()) <= 180:
        raise MailError('Enter a subject between 1 and 180 characters.')
    if any(ord(c) < 32 for c in subject):
        raise MailError('Subject must be a single line.')
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 6000:
        raise MailError('Enter a body between 1 and 6000 characters.')
    if to is not None: to = addresses(to, required=True)
    if cc is not None: cc = addresses(cc)
    with _db() as db:
        row = db.execute('SELECT * FROM drafts WHERE id=? AND owner=?', (key, identity_owner)).fetchone()
        if not row:
            raise MailError('This draft is unavailable or belongs to another user or conversation.')
        if row['status'] != 'draft':
            raise MailError('This request was already submitted or its outcome needs checking. Check Sent Items before creating another request.')
        db.execute('UPDATE drafts SET subject=?, body=?, to_list=?, cc_list=? WHERE id=? AND owner=?',
                   (subject.strip(), body.strip(), row['to_list'] if to is None else to,
                    row['cc_list'] if cc is None else cc, key, identity_owner))
        db.execute("UPDATE pending_auth SET status='cancelled' WHERE owner=? AND draft_id=?", (identity_owner,key))


def outlook_url(record):
    return 'https://outlook.office.com/mail/deeplink/compose?' + urlencode(
        {'to': record['to_list'], 'cc': record['cc_list'], 'subject': record['subject'], 'body': record['body']}, quote_via=quote)


def pending_auth(identity_owner, key, connection, exchange_id):
    # Only a validated, owner-bound draft approved by a Raise a request click is eligible.
    row = get_draft(key, identity_owner)
    if row['status'] != 'draft': raise MailError('This request is no longer editable.')
    with _db() as db:
        db.execute('INSERT OR REPLACE INTO pending_auth VALUES (?,?,?,?,?,?)',
                   (identity_owner,key,connection,exchange_id,time.time()+600,'waiting'))


def auth_request(identity_owner):
    with _db() as db:
        row=db.execute("SELECT * FROM pending_auth WHERE owner=? AND status='waiting' AND expires>?", (identity_owner,time.time())).fetchone()
    return dict(row) if row else None


def claim_auth(identity_owner, expected_draft=None, expected_exchange=None):
    with _db() as db:
        row=db.execute("SELECT * FROM pending_auth WHERE owner=? AND status='waiting' AND expires>?", (identity_owner,time.time())).fetchone()
        if not row:return None
        if expected_draft is not None and row['draft_id']!=expected_draft:return None
        if expected_exchange is not None and row['exchange_id']!=expected_exchange:return None
        claimed=db.execute("UPDATE pending_auth SET status='claimed' WHERE owner=? AND status='waiting' AND expires>?",(identity_owner,time.time())).rowcount
    return dict(row) if claimed else None


def mail_event(event, key, recipient, **details):
    """Append structured audit events without tokens or email content."""
    try:
        path = runtime_path('logs/ticket_mail.log', ROOT/'artifacts/logs/ticket_mail.log', 'TICKET_MAIL_LOG_PATH', ROOT)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {'timestamp': datetime.now(timezone.utc).isoformat(), 'event': event,
                  'request_id': key, 'recipient': recipient, **details}
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, 'a') as handle:
            handle.write(json.dumps(record) + '\n')
    except OSError:
        logging.getLogger(__name__).exception('Could not write ticket mail audit log')


async def send_draft(key, identity_owner, token, aad_object_id, *, recipient=None):
    audit_recipient = recipient or get_draft(key, identity_owner)['to_list']
    mail_event('send_attempt', key, audit_recipient, channel='web' if identity_owner.endswith(':web') else 'teams')
    try:
        await _send_draft(key, identity_owner, token, aad_object_id, recipient=recipient)
    except Exception as exc:
        mail_event('send_failed', key, audit_recipient, error_type=type(exc).__name__,
                   reason=str(exc) if isinstance(exc, MailError) else 'Internal error; check service logs')
        raise
    mail_event('send_accepted', key, audit_recipient, http_status=202)


async def _send_draft(key, identity_owner, token, aad_object_id, *, recipient):
    # Check the Graph account before claiming the draft. Never accept a client-supplied sender.
    async with httpx.AsyncClient(timeout=20) as http:
        try:
            response = await http.get('https://graph.microsoft.com/v1.0/me?$select=id', headers={'Authorization': 'Bearer ' + token})
            if response.status_code != 200 or response.json().get('id') != aad_object_id:
                raise MailError('Sign in to Outlook with the same Microsoft account you use in Teams.')
        except (httpx.HTTPError, ValueError) as exc:
            raise MailError('Could not verify the Outlook account. Please try again.') from exc
        with _db() as db:
            row = db.execute('SELECT * FROM drafts WHERE id=? AND owner=? AND status=?', (key, identity_owner, 'draft')).fetchone()
            if not row:
                raise MailError('This request was already submitted or is unavailable. Check Sent Items before retrying.')
            recipient = addresses(recipient or row['to_list'], required=True)
            if not row['subject'].strip() or not row['body'].strip():
                raise MailError('Enter a subject and describe your issue before sending.')
            changed = db.execute('UPDATE drafts SET status=? WHERE id=? AND owner=? AND status=?', ('sending', key, identity_owner, 'draft')).rowcount
            if changed != 1:
                raise MailError('This request is already being submitted.')
        try:
            response = await http.post('https://graph.microsoft.com/v1.0/me/sendMail', headers={'Authorization': 'Bearer ' + token}, json={
                'message': {'subject': row['subject'], 'body': {'contentType': 'Text', 'content': row['body']},
                            'toRecipients': [{'emailAddress': {'address': a.strip()}} for a in addresses(recipient, required=True).split(',')],
                            'ccRecipients': [{'emailAddress': {'address': a.strip()}} for a in row['cc_list'].split(',') if a.strip() and a.strip().casefold() not in {x.strip().casefold() for x in recipient.split(',')}]},
                'saveToSentItems': True,
            })
        except httpx.HTTPError as exc:
            with _db() as db:
                db.execute('UPDATE drafts SET status=? WHERE id=?', ('unknown', key))
            mail_event('send_outcome_unknown', key, recipient, error_type=type(exc).__name__)
            raise MailError('The send outcome is uncertain. Check Outlook Sent Items before creating another request; this click will not be retried automatically.') from exc
        with _db() as db:
            status = 'accepted' if response.status_code == 202 else 'draft' if response.status_code in {400, 401, 403, 429} else 'unknown'
            db.execute('UPDATE drafts SET status=? WHERE id=?', (status, key))
        if response.status_code != 202:
            mail_event('graph_rejected', key, recipient, http_status=response.status_code,
                       graph_request_id=response.headers.get('request-id'))
            raise MailError('Outlook did not accept the email. Check mailbox permissions and Sent Items before retrying.')


def cancel_draft(key, identity_owner):
    with _db() as db:
        changed = db.execute('UPDATE drafts SET status=? WHERE id=? AND owner=? AND status=?', ('cancelled', key, identity_owner, 'draft')).rowcount
        db.execute("UPDATE pending_auth SET status='cancelled' WHERE owner=? AND draft_id=?",(identity_owner,key))
        if changed != 1:
            raise MailError('This draft is already submitted or unavailable. Check Sent Items if you clicked Raise a request.')
