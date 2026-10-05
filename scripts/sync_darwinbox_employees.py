#!/usr/bin/env python3
"""Synchronize the protected local employee directory from Darwinbox."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from actions.darwinbox import DarwinboxAPIError, DarwinboxConfigurationError, EmployeeDirectoryAdapter


async def synchronize(full: bool) -> bool:
    try:
        result = await EmployeeDirectoryAdapter().sync(full=full)
        print("[darwinbox-sync] " + json.dumps(result, sort_keys=True), flush=True)
        return True
    except DarwinboxConfigurationError as exc:
        print(f"[darwinbox-sync] skipped: {exc}", flush=True)
        return False
    except (DarwinboxAPIError, OSError, RuntimeError, ValueError) as exc:
        print(f"[darwinbox-sync] failed; retaining last successful cache: {exc}", flush=True)
        return False


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--incremental-only", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    success = await synchronize(full=not args.incremental_only)
    if not args.watch:
        configured = bool(
            os.getenv("DARWINBOX_BASE_URL", "").strip()
            or os.getenv("DARWINBOX_EMPLOYEE_URL", "").strip()
        )
        return 0 if success or not configured else 1
    interval = max(60, int(os.getenv("DARWINBOX_EMPLOYEE_SYNC_INTERVAL_SECONDS", "3600")))
    while True:
        await asyncio.sleep(interval)
        await synchronize(full=False)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
