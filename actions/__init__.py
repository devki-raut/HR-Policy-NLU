"""Load local settings for independently launched action workers.

App Service process settings take precedence. Tests and environment-only
diagnostics disable this through PYTHON_DOTENV_DISABLED.
"""
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)

from .runtime_bootstrap import bootstrap_runtime_files

bootstrap_runtime_files()
