#!/usr/bin/env bash
set -euo pipefail
mode="${1:-local}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
source "$root/deploy/runtime.sh"

case "$mode" in
  local)
    exec .venv-runtime/bin/python scripts/run_deployment.py web
    ;;
  docker)
    ./deploy/stop.sh all
    docker build --target deployment -f Dockerfile -t employeeassist:local .
    exec docker run --rm --name employeeassist --env-file .env \
      -e WEB_PORT="$employeeassist_web_port" \
      -p "$employeeassist_web_port:$employeeassist_web_port" \
      -v "$root/data/policies:/app/data/policies" \
      -v "$root/artifacts:/app/artifacts" \
      employeeassist:local
    ;;
  *)
    echo "Usage: $0 [local|docker]" >&2
    exit 2
    ;;
esac
