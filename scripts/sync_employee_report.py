#!/usr/bin/env python3
"""Refresh the employee email mapping from a Darwinbox Report Builder report."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from actions.darwinbox_adapters import (  # noqa: E402
    DarwinboxAPIError, DarwinboxConfigurationError, EmployeeReportAdapter,
)


async def synchronize() -> bool:
    try:
        result = await EmployeeReportAdapter().sync()
        print("[employee-report-sync] " + json.dumps(result, sort_keys=True), flush=True)
        return True
    except DarwinboxConfigurationError as exc:
        print(f"[employee-report-sync] skipped: {exc}", flush=True)
    except (DarwinboxAPIError, OSError, RuntimeError, ValueError) as exc:
        print(f"[employee-report-sync] failed; retaining last successful directory: {exc}", flush=True)
    return False


def next_run(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="run daily at the configured India time")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    if not args.watch:
        return 0 if await synchronize() else 1

    # Populate a missing/stale directory immediately on service start, then
    # retain the daily India-time refresh schedule.
    await synchronize()
    hour = int(os.getenv("DARWINBOX_REPORT_SYNC_HOUR", "16"))
    minute = int(os.getenv("DARWINBOX_REPORT_SYNC_MINUTE", "30"))
    if hour != 16 or not 0 <= minute <= 59:
        raise SystemExit("DARWINBOX report sync must be scheduled between 16:00 and 16:59")
    zone = ZoneInfo("Asia/Kolkata")
    while True:
        now = datetime.now(zone)
        target = next_run(now, hour, minute)
        print(f"[employee-report-sync] next run: {target.isoformat()}", flush=True)
        await asyncio.sleep(max(1, (target - now).total_seconds()))
        await synchronize()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
