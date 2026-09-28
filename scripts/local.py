"""Start/status/stop local services. Stop affects only processes started here."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/local'
SERVICES = {
    'actions': ('http://127.0.0.1:5055/health', ['.venv/bin/rasa', 'run', 'actions', '--actions', 'hr_policy.actions']),
    'rasa': ('http://127.0.0.1:5005/webhooks/rest/', ['.venv/bin/rasa', 'run', '--credentials', 'credentials.yml', '--interface', '127.0.0.1']),
    'api': ('http://127.0.0.1:8000/health', ['.venv/bin/uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000']),
}

def healthy(url):
    try:
        with urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False

def identity(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'stop'])
    args = parser.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    if args.command == 'start':
        if not (ROOT / '.venv/bin/rasa').exists() or not list((ROOT / 'models').glob('*.tar.gz')) or not (ROOT / 'credentials.yml').exists():
            parser.error('Run scripts/setup.sh first; runtime, model and credentials are required')
    failed = False
    entries = list(SERVICES.items())
    if args.command == 'stop':
        entries.reverse()
    for name, (url, command) in entries:
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
            with (RUN / f'{name}.log').open('a') as log:
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True, env={**os.environ, 'RASA_TELEMETRY_ENABLED': 'false'})
            record.write_text(json.dumps({'pid': process.pid, 'identity': identity(process.pid)}))
            deadline = time.monotonic() + 120
            while not healthy(url) and time.monotonic() < deadline and process.poll() is None:
                time.sleep(1)
        ready = healthy(url)
        failed |= not ready
        print(f'{name}: {"ready" if ready else "unavailable"} — {url}', flush=True)
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())
