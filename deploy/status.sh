#!/usr/bin/env bash
set -euo pipefail
mode="${1:-all}"

local_status() {
  for port in 5055 5005 8610; do
    pids="$(fuser -n tcp "$port" 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      echo "port $port: running (PIDs:$pids)"
    else
      echo "port $port: stopped"
    fi
  done
}

docker_status() {
  if docker inspect -f '{{.State.Status}}' employeeassist 2>/dev/null; then
    return 0
  fi
  echo "employeeassist container: stopped"
}

case "$mode" in
  local) local_status ;;
  docker) docker_status ;;
  all) local_status; docker_status ;;
  *)
    echo "Usage: $0 [local|docker|all]" >&2
    exit 2
    ;;
esac
