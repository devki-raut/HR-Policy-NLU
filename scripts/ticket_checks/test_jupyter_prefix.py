import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from teams_app.web_package import jupyter, main
from teams_app.web_paths import jupyter_public_url


class JupyterPrefixTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        import uvloop
        cls.policy = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    @classmethod
    def tearDownClass(cls):
        asyncio.set_event_loop_policy(cls.policy)

    async def test_entry_redirect_and_generated_link_for_azure_and_ngrok(self):
        for variable in ['PUBLIC_BASE_URL', 'APP_PUBLIC_URL']:
            for origin, prefix in [('https://dockerpoc-webapp01.azurewebsites.net', '/employeeassist'),
                                   ('https://flounder-sphere-dab.ngrok-free.dev', '')]:
                env = {'PUBLIC_BASE_URL': '', 'APP_PUBLIC_URL': '', variable: origin+prefix+'/'}
                with patch.dict(os.environ, env), patch.object(main, 'BASE_PATH', prefix), patch.object(main.app, 'root_path', prefix):
                    self.assertEqual(jupyter_public_url(), origin+prefix+'/jupyter/lab')
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=origin) as client:
                        for external in (['', prefix] if prefix else ['']):
                            for suffix in ['/jupyter', '/jupyter/']:
                                result = await client.get(external+suffix)
                                self.assertEqual(result.status_code, 307)
                                self.assertEqual(result.headers['location'], prefix+'/jupyter/lab')
                        home = await client.get(prefix+'/')
                        self.assertNotIn('Open JupyterLab', home.text)
                        self.assertNotIn('jupyter-link', home.text)

    async def test_upstream_redirects_and_cookies_preserve_azure_prefix(self):
        async def chunks():
            yield b'redirect'
        upstream = SimpleNamespace(status=302, headers={}, content=SimpleNamespace(iter_any=chunks), close=Mock(), raw_headers=[
            (b'Location', b'/jupyter/lab?next=abc#fragment'),
            (b'Set-Cookie', b'session=example; Path=/jupyter/; HttpOnly; Secure'),
            (b'Set-Cookie', b'other=value; Path=/employeeassist/jupyter/; HttpOnly')])
        session = SimpleNamespace(request=AsyncMock(return_value=upstream), close=AsyncMock())
        with patch.dict(os.environ, {'PUBLIC_BASE_URL': 'https://example.com/employeeassist/'}), \
                patch.object(main, 'BASE_PATH', '/employeeassist'), patch.object(main.app, 'root_path', '/employeeassist'), \
                patch.object(jupyter.aiohttp, 'ClientSession', return_value=session):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='https://example.com') as client:
                response = await client.get('/employeeassist/jupyter/redirect-test')
            self.assertEqual(response.headers['location'], '/employeeassist/jupyter/lab?next=abc#fragment')
            self.assertEqual(response.headers.get_list('set-cookie'), [
                'session=example; Path=/employeeassist/jupyter/; HttpOnly; Secure',
                'other=value; Path=/employeeassist/jupyter/; HttpOnly'])
            self.assertEqual(str(session.request.await_args.args[1]), 'http://127.0.0.1:8888/employeeassist/jupyter/redirect-test')
            self.assertEqual(jupyter.public_location('http://127.0.0.1:8888/jupyter/login?next=x'), '/employeeassist/jupyter/login?next=x')
            self.assertEqual(jupyter.public_location('/employeeassist/jupyter/lab'), '/employeeassist/jupyter/lab')
            self.assertEqual(jupyter.public_location('https://outside.example/jupyter/lab'), 'https://outside.example/jupyter/lab')
            self.assertEqual(jupyter.public_location('/jupyter/login?next=%2Fjupyter%2Flab'),
                             '/employeeassist/jupyter/login?next=%2Femployeeassist%2Fjupyter%2Flab')
            self.assertEqual(jupyter.public_location('/employeeassist/jupyter/login?next=%2Fjupyter%2Flab'),
                             '/employeeassist/jupyter/login?next=%2Femployeeassist%2Fjupyter%2Flab')
        upstream.close.assert_called_once()
        session.close.assert_awaited()

    def test_private_jupyter_server_config_uses_same_public_base(self):
        config_path = Path(__file__).resolve().parents[2]/'scripts/jupyter_server_config.py'
        for variable in ['PUBLIC_BASE_URL', 'APP_PUBLIC_URL']:
            config = SimpleNamespace(IdentityProvider=SimpleNamespace(), ServerApp=SimpleNamespace())
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                'PYTHON_DOTENV_DISABLED': '1', 'PUBLIC_BASE_URL': '', 'APP_PUBLIC_URL': '',
                variable: 'https://example.com/employeeassist/', 'JUPYTER_TOKEN': 'test-token', 'DATA_ROOT': folder}):
                exec(compile(config_path.read_text(), str(config_path), 'exec'), {'__file__': str(config_path), 'get_config': lambda: config})
            self.assertEqual(config.ServerApp.base_url, '/employeeassist/jupyter/')
            self.assertEqual(config.ServerApp.default_url, '/employeeassist/jupyter/lab')

    def test_launcher_passes_the_same_base_path_to_jupyter(self):
        from scripts.run_deployment import commands
        for prefix in ['', '/employeeassist']:
            with patch.dict(os.environ, {'PUBLIC_BASE_URL': 'https://example.com'+prefix+'/'}):
                command = commands('web')['jupyter']
            self.assertIn('--ServerApp.base_url='+prefix+'/jupyter/', command)
            self.assertIn('--ServerApp.default_url='+prefix+'/jupyter/lab', command)

    def test_shared_host_nginx_configuration_strips_only_the_app_prefix(self):
        from scripts.render_subroute_nginx import render
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'nginx.conf'
            render(output, folder, web_port=8610, proxy_port=18610)
            content = output.read_text()
        self.assertIn('location /employeeassist/', content)
        self.assertIn('proxy_pass http://127.0.0.1:8610/;', content)
        self.assertIn('location / { return 404; }', content)
        self.assertIn('proxy_set_header Upgrade $http_upgrade;', content)

    async def test_kernel_websocket_proxy_keeps_prefix_and_passes_both_message_types(self):
        queue = asyncio.Queue()
        delivered = asyncio.Event()
        sent = []

        class Backend:
            protocol = 'kernel-protocol'
            close = AsyncMock()

            async def send_str(self, text):
                await queue.put(SimpleNamespace(type=jupyter.aiohttp.WSMsgType.TEXT, data=text))

            async def send_bytes(self, data):
                await queue.put(SimpleNamespace(type=jupyter.aiohttp.WSMsgType.BINARY, data=data))

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await queue.get()

        backend = Backend()

        class Session:
            ws_connect = AsyncMock(return_value=backend)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        session = Session()
        incoming = iter([{'type': 'websocket.receive', 'text': 'kernel-message'},
                         {'type': 'websocket.receive', 'bytes': b'kernel-bytes'}])

        async def receive():
            try:
                return next(incoming)
            except StopIteration:
                await delivered.wait()
                return {'type': 'websocket.disconnect'}

        async def send_message(value):
            sent.append(value)
            if len(sent) == 2:
                delivered.set()

        websocket = SimpleNamespace(
            scope={'raw_path': b'/jupyter/api/kernels/kernel/channels', 'query_string': b'session_id=test', 'subprotocols': ['kernel-protocol']},
            url=SimpleNamespace(scheme='wss', path='/jupyter/api/kernels/kernel/channels'),
            headers={'Host': 'example.com', 'Origin': 'https://example.com', 'Cookie': 'session=value', 'Sec-WebSocket-Key': 'client-key'},
            accept=AsyncMock(), receive=receive, send_text=send_message, send_bytes=send_message, close=AsyncMock())
        with patch.dict(os.environ, {'PUBLIC_BASE_URL': 'https://example.com/employeeassist/'}), \
                patch.object(jupyter.aiohttp, 'ClientSession', return_value=session):
            await asyncio.wait_for(jupyter.proxy_websocket(websocket, 'api/kernels/kernel/channels'), 5)
        self.assertEqual(sent, ['kernel-message', b'kernel-bytes'])
        args = session.ws_connect.await_args
        self.assertEqual(str(args.args[0]), 'http://127.0.0.1:8888/employeeassist/jupyter/api/kernels/kernel/channels?session_id=test')
        self.assertEqual(args.kwargs['headers']['X-Forwarded-Proto'], 'https')
        self.assertEqual(args.kwargs['headers']['Cookie'], 'session=value')
        self.assertNotIn('Sec-WebSocket-Key', args.kwargs['headers'])
        websocket.accept.assert_awaited_once_with(subprotocol='kernel-protocol')
        backend.close.assert_awaited_once()

