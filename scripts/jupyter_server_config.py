"""Private Jupyter backend for the webapp's same-domain /jupyter/ proxy."""
import os
from pathlib import Path

from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env", override=False)
token = os.environ.get("JUPYTER_TOKEN", "")
if not token.strip():
    raise RuntimeError("JUPYTER_TOKEN is required for Jupyter")

c = get_config()  # noqa: F821
c.IdentityProvider.token = token
c.ServerApp.ip = "127.0.0.1"
c.ServerApp.port = 8888
c.ServerApp.port_retries = 0
c.ServerApp.open_browser = False
c.ServerApp.root_dir = str(root)
c.ServerApp.base_url = "/jupyter/"
c.ServerApp.default_url = "/lab"
c.ServerApp.allow_remote_access = True
c.ServerApp.trust_xheaders = True
