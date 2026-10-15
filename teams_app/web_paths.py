"""Public reverse-proxy prefix and private web endpoint configuration."""
import os
from urllib.parse import urlsplit


class PublicPrefixMiddleware:
    """Normalize proxies that either preserve or strip the public mount path.

    Starlette's mounted static apps need path and root_path to agree, including
    when the reverse proxy has already removed the external prefix.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'}:
            prefix = scope.get('root_path', '').rstrip('/')
            path = scope.get('path', '/')
            if prefix and path != prefix and not path.startswith(prefix + '/'):
                scope = dict(scope)
                scope['path'] = prefix + path
                scope['raw_path'] = prefix.encode('utf-8') + scope.get('raw_path', path.encode('utf-8'))
        await self.app(scope, receive, send)


def base_path():
    raw = os.getenv('PUBLIC_BASE_URL', '').strip() or os.getenv('APP_PUBLIC_URL', '').strip()
    path = urlsplit(raw).path.rstrip('/')
    return '/' + path.lstrip('/') if path else ''


def public_path(path='/'):
    return base_path() + '/' + path.lstrip('/')


def web_port():
    configured = os.getenv('WEB_PORT') or os.getenv('PORT') or os.getenv('WEBSITES_PORT')
    return int(configured or ('8608' if os.getenv('WEBSITE_INSTANCE_ID') else '8610'))


def public_base_url():
    for name in ['PUBLIC_BASE_URL', 'APP_PUBLIC_URL', 'BASE_URL']:
        value = os.getenv(name, '').strip()
        if value and urlsplit(value).scheme:
            parts = urlsplit(value)
            return f'{parts.scheme}://{parts.netloc}{base_path()}'
    return f'http://127.0.0.1:{web_port()}{base_path()}'


def jupyter_public_url():
    return public_base_url().rstrip('/') + '/jupyter/lab'


def jupyter_base_path():
    return public_path('/jupyter/')
