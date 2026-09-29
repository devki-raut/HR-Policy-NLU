"""Microsoft Bot Framework adapter configuration."""
import logging
import os
from pathlib import Path
import sys

from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=False)

settings = BotFrameworkAdapterSettings(
    app_id=os.getenv("MICROSOFT_APP_ID") or None,
    app_password=os.getenv("MICROSOFT_APP_PASSWORD") or None,
    channel_auth_tenant=os.getenv("MICROSOFT_TENANT_ID") or None,
)
ADAPTER = BotFrameworkAdapter(settings)


async def _on_error(context, error):
    logging.getLogger(__name__).exception("Bot turn failed", exc_info=error)
    await context.send_activity("Sorry, the bot could not process that message.")


ADAPTER.on_turn_error = _on_error
