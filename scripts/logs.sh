#!/usr/bin/env bash
set -euo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-all}" in
  api|rasa|actions) files=("$DEPLOY_ROOT/artifacts/local/$1.log") ;;
  all) shopt -s nullglob; files=("$DEPLOY_ROOT"/artifacts/local/*.log) ;;
  *) echo 'Usage: logs.sh [api|rasa|actions|all]' >&2; exit 2 ;;
esac
if (( ${#files[@]} == 0 )) || [[ ! -f "${files[0]}" ]]; then
  echo 'No managed logs found. Services started elsewhere keep their original logs.' >&2
  exit 1
fi
exec tail -n 100 -F "${files[@]}"
