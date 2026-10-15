#!/usr/bin/env bash
set -euo pipefail
mode="${1:-all}"
source "$(dirname "${BASH_SOURCE[0]}")/runtime.sh"

stop_local() {
  for port in 5055 5005 8888 "$employeeassist_web_port"; do
    pids="$(fuser -n tcp "$port" 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      echo "Stopping port $port (PIDs:$pids)"
      kill -TERM $pids 2>/dev/null || true
    fi
  done
}

stop_docker() {
  docker stop -t 20 employeeassist 2>/dev/null || true
}

case "$mode" in
  local) stop_local ;;
  docker) stop_docker ;;
  all) stop_docker; stop_local ;;
  *)
    echo "Usage: $0 [local|docker|all]" >&2
    exit 2
    ;;
esac
