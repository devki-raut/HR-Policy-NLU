"""Build a Teams personal-tab package using real deployment metadata."""
import argparse
import json
from pathlib import Path
import struct
from urllib.parse import urlparse
import uuid
import zipfile
import zlib

ROOT = Path(__file__).resolve().parent

def png(size, outline=False):
    # A simple code-drawn document icon. No external image dependencies.
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            X, Y = x / size, y / size
            border = .25 <= X <= .75 and .18 <= Y <= .82 and (X < .29 or X > .71 or Y < .22 or Y > .78)
            line = .35 <= X <= .65 and any(a <= Y <= a + .035 for a in (.36, .49, .62))
            pixels.extend((255,255,255,255) if border or line else ((0,0,0,0) if outline else (81,70,185,255)))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')

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
    manifest = {
        '$schema': 'https://developer.microsoft.com/json-schemas/teams/v1.19/MicrosoftTeams.schema.json',
        'manifestVersion': '1.19', 'version': '1.0.0', 'id': app_id,
        'developer': {'name': config['developer_name'], 'websiteUrl': https_url(config['website_url']), 'privacyUrl': https_url(config['privacy_url']), 'termsOfUseUrl': https_url(config['terms_url'])},
        'name': {'short': 'HR Policy', 'full': 'HR Policy Assistant'},
        'description': {'short': 'Ask HR policy questions with source references.', 'full': 'Find policy excerpts about leave, remote work, benefits, expenses, conduct and onboarding. Contact HR for individual eligibility.'},
        'icons': {'color': 'color.png', 'outline': 'outline.png'}, 'accentColor': '#5146B9',
        'staticTabs': [{'entityId': 'hr-policy', 'name': 'Ask HR', 'contentUrl': base + '/', 'websiteUrl': base + '/', 'scopes': ['personal']}],
        'validDomains': [urlparse(base).hostname]
    }
    if not config['developer_name'].strip() or len(config['developer_name']) > 32:
        raise ValueError('developer_name must contain 1–32 characters')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('manifest.json', json.dumps(manifest, indent=2))
        package.writestr('color.png', png(192))
        package.writestr('outline.png', png(32, True))
    return manifest

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/hr-policy-teams.zip')
    args = parser.parse_args()
    build(json.loads(args.config.read_text()), args.output)
    print(f'Created {args.output}')
