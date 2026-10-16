"""Report missing integration setting names without exposing their values."""
import os
from urllib.parse import urlsplit


def present(name):
    return bool(os.getenv(name, '').strip())


def configuration_status():
    common = ['DARWINBOX_BASE_URL', 'DARWINBOX_EMPLOYEE_URL', 'DARWINBOX_LEAVE_BALANCE_URL']
    leave_missing = []
    holiday_missing = []
    if not any(present(name) for name in common):
        leave_missing.append('DARWINBOX_BASE_URL or DARWINBOX_LEAVE_BALANCE_URL')
    if not present('DARWINBOX_HOLIDAY_URL') and not any(present(name) for name in common):
        holiday_missing.append('DARWINBOX_BASE_URL or DARWINBOX_HOLIDAY_URL')
    for name in ['DARWINBOX_LEAVE_BALANCE_URL', 'DARWINBOX_HOLIDAY_URL', 'DARWINBOX_BASE_URL']:
        if present(name):
            try:
                parsed = urlsplit(os.environ[name].strip())
                valid = parsed.scheme in {'https', 'http'} and bool(parsed.netloc)
            except ValueError:
                valid = False
            if not valid:
                if name != 'DARWINBOX_HOLIDAY_URL':
                    leave_missing.append(name+' must be an absolute HTTP(S) URL')
                if name != 'DARWINBOX_LEAVE_BALANCE_URL':
                    holiday_missing.append(name+' must be an absolute HTTP(S) URL')
    if present('DARWINBOX_LEAVE_API_KEY'):
        mode = 'api_key'
    else:
        mode = 'sha512'
        leave_missing += [name for name in ['DARWINBOX_ADMIN_EMAIL', 'DARWINBOX_SECRET_KEY', 'DARWINBOX_UID'] if not present(name)]
    user = present('DARWINBOX_LEAVE_USERNAME')
    password = present('DARWINBOX_LEAVE_PASSWORD')
    if user != password:
        leave_missing.append('DARWINBOX_LEAVE_USERNAME' if not user else 'DARWINBOX_LEAVE_PASSWORD')
    if not present('DARWINBOX_HOLIDAY_API_KEY'):
        holiday_missing.append('DARWINBOX_HOLIDAY_API_KEY')
    if not (present('DARWINBOX_HOLIDAY_USERNAME') or user):
        holiday_missing.append('DARWINBOX_HOLIDAY_USERNAME or DARWINBOX_LEAVE_USERNAME')
    if not (present('DARWINBOX_HOLIDAY_PASSWORD') or password):
        holiday_missing.append('DARWINBOX_HOLIDAY_PASSWORD or DARWINBOX_LEAVE_PASSWORD')
    return {'leave': {'configured': not leave_missing, 'auth_mode': mode, 'missing': leave_missing},
            'holidays': {'configured': not holiday_missing, 'missing': holiday_missing},
            'note': 'Configuration checks do not test network access, credentials, employee mappings or Darwinbox API responses.'}
