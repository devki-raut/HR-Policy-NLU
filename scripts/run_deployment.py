#!/usr/bin/env python3
"""Run Rasa, its action server, and the EmployeeAssist web/Teams endpoint."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from actions.runtime_paths import runtime_log_paths, runtime_path, runtime_root
from actions.runtime_bootstrap import bootstrap_runtime_files, sync_runtime_backups
from teams_app.web_paths import web_port, jupyter_base_path

PYTHON = sys.executable
LOG_DIR = runtime_path("logs", ROOT/"artifacts/logs")
LOG_MIRROR_DIR = None
PORTS_BY_MODE = {
    "web": (5055, 5005, 8888, 8610),
    "bot": (5055, 5005, 3978),
    "combined": (5055, 5005, 8888, 8610, 3978),
}


class _LogOutputs:
    def __init__(self, handles):
        self.handles = handles

    def write(self, value):
        for handle in self.handles:
            handle.write(value)
        return len(value)

    def flush(self):
        for handle in self.handles:
            handle.flush()

    def close(self):
        for handle in self.handles:
            handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _open_log(name):
    directories = [LOG_DIR]
    if LOG_MIRROR_DIR is not None and LOG_MIRROR_DIR != LOG_DIR:
        directories.append(LOG_MIRROR_DIR)
    handles = []
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(path, 0o600)
        handles.append(os.fdopen(fd, "a", encoding="utf-8", buffering=1))
    return _LogOutputs(handles)


def _report(message):
    print(message, flush=True)
    with _open_log("deployment") as output:
        output.write(message + "\n")


def _relay_output(name, source, output):
    with source, output:
        for line in source:
            output.write(line)
            print(f"[{name}] {line}", end="", flush=True)


def _start_service(name, command):
    output = _open_log(name)
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except Exception:
        output.close()
        raise
    relay = threading.Thread(
        target=_relay_output, args=(name, process.stdout, output), daemon=True,
    )
    relay.start()
    process.log_relay = relay
    return process


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

    _report(f"[deployment] restarting existing listeners on ports {busy} (PIDs {sorted(pids)})")
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


def restart_targets(target, processes):
    """Resolve the requested service against the processes in this deployment."""
    if target == "all":
        return list(processes)
    if target == "bot" and "bot" not in processes and "web" in processes:
        # In web mode, POST /api/messages is served by the web process.
        target = "web"
    if target not in processes:
        raise ValueError(f"{target} is not running in this deployment")
    return [target]


def restart_services(target, processes, mode):
    names = restart_targets(target, processes)
    for name in names:
        process = processes[name]
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        relay = getattr(process, "log_relay", None)
        if relay:
            relay.join(timeout=2)
    sync_runtime_backups()
    current_commands = commands(mode)
    for name in names:
        bootstrap_runtime_files()
        if name in {"actions", "rasa", "web", "bot", "jupyter"} and darwinbox_report_configured():
            refresh = subprocess.run(
                [PYTHON, str(ROOT / "scripts/sync_employee_report.py")],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            with _open_log("employee-report-sync") as output:
                for line in (refresh.stdout + refresh.stderr).splitlines(True):
                    output.write(line)
                    print(f"[employee-report-sync] {line}", end="", flush=True)
            if refresh.returncode:
                _report("[deployment] employee report refresh failed during restart; retained the last snapshot")
        _report(f"[deployment] restarting {name}")
        processes[name] = _start_service(name, current_commands[name])
    return {name: processes[name].pid for name in names}


def control_socket_path():
    return runtime_path("control/deployment.sock", ROOT/"artifacts/control/deployment.sock")


def request_restart(target):
    path = control_socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(120)
        try:
            client.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise RuntimeError("deployment supervisor is not running; start it first") from exc
        client.sendall((json.dumps({"restart": target}) + "\n").encode())
        response = client.recv(4096)
    if not response:
        raise RuntimeError("deployment supervisor closed the control connection")
    result = json.loads(response)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def main():
    global LOG_DIR, LOG_MIRROR_DIR
    if len(sys.argv) >= 2 and sys.argv[1] == "restart":
        parser = argparse.ArgumentParser(description="Restart a service in the running deployment")
        parser.add_argument("command", choices=("restart",))
        parser.add_argument("target", choices=("all", "rasa", "actions", "web", "bot", "jupyter"))
        args = parser.parse_args()
        load_dotenv(ROOT / ".env", override=False)
        print(json.dumps(request_restart(args.target)))
        return 0
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
    bootstrap_runtime_files()
    log_paths = runtime_log_paths("logs", ROOT/"artifacts/logs")
    LOG_DIR = log_paths[0]
    LOG_MIRROR_DIR = log_paths[1] if len(log_paths) > 1 else None
    if runtime_root():
        for name, relative in {
            'JUPYTER_CONFIG_DIR': 'jupyter/config', 'JUPYTER_DATA_DIR': 'jupyter/data',
            'JUPYTER_RUNTIME_DIR': 'jupyter/runtime', 'IPYTHONDIR': 'jupyter/ipython',
        }.items():
            os.environ.setdefault(name, str(runtime_path(relative, ROOT/'artifacts'/relative)))
    if args.mode in {"web", "combined"} and not os.environ.get("JUPYTER_TOKEN", "").strip():
        parser.error("JUPYTER_TOKEN must be set before starting Jupyter")
    for log_dir in log_paths:
        log_dir.mkdir(parents=True, exist_ok=True)
    control_path = control_socket_path()
    if control_path.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                probe.connect(str(control_path))
            parser.error(f"deployment supervisor is already running at {control_path}; use the restart command")
        except (ConnectionRefusedError, FileNotFoundError):
            pass

    if darwinbox_directory_configured():
        _report("[deployment] synchronizing Darwinbox employee directory")
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
            _report("[deployment] Darwinbox sync failed; continuing with the last successful cache")

    ports = tuple(web_port() if p == 8610 else p for p in PORTS_BY_MODE[args.mode])
    busy = [port for port in ports if port_is_busy(port)]
    if busy and args.no_restart:
        parser.error(f"deployment ports are already in use: {busy}")
    release_ports(ports)

    processes = {}
    control_path.parent.mkdir(parents=True, exist_ok=True)
    if control_path.exists():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                probe.connect(str(control_path))
            raise RuntimeError(f"deployment supervisor is already running at {control_path}")
        except (ConnectionRefusedError, FileNotFoundError):
            control_path.unlink(missing_ok=True)
    control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    control.bind(str(control_path))
    os.chmod(control_path, 0o600)
    control.listen(4)
    control.settimeout(0.5)

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
        for process in processes.values():
            relay = getattr(process, "log_relay", None)
            if relay:
                relay.join(timeout=2)
        sync_runtime_backups()

    def handle_signal(_signum, _frame):
        stop_all()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        for name, command in commands(args.mode).items():
            _report(f"[deployment] starting {name}: {' '.join(command)}")
            processes[name] = _start_service(name, command)
        while True:
            for name, process in processes.items():
                code = process.poll()
                if code is not None:
                    _report(f"[deployment] {name} exited with status {code}")
                    return code
            try:
                connection, _ = control.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(2)
                try:
                    request = json.loads(connection.recv(4096))
                    if not isinstance(request, dict) or not isinstance(request.get("restart"), str):
                        raise ValueError("expected a restart target")
                    restarted = restart_services(request["restart"], processes, args.mode)
                    response = {"restarted": restarted}
                except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                    response = {"error": str(exc)}
                try:
                    connection.sendall((json.dumps(response) + "\n").encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass
    finally:
        stop_all()
        control.close()
        control_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
