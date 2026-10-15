"""Exercise real Jupyter routes in memory, without a listening socket.

Run with .venv-jupyter/bin/python. This loads the project's actual Jupyter
configuration and Lab extension, testing both root and /employeeassist mounts.
"""
import asyncio
import html
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from jupyter_server.serverapp import ServerApp
from tornado.httputil import HTTPHeaders, HTTPServerRequest
from traitlets.config import Config

ROOT = Path(__file__).resolve().parents[1]


class InMemoryConnection:
    def __init__(self):
        self.context = SimpleNamespace(remote_ip='127.0.0.1', protocol='https')
        self.body = []
        self.done = asyncio.Event()

    def set_close_callback(self, callback):
        pass

    def write_headers(self, start_line, headers, chunk=None):
        self.status = start_line.code
        self.headers = headers
        return self.write(chunk or b'')

    def write(self, chunk):
        self.body.append(chunk)
        result = asyncio.get_running_loop().create_future()
        result.set_result(None)
        return result

    def finish(self):
        self.done.set()


async def exercise(public_url):
    prefix = urlsplit(public_url).path.rstrip('/')
    base = prefix + '/jupyter/'
    token = 'in-memory-jupyter-test-only'
    with tempfile.TemporaryDirectory(prefix='employeeassist-jupyter-handlers-') as folder:
        env = {key: str(Path(folder)/value) for key, value in {
            'JUPYTER_CONFIG_DIR': 'config', 'JUPYTER_DATA_DIR': 'data',
            'JUPYTER_RUNTIME_DIR': 'runtime', 'IPYTHONDIR': 'ipython',
        }.items()}
        env.update(PYTHON_DOTENV_DISABLED='1', PUBLIC_BASE_URL=public_url,
                   APP_PUBLIC_URL='', DATA_ROOT=folder, JUPYTER_TOKEN=token)
        with patch.dict(os.environ, env):
            app = ServerApp(config=Config({'ServerApp': {
                'jpserver_extensions': {'jupyterlab': True}, 'allow_root': True,
                'open_browser': False, 'log_level': 'ERROR',
            }}))
            app.initialize(['--config='+str(ROOT/'scripts/jupyter_server_config.py'),
                '--ServerApp.base_url='+base, '--ServerApp.default_url='+base+'lab',
                '--ServerApp.root_dir='+folder], find_extensions=False, new_httpserver=False)

            async def request(uri):
                connection = InMemoryConnection()
                incoming = HTTPServerRequest(method='GET', uri=uri, connection=connection,
                    headers=HTTPHeaders({'Host': urlsplit(public_url).netloc}))
                app.web_app(incoming)
                await asyncio.wait_for(connection.done.wait(), 10)
                return connection

            try:
                response = await request(base+'lab')
                assert response.status == 302, response.status
                login_url = response.headers['Location']
                assert urlsplit(login_url).path == base+'login', login_url
                assert parse_qs(urlsplit(login_url).query)['next'] == [base+'lab'], login_url
                login = await request(login_url)
                assert login.status == 200, login.status
                login_html = b''.join(login.body).decode()
                forms = [html.unescape(v) for v in re.findall(r'<form[^>]+action="([^"]+)"', login_html)]
                assert forms and all(urlsplit(v).path == base+'login' for v in forms), forms
                assert parse_qs(urlsplit(forms[0]).query)['next'] == [base+'lab'], forms
                lab = await request(base+'lab?'+urlencode({'token': token}))
                assert lab.status == 200, lab.status
                lab_html = b''.join(lab.body).decode()
                match = re.search(r'<script[^>]+id="jupyter-config-data"[^>]*>(.*?)</script>', lab_html, re.S)
                assert match, 'Lab page config missing'
                page_config = json.loads(match[1])
                assert page_config['baseUrl'] == base, page_config['baseUrl']
                assert all('Path='+base in cookie for cookie in lab.headers.get_list('Set-Cookie')), 'Cookie path escaped application mount'
                assets = [html.unescape(v) for v in re.findall(r'(?:href|src)="([^"]+)"', lab_html)]
                assert not any(prefix and v.startswith('/jupyter/') for v in assets), assets
                asset = next(v for v in assets if v.startswith(base+'static/'))
                static = await request(asset)
                assert static.status == 200, (asset, static.status)
                api = await request(base+'api/kernels?'+urlencode({'token': token}))
                assert api.status == 200 and json.loads(b''.join(api.body)) == [], api.status
                return {'public_base': public_url, 'base_url': base, 'passed': True,
                        'checks': ['login redirect', 'next target', 'login form', 'Lab baseUrl', 'cookies', 'static asset', 'kernel API']}
            finally:
                await app.cleanup_kernels()
                app.clear_instance()


async def main():
    results = []
    for public_url in ['https://flounder-sphere-dab.ngrok-free.dev/',
                       'https://flounder-sphere-dab.ngrok-free.dev/employeeassist/',
                       'https://dockerpoc-webapp01.azurewebsites.net/employeeassist/']:
        results.append(await exercise(public_url))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
