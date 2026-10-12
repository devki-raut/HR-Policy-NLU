"""Owned Teams drafts and duplicate-resistant delegated Graph sends."""
import os
from pathlib import Path
import sqlite3
import time
import uuid
import httpx

ROOT = Path(__file__).resolve().parents[2]


class MailError(RuntimeError):
    pass


def _db():
    path = ROOT / 'artifacts/private/teams_ticket_mail.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, owner TEXT, subject TEXT, body TEXT, status TEXT, created REAL)')
    return db


def owner(identity, conversation_id):
    if not identity.get('aad_object_id') or not identity.get('tenant_id'):
        raise MailError('A verified Teams identity is required to send mail.')
    return ':'.join([identity['tenant_id'], identity['aad_object_id'], conversation_id])


def create_draft(identity_owner, subject, body):
    key = uuid.uuid4().hex
    with _db() as db:
        db.execute('DELETE FROM drafts WHERE created < ?', (time.time() - 7 * 86400,))
        db.execute('INSERT INTO drafts VALUES (?,?,?,?,?,?)', (key, identity_owner, subject, body, 'draft', time.time()))
    return key


def update_draft(key, identity_owner, subject, body):
    if not isinstance(subject, str) or not 1 <= len(subject.strip()) <= 180:
        raise MailError('Enter a subject between 1 and 180 characters.')
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 6000:
        raise MailError('Enter a body between 1 and 6000 characters.')
    with _db() as db:
        row = db.execute('SELECT * FROM drafts WHERE id=? AND owner=?', (key, identity_owner)).fetchone()
        if not row:
            raise MailError('This draft is unavailable or belongs to another user or conversation.')
        if row['status'] != 'draft':
            raise MailError('This request was already submitted or its outcome needs checking. Check Sent Items before creating another request.')
        db.execute('UPDATE drafts SET subject=?, body=? WHERE id=? AND owner=?', (subject.strip(), body.strip(), key, identity_owner))


async def send_draft(key, identity_owner, token, aad_object_id):
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
            changed = db.execute('UPDATE drafts SET status=? WHERE id=? AND owner=? AND status=?', ('sending', key, identity_owner, 'draft')).rowcount
            if changed != 1:
                raise MailError('This request is already being submitted.')
        try:
            response = await http.post('https://graph.microsoft.com/v1.0/me/sendMail', headers={'Authorization': 'Bearer ' + token}, json={
                'message': {'subject': row['subject'], 'body': {'contentType': 'Text', 'content': row['body']},
                            'toRecipients': [{'emailAddress': {'address': 'helpdesk@emergys.com'}}]},
                'saveToSentItems': True,
            })
        except httpx.HTTPError as exc:
            with _db() as db:
                db.execute('UPDATE drafts SET status=? WHERE id=?', ('unknown', key))
            raise MailError('The send outcome is uncertain. Check Outlook Sent Items before creating another request; this click will not be retried automatically.') from exc
        with _db() as db:
            status = 'accepted' if response.status_code == 202 else 'draft' if response.status_code in {400, 401, 403, 429} else 'unknown'
            db.execute('UPDATE drafts SET status=? WHERE id=?', (status, key))
        if response.status_code != 202:
            raise MailError('Outlook did not accept the email. Check mailbox permissions and Sent Items before retrying.')


def cancel_draft(key, identity_owner):
    with _db() as db:
        changed = db.execute('UPDATE drafts SET status=? WHERE id=? AND owner=? AND status=?', ('cancelled', key, identity_owner, 'draft')).rowcount
        if changed != 1:
            raise MailError('This draft is already submitted or unavailable. Check Sent Items if you clicked Raise a request.')
