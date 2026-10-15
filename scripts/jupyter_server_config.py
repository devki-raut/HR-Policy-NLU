"""Private Jupyter backend for the webapp's same-domain /jupyter/ proxy."""
import os
from pathlib import Path

from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env", override=False)
import sys
if str(root) not in sys.path: sys.path.insert(0,str(root))
from actions.runtime_paths import runtime_path
runtime = runtime_path('jupyter/runtime', root/'artifacts/jupyter/runtime')
runtime.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('JUPYTER_RUNTIME_DIR',str(runtime))
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
from teams_app.web_paths import jupyter_base_path
c.ServerApp.base_url = jupyter_base_path()
c.ServerApp.default_url = jupyter_base_path() + 'lab'
c.ServerApp.allow_remote_access = True
c.ServerApp.trust_xheaders = True
