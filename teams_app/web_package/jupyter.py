"""Forward HTTP and WebSockets; Jupyter retains token/cookie authentication."""
import asyncio
from contextlib import suppress

import aiohttp
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from yarl import URL

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
    query = connection.scope.get("query_string", b"").decode("ascii")
    return URL(BACKEND + path + ("?" + query if query else ""), encoded=True)


@router.get("/jupyter", include_in_schema=False)
async def jupyter_redirect():
    return RedirectResponse("/jupyter/lab")


@router.api_route("/jupyter/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
async def proxy_http(request: Request, path: str):
    session = aiohttp.ClientSession(auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(), timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))
    try:
        upstream = await session.request(request.method, target(request), headers=headers_for(request), data=request.stream(), allow_redirects=False)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        await session.close()
        return Response("Jupyter is unavailable", status_code=503)

    async def cleanup():
        upstream.close()
        await session.close()

    async def stream():
        try:
            async for chunk in upstream.content.iter_any():
                yield chunk
        finally:
            await cleanup()

    response = StreamingResponse(stream(), status_code=upstream.status, background=BackgroundTask(cleanup))
    excluded = HOP | {x.strip().lower() for x in upstream.headers.get("Connection", "").split(",")}
    response.raw_headers = [(k, v) for k, v in upstream.raw_headers if k.decode().lower() not in excluded]
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
