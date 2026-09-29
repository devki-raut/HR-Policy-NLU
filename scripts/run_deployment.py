#!/usr/bin/env python3
"""Run Rasa, its action server, and one or both Teams entry points."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def commands(mode):
    shared = {
        "actions": [PYTHON, "-m", "rasa", "run", "actions", "--port", "5055"],
        "rasa": [PYTHON, "-m", "rasa", "run", "--enable-api", "--port", "5005"],
    }
    if mode in {"web", "combined"}:
        shared["web"] = [
            PYTHON, "-m", "uvicorn", "teams_app.web_package.main:app",
            "--host", "0.0.0.0", "--port", "8610", "--ws", "none",
        ]
    if mode in {"bot", "combined"}:
        shared["bot"] = [PYTHON, "-m", "teams_app.bot_package.main"]
    return shared


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("web", "bot", "combined"))
    args = parser.parse_args()
    os.chdir(ROOT)
    processes = {}

    def stop_all():
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 10
        for process in processes.values():
            if process.poll() is None:
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()

    def handle_signal(_signum, _frame):
        stop_all()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    for name, command in commands(args.mode).items():
        print(f"[deployment] starting {name}: {' '.join(command)}", flush=True)
        processes[name] = subprocess.Popen(command, cwd=ROOT)

    try:
        while True:
            for name, process in processes.items():
                code = process.poll()
                if code is not None:
                    print(f"[deployment] {name} exited with status {code}", flush=True)
                    return code
            time.sleep(0.5)
    finally:
        stop_all()


if __name__ == "__main__":
    raise SystemExit(main())
