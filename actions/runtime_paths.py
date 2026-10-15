"""Writable runtime locations; explicit overrides win over Azure /tmp defaults."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def runtime_root() -> Path | None:
    configured = os.getenv('DATA_ROOT', '').strip()
    if configured:
        root = Path(configured).expanduser()
        return (root if root.is_absolute() else PROJECT_ROOT / root) / 'employeeassist'
    if os.getenv('WEBSITE_INSTANCE_ID'):
        return Path('/tmp/employeeassist')
    return None


def runtime_path(relative, local_default, env_name=None, project_root=None) -> Path:
    configured = os.getenv(env_name, '').strip() if env_name else ''
    root = runtime_root()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (root or project_root or PROJECT_ROOT) / path
    return root / relative if root else Path(local_default)
