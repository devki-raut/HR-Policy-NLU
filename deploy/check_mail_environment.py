"""Check deployment environment presence and public manifest alignment.

This script never reads .env files or displays secret values.
"""
import json
import os
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'teams_app/app_package/upload/manifest.json').read_text())
checks={name:bool(os.getenv(name,'').strip()) for name in ['MICROSOFT_APP_ID','MICROSOFT_APP_PASSWORD','MICROSOFT_TENANT_ID','TEAMS_MAIL_OAUTH_CONNECTION','JUPYTER_TOKEN']}
checks['app_id_matches_package']=os.getenv('MICROSOFT_APP_ID','')==manifest['bots'][0]['botId']
checks['sso_resource_matches_package']=os.getenv('ENTRA_API_RESOURCE',manifest['webApplicationInfo']['resource'])==manifest['webApplicationInfo']['resource']
if os.getenv('ENTRA_CLIENT_ID') and os.getenv('ENTRA_CLIENT_ID')!=os.getenv('MICROSOFT_APP_ID'):
    checks['ENTRA_CLIENT_SECRET']=bool(os.getenv('ENTRA_CLIENT_SECRET','').strip())
print(json.dumps({'checks':checks,'ready_for_startup':all(checks.values()),'note':'Presence checks do not verify Azure OAuth connection, tenant consent, token exchange, or live mail delivery.'},indent=2))
sys.exit(0 if all(checks.values()) else 1)
