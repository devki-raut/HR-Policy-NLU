"""Shared chat service for authenticated bot turns and the public web API.

Only callers inside the application supply verified identity metadata. Public
HTTP routes derive identity from token validation and ignore client metadata.
"""
import os
import re
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from actions.darwinbox_adapters.diagnostics import record_darwinbox_event
from teams_app.web_paths import public_path


def chat_sender(identity, requested_sender):
    oid = str(identity.get('aad_object_id') or '')
    tenant = str(identity.get('tenant_id') or '')
    if oid and tenant:
        channel = 'teams' if identity.get('channel') == 'msteams' else 'web'
        return f'{channel}:{tenant}:{oid}'
    return 'guest:' + requested_sender


async def _enrich_policy_chunk(payload, question):
    # Only expose evidence actually selected by the Rasa action. Never retrieve again.
    sources = []
    for message in payload.get('messages', []):
        custom = message.get('custom', {})
        if isinstance(custom, dict):
            sources.extend(custom.get('sources', []))
            if custom.get('type') == 'policy_evidence':
                chunk = custom.get('chunk')
                if isinstance(chunk, dict):
                    chunk = dict(chunk)
                    source = chunk.get('source')
                    if source:
                        page = chunk.get('page')
                        suffix = f'#page={page}' if page else ''
                        chunk['pdf_url'] = (
                            public_path(f'/policies/pdfs/{quote(str(source), safe="")}{suffix}')
                        )
                    sources.append(chunk)
            if custom.get('intent'):
                payload['intent'] = custom['intent']
            if custom.get('status'):
                payload['status'] = custom['status']
            if custom.get('type') == 'leave_balance_metadata':
                payload['leave_balance'] = {
                    'status': custom.get('status'),
                    'integration_status': custom.get('integration_status'),
                    'employee_no_requested': custom.get('employee_no_requested'),
                    'employee_mapping_fallback': custom.get('employee_mapping_fallback', False),
                    'employee_name': custom.get('employee_name'),
                    'employee_no': custom.get('employee_no'),
                    'balance_count': custom.get('balance_count', 0),
                    'requested_leave_names': custom.get('requested_leave_names', []),
                    'not_returned_leave_names': custom.get('not_returned_leave_names', []),
                    'balances': custom.get('balances', []),
                }
            if custom.get('type') == 'holiday_list_metadata':
                payload['holiday_list'] = {
                    'status': custom.get('status'),
                    'integration_status': custom.get('integration_status'),
                    'employee_no_requested': custom.get('employee_no_requested'),
                    'employee_mapping_fallback': custom.get('employee_mapping_fallback', False),
                    'year': custom.get('year'),
                    'mode': custom.get('mode'),
                    'requested_count': custom.get('requested_count'),
                    'returned_count': custom.get('returned_count'),
                    'holidays': custom.get('holidays', []),
                }
    payload['sources'] = sources
    payload['chunk'] = sources[0] if sources else None
    return payload


async def rasa_chat(sender, message, metadata=None):
    if not sender.strip() or not message.strip():
        raise HTTPException(422, 'sender and message cannot be blank')
    # The web quick-action labels are intentionally short and can be scored as
    # generic policy questions by DIET. Map only these exact UI commands to
    # canonical training phrases; ordinary user text remains untouched.
    quick_actions = {
        'my leave balance': 'What is my leave balance?',
        'holiday calendar': 'Show the full holiday list',
        'next holidays': 'What are the next holidays?',
        'show my leaves': 'What is my leave balance?',
        'show my leave balance': 'What is my leave balance?',
        'show the next 5 holidays': 'What are the next holidays?',
        'show the full holiday list for this year': 'Show the full holiday list',
    }
    normalized_message = quick_actions.get(message.strip().casefold(), message)
    url = os.getenv('RASA_URL', 'http://127.0.0.1:5005').rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Forms otherwise consume every next message as a slot value. For
            # recognized non-ticket commands, inspect and clear an active IT
            # form before submitting the new message. The cancellation event
            # is deliberately not returned to the web client.
            lower = normalized_message.casefold()
            interrupt = (
                lower in quick_actions.values()
                or bool(re.search(
                    r"\b(leave balance|my leaves|holiday|holidays|help|remote work|benefits|expenses|bereavement|maternity|paternity|posh|referral|onboarding)\b",
                    lower,
                ))
            )
            if interrupt:
                tracker_response = await client.get(f'{url}/conversations/{quote(sender, safe="")}/tracker')
                if tracker_response.is_success:
                    tracker = tracker_response.json()
                    active_loop = (tracker.get('active_loop') or {}).get('name')
                    if active_loop in {'ticket_form', 'ticket_edit_form'}:
                        # Reset the conversation before forwarding the other
                        # command. Merely triggering ``ticket_cancel`` (or
                        # executing an action) can leave Rasa's form stack
                        # active, causing the next leave/holiday message to be
                        # captured as a ticket field.
                        await client.post(
                            f'{url}/conversations/{quote(sender, safe="")}/tracker/events',
                            json=[{'event': 'restart'}],
                        )
            response = await client.post(f'{url}/webhooks/rest/webhook', json={
                'sender': sender,
                'message': normalized_message,
                'metadata': metadata or {},
            })
            response.raise_for_status()
            messages = response.json()
        if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
            raise ValueError('Invalid Rasa response')
        component = 'bot' if sender.startswith('teams:') else 'web'
        for item in messages:
            custom = item.get('custom')
            if not isinstance(custom, dict):
                continue
            operation = {'leave_balance_metadata': 'leave_balance',
                         'holiday_list_metadata': 'holiday_list'}.get(custom.get('type'))
            if operation:
                status = custom.get('integration_status')
                outcome = status if status in {
                    'success', 'not_configured', 'api_error',
                    'not_authorized_or_api_error', 'employee_not_mapped',
                } else 'other'
                record_darwinbox_event(component, operation, outcome, channel=component)
        return {'sender': sender, 'answer': '\n\n'.join(m['text'] for m in messages if isinstance(m.get('text'), str)), 'messages': messages}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, 'Rasa response timed out') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, 'Rasa is unavailable or returned an invalid response. Start make serve and make actions.') from exc


