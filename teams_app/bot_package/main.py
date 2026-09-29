"""HTTP entry point used by Azure Bot Service and the Teams channel."""
import os
from pathlib import Path
import sys

from aiohttp import web
from botbuilder.schema import Activity

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from teams_app.bot_package.adapter import ADAPTER
from teams_app.bot_package.bot_handler import BOT


async def messages(request: web.Request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Expected application/json")
    activity = Activity().deserialize(await request.json())
    auth_header = request.headers.get("Authorization", "")
    invoke_response = await ADAPTER.process_activity(
        activity, auth_header, BOT.on_turn
    )
    if invoke_response:
        return web.json_response(
            data=invoke_response.body,
            status=invoke_response.status,
        )
    return web.Response(status=201)


async def health(_request: web.Request):
    return web.json_response({"status": "ok", "service": "teams-bot"})


app = web.Application()
app.router.add_post("/api/messages", messages)
app.router.add_get("/health", health)


def main():
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("BOT_PORT", "3978")))


if __name__ == "__main__":
    main()
