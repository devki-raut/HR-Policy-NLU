#!/usr/bin/env python3
"""Run Rasa, its action server, and the EmployeeAssist web/Teams endpoint."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from actions.runtime_paths import runtime_path, runtime_root
from teams_app.web_paths import web_port, jupyter_base_path

PYTHON = sys.executable
LOG_DIR = runtime_path("logs", ROOT/"artifacts/logs")
PORTS_BY_MODE = {
    "web": (5055, 5005, 8888, 8610),
    "bot": (5055, 5005, 3978),
    "combined": (5055, 5005, 8888, 8610, 3978),
}


def darwinbox_directory_configured():
    endpoint = bool(
        os.environ.get("DARWINBOX_EMPLOYEE_URL", "").strip()
        or os.environ.get("DARWINBOX_BASE_URL", "").strip()
    )
    dataset = bool(
        os.environ.get("DARWINBOX_EMPLOYEE_DATASET_KEY", "").strip()
        or os.environ.get("DARWINBOX_DATASET_KEY", "").strip()
    )
    return endpoint and dataset and all(
        os.environ.get(name, "").strip()
        for name in ("DARWINBOX_UID", "DARWINBOX_ADMIN_EMAIL", "DARWINBOX_SECRET_KEY")
    )


def darwinbox_report_configured():
    endpoint = bool(
        os.environ.get("DARWINBOX_REPORT_URL", "").strip()
        or os.environ.get("DARWINBOX_BASE_URL", "").strip()
    )
    username = os.environ.get("DARWINBOX_LEAVE_USERNAME", "").strip()
    password = os.environ.get("DARWINBOX_LEAVE_PASSWORD", "").strip()
    return endpoint and bool(username and password) and all(
        os.environ.get(name, "").strip()
        for name in ("DARWINBOX_REPORT_API_KEY", "DARWINBOX_REPORT_ID")
    )


def commands(mode):
    shared = {}
    if darwinbox_report_configured():
        shared["employee-report-sync"] = [
            PYTHON, str(ROOT / "scripts/sync_employee_report.py"), "--watch",
        ]
    if darwinbox_directory_configured():
        shared["darwinbox-sync"] = [
            PYTHON, str(ROOT / "scripts/sync_darwinbox_employees.py"),
            "--watch", "--incremental-only",
        ]
    shared.update({
        "actions": [PYTHON, "-m", "rasa", "run", "actions", "--port", "5055"],
        "rasa": [PYTHON, "-m", "rasa", "run", "--enable-api", "--connector", "rest", "--interface", "127.0.0.1", "--port", "5005"],
    })
    if mode in {"web", "combined"}:
        shared["jupyter"] = [
            os.environ.get("JUPYTER_PYTHON", str(ROOT / ".venv-jupyter/bin/python")),
            "-m", "jupyterlab", "--config", str(ROOT / "scripts/jupyter_server_config.py"),
            "--ServerApp.base_url=" + jupyter_base_path(),
            "--ServerApp.default_url=" + jupyter_base_path() + "lab",
        ]
        shared["web"] = [
            PYTHON, "-m", "uvicorn", "teams_app.web_package.main:app",
            "--host", "0.0.0.0", "--port", str(web_port()), "--ws", "wsproto",
        ]
    if mode in {"bot", "combined"}:
        shared["bot"] = [PYTHON, "-m", "teams_app.bot_package.main"]
    return shared


def port_is_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def listener_pids(port):
    if shutil.which("fuser") is None:
        raise RuntimeError("fuser is required to restart processes using deployment ports")
    result = subprocess.run(
        ["fuser", "-n", "tcp", str(port)],
        capture_output=True,
        text=True,
        check=False,
    )
    return {int(value) for value in result.stdout.split() if value.isdigit()}


def release_ports(ports, timeout=10):
    busy = [port for port in ports if port_is_busy(port)]
    if not busy:
        return

    pids = set()
    for port in busy:
        pids.update(listener_pids(port))
    pids.discard(os.getpid())
    pids.discard(os.getppid())
    if not pids:
        raise RuntimeError(f"Ports are busy but their processes could not be identified: {busy}")

    print(f"[deployment] restarting existing listeners on ports {busy} (PIDs {sorted(pids)})", flush=True)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise RuntimeError(f"Cannot stop PID {pid}; run it as the same user") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(port_is_busy(port) for port in busy):
            return
        time.sleep(0.25)

    remaining = set()
    for port in busy:
        remaining.update(listener_pids(port))
    remaining.discard(os.getpid())
    remaining.discard(os.getppid())
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not any(port_is_busy(port) for port in busy):
            return
        time.sleep(0.25)
    raise RuntimeError(f"Could not release deployment ports: {busy}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("web", "bot", "combined"))
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="fail instead of stopping processes already listening on deployment ports",
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env", override=False)
    if runtime_root():
        for name, relative in {
            'JUPYTER_CONFIG_DIR': 'jupyter/config', 'JUPYTER_DATA_DIR': 'jupyter/data',
            'JUPYTER_RUNTIME_DIR': 'jupyter/runtime', 'IPYTHONDIR': 'jupyter/ipython',
        }.items():
            os.environ.setdefault(name, str(runtime_path(relative, ROOT/'artifacts'/relative)))
    if args.mode in {"web", "combined"} and not os.environ.get("JUPYTER_TOKEN", "").strip():
        parser.error("JUPYTER_TOKEN must be set before starting Jupyter")
    runtime_path("logs", ROOT/"artifacts/logs").mkdir(parents=True, exist_ok=True)

    if darwinbox_directory_configured():
        print("[deployment] synchronizing Darwinbox employee directory", flush=True)
        sync = subprocess.run(
            [PYTHON, str(ROOT / "scripts/sync_darwinbox_employees.py")],
            cwd=ROOT,
            check=False,
        )
        if sync.returncode and os.environ.get(
            "DARWINBOX_EMPLOYEE_SYNC_REQUIRED", "false"
        ).casefold() in {"1", "true", "yes"}:
            parser.error("required Darwinbox employee directory sync failed")
        if sync.returncode:
            print(
                "[deployment] Darwinbox sync failed; continuing with the last successful cache",
                flush=True,
            )

    ports = tuple(web_port() if p == 8610 else p for p in PORTS_BY_MODE[args.mode])
    busy = [port for port in ports if port_is_busy(port)]
    if busy and args.no_restart:
        parser.error(f"deployment ports are already in use: {busy}")
    release_ports(ports)

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
