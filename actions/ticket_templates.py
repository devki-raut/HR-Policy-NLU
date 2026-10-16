"""Editable email text, without placeholder-only drafts or invented details."""


def draft(details=None, started=None, identity=None, error=None):
    identity = identity or {}
    name = ' '.join(str(identity.get('display_name') or identity.get('email') or 'Employee').split())
    details = str(details or '').strip()
    subject = 'IT support required — ' + ' '.join(details.split())[:90] if details else 'IT support request'
    description = details or 'I need assistance with an IT issue or access request.'
    error_line = f'\n\nError or behaviour: {error}' if error else ''
    started_line = f'\n\nStarted: {started}' if started else ''
    body = f'Dear Team,\n\n{description}{error_line}{started_line}\n\nPlease help me resolve this issue.\n\nRegards,\n{name}'
    return subject, body


def prefill_draft(value, identity=None):
    subject, body = draft(identity=identity)
    return {**value, 'to': value.get('to') or 'helpdesk@emergys.com',
            'subject': value.get('subject') or subject, 'body': value.get('body') or body}
