"""Forward HTTP and WebSockets; Jupyter retains token/cookie authentication."""
import asyncio
from contextlib import suppress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from yarl import URL
from teams_app.web_paths import base_path, public_path, public_base_url

router = APIRouter()
BACKEND = "http://127.0.0.1:8888"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade"}


def headers_for(connection, websocket=False):
    excluded = HOP | {x.strip().lower() for x in connection.headers.get("connection", "").split(",")}
    if websocket:
        excluded |= {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}
    headers = {k: v for k, v in connection.headers.items() if k.lower() not in excluded}
    headers["X-Forwarded-Proto"] = {"ws": "http", "wss": "https"}.get(connection.url.scheme, connection.url.scheme)
    return headers


def target(connection):
    path = connection.scope.get("raw_path", connection.url.path.encode()).decode("ascii")
    prefix = base_path()
    if prefix and not path.startswith(prefix + '/'):
        path = prefix + path
    query = connection.scope.get("query_string", b"").decode("ascii")
    return URL(BACKEND + path + ("?" + query if query else ""), encoded=True)


@router.get("/jupyter", include_in_schema=False)
@router.get("/jupyter/", include_in_schema=False)
async def jupyter_redirect():
    return RedirectResponse(public_path("/jupyter/lab"))


def public_location(location):
    """Keep legacy upstream /jupyter redirects on the public proxy prefix."""
    parsed = urlsplit(location)
    if parsed.netloc and parsed.netloc not in {urlsplit(BACKEND).netloc, urlsplit(public_base_url()).netloc}:
        return location
    prefix = base_path()

    def with_prefix(path):
        return prefix + path if path == '/jupyter' or path.startswith('/jupyter/') else path

    path = with_prefix(parsed.path)
    if path == prefix + '/jupyter' or path.startswith(prefix + '/jupyter/'):
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        fixed = [(key, with_prefix(value) if key == 'next' else value) for key, value in pairs]
        query = urlencode(fixed) if fixed != pairs else parsed.query
        return urlunsplit(('', '', path, query, parsed.fragment))
    return location


def response_headers(upstream):
    excluded = HOP | {x.strip().lower() for x in upstream.headers.get('Connection', '').split(',')}
    headers = []
    prefix = base_path()
    for key, value in upstream.raw_headers:
        name = key.decode('latin-1').lower()
        if name in excluded:
            continue
        if name == 'location':
            value = public_location(value.decode('latin-1')).encode('latin-1')
        elif name == 'set-cookie' and prefix:
            # Preserve separate Set-Cookie headers and every cookie attribute.
            parts = value.decode('latin-1').split(';')
            for position, part in enumerate(parts):
                attr, separator, path = part.strip().partition('=')
                if separator and attr.casefold() == 'path' and (path == '/jupyter' or path.startswith('/jupyter/')):
                    parts[position] = ' Path=' + public_path(path)
            value = ';'.join(parts).encode('latin-1')
        headers.append((key, value))
    return headers


@router.api_route("/jupyter/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
async def proxy_http(request: Request, path: str):
    session = aiohttp.ClientSession(auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))
    try:
        upstream = await session.request(request.method, target(request), headers=headers_for(request), data=request.stream(), allow_redirects=False)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await session.close()
        return Response("Jupyter is unavailable", status_code=503)

    closed = False

    async def cleanup():
        nonlocal closed
        if closed:
            return
        closed = True
        upstream.close()
        await session.close()

    async def stream():
        try:
            async for chunk in upstream.content.iter_any():
                yield chunk
        finally:
            await cleanup()

    response = StreamingResponse(stream(), status_code=upstream.status, background=BackgroundTask(cleanup))
    response.raw_headers = response_headers(upstream)
    return response


@router.websocket("/jupyter/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str):
    protocols = websocket.scope.get("subprotocols", [])
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        try:
            upstream = await session.ws_connect(target(websocket), headers=headers_for(websocket, True), protocols=protocols, max_msg_size=0)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await websocket.close(code=1008)
            return
        try:
            await websocket.accept(subprotocol=upstream.protocol)

            async def to_backend():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        await upstream.send_bytes(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send_str(message["text"])

            async def to_browser():
                async for message in upstream:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        await websocket.send_bytes(message.data)
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        await websocket.send_text(message.data)

            tasks = [asyncio.create_task(to_backend()), asyncio.create_task(to_browser())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close()
        finally:
            await upstream.close()
