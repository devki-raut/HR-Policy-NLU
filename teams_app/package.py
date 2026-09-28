"""Build a Teams personal-tab package using real deployment metadata."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent

def https_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Provide an HTTPS URL without credentials, query or fragment')
    return value.rstrip('/')

def build(config, output):
    app_id = str(uuid.UUID(config['app_id']))
    base = https_url(config['base_url'])
    if urlparse(base).path not in ('', '/'):
        raise ValueError('base_url must be a host root, without a path')
    values = {
        '${APP_ID}': app_id, '${BASE_URL}': base,
        '${HOSTNAME}': urlparse(base).hostname,
        '${DEVELOPER_NAME}': config['developer_name'],
        '${WEBSITE_URL}': https_url(config['website_url']),
        '${PRIVACY_URL}': https_url(config['privacy_url']),
        '${TERMS_URL}': https_url(config['terms_url']),
    }
    def render(value):
        if isinstance(value, dict):
            return {k: render(v) for k, v in value.items()}
        if isinstance(value, list):
            return [render(v) for v in value]
        if isinstance(value, str):
            for token, replacement in values.items():
                value = value.replace(token, replacement)
            if '${' in value:
                raise ValueError('Unresolved manifest placeholder')
        return value
    manifest = render(json.loads((ROOT / 'appPackage/manifest.json').read_text()))
    if not config['developer_name'].strip() or len(config['developer_name']) > 32:
        raise ValueError('developer_name must contain 1–32 characters')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    (output.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('manifest.json', json.dumps(manifest, indent=2))
        package.write(ROOT / 'appPackage/color.png', 'color.png')
        package.write(ROOT / 'appPackage/outline.png', 'outline.png')
    return manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/hr-policy-teams.zip')
    args = parser.parse_args()
    build(json.loads(args.config.read_text()), args.output)
    print(f'Created {args.output}')
