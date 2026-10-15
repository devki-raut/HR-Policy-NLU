"""Production configuration checks; never load dotenv or print secret values."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def valid_uuid(value):
    try:
        return bool(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return False


def checks():
    from teams_app.web_paths import public_base_url, web_port
    manifest = json.loads((ROOT/'teams_app/app_package/manifest.json').read_text())
    app_id = os.getenv('MICROSOFT_APP_ID', '').strip()
    tenant = os.getenv('MICROSOFT_TENANT_ID', '').strip()
    entra_app = os.getenv('ENTRA_CLIENT_ID', '').strip() or app_id
    entra_tenant = os.getenv('ENTRA_TENANT_ID', 'organizations').strip()
    ids = [v.strip() for v in os.getenv('TEAMS_SUPERADMIN_OBJECT_IDS', '').split(',') if v.strip()]
    emails = [v.strip() for v in os.getenv('TEAMS_SUPERADMIN_EMAILS', '').split(',') if v.strip()]
    public = public_base_url()
    parsed = urlsplit(public)
    result = {
        'microsoft_app_id_valid': valid_uuid(app_id),
        'microsoft_tenant_id_valid': valid_uuid(tenant),
        'entra_app_matches_combined_package': entra_app == manifest['webApplicationInfo']['id'] == app_id,
        'tenant_settings_match': valid_uuid(entra_tenant) and entra_tenant.casefold() == tenant.casefold(),
        'broadcast_tenant_matches': os.getenv('TEAMS_BROADCAST_TENANT_ID', tenant).strip().casefold() == tenant.casefold(),
        'microsoft_app_password_present': bool(os.getenv('MICROSOFT_APP_PASSWORD', '').strip()),
        'teams_oauth_connection_present': bool(os.getenv('TEAMS_MAIL_OAUTH_CONNECTION', '').strip()),
        'jupyter_token_present': bool(os.getenv('JUPYTER_TOKEN', '').strip()),
        'signin_required': os.getenv('ENTRA_AUTH_REQUIRED', 'true').casefold().strip() in {'1', 'true', 'yes'},
        'public_url_https': parsed.scheme == 'https' and bool(parsed.netloc),
        'public_url_matches_teams_tab': public.rstrip('/') == manifest['staticTabs'][0]['contentUrl'].rstrip('/'),
        'redirect_uri_matches_base': os.getenv('ENTRA_REDIRECT_URI', public).rstrip('/') == public.rstrip('/'),
        'sso_resource_matches_package': os.getenv('ENTRA_API_RESOURCE', manifest['webApplicationInfo']['resource']).rstrip('/') == manifest['webApplicationInfo']['resource'],
        'bootstrap_admin_configured': bool(ids or emails) and all(valid_uuid(v) for v in ids)
            and all(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', v) for v in emails),
        'trained_model_available': any((ROOT/'models').glob('*.tar.gz')),
    }
    try:
        port = web_port()
        result['web_port_valid'] = 0 < port < 65536
        if os.getenv('WEBSITES_PORT'):
            result['azure_target_port_matches'] = port == int(os.environ['WEBSITES_PORT'])
    except ValueError:
        result['web_port_valid'] = False
    return result


if __name__ == '__main__':
    results = checks()
    ready = all(results.values())
    print(json.dumps({'checks': results, 'configuration_ready': ready,
        'note': 'These checks do not verify a container build, Azure routing, consent, Teams SSO, mail delivery, or durable storage.'}, indent=2))
    sys.exit(0 if ready else 1)
