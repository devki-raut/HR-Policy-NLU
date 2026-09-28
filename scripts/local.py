"""Start/status/stop local services. Stop affects only processes started here."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.request import urlopen

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional in local runtime
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if load_dotenv is not None:
    load_dotenv(ROOT / '.env', override=False)

RUN = ROOT / 'artifacts/local'
SERVICES = {
    'actions': ('http://127.0.0.1:5055/health', ['.venv-runtime/bin/rasa', 'run', 'actions', '--actions', 'hr_policy.actions']),
    'rasa': ('http://127.0.0.1:5005/webhooks/rest/', ['.venv-runtime/bin/rasa', 'run', '--credentials', 'credentials.yml', '--interface', '127.0.0.1', '--enable-api']),
    'api': ('http://127.0.0.1:8000/health', ['.venv-runtime/bin/uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000']),
    'model-backend': ('http://127.0.0.1:9001/v1/models', ['.venv/bin/python', 'scripts/local_model_host.py']),
    'model': ('http://127.0.0.1:9000/health', ['.venv-runtime/bin/python', 'scripts/local_model_service.py']),
}

def healthy(url):
    try:
        with urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False

def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'stop'])
    args = parser.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    if args.command == 'start':
        if not (ROOT / '.venv-runtime/bin/rasa').exists() or not list((ROOT / 'models').glob('*.tar.gz')) or not (ROOT / 'credentials.yml').exists():
            parser.error('Run scripts/setup.sh first; runtime, model and credentials are required')
    failed = False
    entries = list(SERVICES.items())
    if args.command == 'stop':
        entries.reverse()
    for name, (url, command) in entries:
        if args.command != 'stop' and name in {'model', 'model-backend'}:
            if os.getenv('USE_POLICY_GENERATION', '0') in {'0', 'false', 'False', 'disabled', 'no'}:
                print(f'{name}: skipped (USE_POLICY_GENERATION={os.getenv("USE_POLICY_GENERATION", "1")})')
                continue
            if os.getenv('MODEL_BACKEND', '').strip().lower() in {'', 'none', 'off', 'disabled'}:
                print(f'{name}: skipped (MODEL_BACKEND={os.getenv("MODEL_BACKEND", "") or "unset"})')
                continue
        record = RUN / f'{name}.json'
        if args.command == 'stop':
            if record.exists():
                data = json.loads(record.read_text())
                if data.get('identity') is not None and identity(data['pid']) == data['identity']:
                    os.kill(data['pid'], signal.SIGTERM)
                    deadline = time.monotonic() + 20
                    while identity(data['pid']) == data['identity'] and time.monotonic() < deadline:
                        time.sleep(.2)
                    if identity(data['pid']) == data['identity']:
                        print(f'{name}: still stopping; retry later')
                        failed = True
                        continue
                    print(f'{name}: stopped')
                record.unlink()
            else:
                print(f'{name}: not managed here; left running')
            continue
        if args.command == 'start' and not healthy(url):
            env = {
                **os.environ,
                'RASA_TELEMETRY_ENABLED': 'false',
                'USE_POLICY_GENERATION': os.getenv('USE_POLICY_GENERATION', '0'),
                'POLICY_GENERATION_MODEL': os.getenv('POLICY_GENERATION_MODEL', os.getenv('MODEL_NAME', 'HuggingFaceTB/SmolLM2-360M-Instruct')),
                'MODEL_BACKEND': os.getenv('MODEL_BACKEND', 'vllm'),
                'MODEL_NAME': os.getenv('MODEL_NAME', os.getenv('POLICY_GENERATION_MODEL', 'HuggingFaceTB/SmolLM2-360M-Instruct')),
                'MODEL_HOST': os.getenv('MODEL_HOST', '127.0.0.1'),
                'MODEL_PORT': os.getenv('MODEL_PORT', '9000'),
                'MODEL_BACKEND_PORT': os.getenv('MODEL_BACKEND_PORT', '9001'),
                'MODEL_DEVICE': os.getenv('MODEL_DEVICE', 'cpu'),
            }
            with (RUN / f'{name}.log').open('a') as log:
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True, env=env)
            record.write_text(json.dumps({'pid': process.pid, 'identity': identity(process.pid)}))
            deadline = time.monotonic() + 240
            while not healthy(url) and time.monotonic() < deadline and process.poll() is None:
                time.sleep(1)
        ready = healthy(url)
        if name in {'model', 'model-backend'}:
            if ready:
                print(f'{name}: ready — {url}', flush=True)
            else:
                print(f'{name}: unavailable — {url} (optional generation backend)', flush=True)
            continue
        failed |= not ready
        print(f'{name}: {"ready" if ready else "unavailable"} — {url}', flush=True)
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())
