#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
source "$root/deploy/runtime.sh"
mkdir -p "$employeeassist_log_dir"
mode="${1:-root}"
employeeassist_tunnel_port="$employeeassist_web_port"
case "$mode" in
  root) ;;
  subroute)
    employeeassist_configured_prefix="$(python3 -c 'from teams_app.web_paths import base_path; print(base_path())')"
    if [[ "$employeeassist_configured_prefix" != /employeeassist ]]; then
      echo 'Set PUBLIC_BASE_URL to the ngrok domain plus /employeeassist/ and restart the full deployment before using subroute mode.' >&2
      exit 2
    fi
    employeeassist_proxy_runtime="$root/$employeeassist_log_dir/nginx-subroute"
    if [[ "$employeeassist_log_dir" == /* ]]; then
      employeeassist_proxy_runtime="$employeeassist_log_dir/nginx-subroute"
    fi
    mkdir -p "$employeeassist_proxy_runtime"
    employeeassist_tunnel_port="${NGROK_SUBROUTE_PORT:-18610}"
    python3 scripts/render_subroute_nginx.py \
      --output "$employeeassist_proxy_runtime/nginx.conf" \
      --runtime "$employeeassist_proxy_runtime" \
      --web-port "$employeeassist_web_port" --proxy-port "$employeeassist_tunnel_port"
    nginx -t -p "$employeeassist_proxy_runtime/" -c "$employeeassist_proxy_runtime/nginx.conf" -g 'error_log stderr;'
    if [[ -s "$employeeassist_proxy_runtime/nginx.pid" ]] && kill -0 "$(cat "$employeeassist_proxy_runtime/nginx.pid")" 2>/dev/null; then
      nginx -p "$employeeassist_proxy_runtime/" -c "$employeeassist_proxy_runtime/nginx.conf" -s reload -g 'error_log stderr;'
    else
      nginx -p "$employeeassist_proxy_runtime/" -c "$employeeassist_proxy_runtime/nginx.conf" -g 'error_log stderr;'
    fi
    ;;
  *) echo "Usage: $0 [root|subroute]" >&2; exit 2 ;;
esac
nohup ngrok http "$employeeassist_tunnel_port" \
  --url "${NGROK_PUBLIC_URL:-https://flounder-sphere-dab.ngrok-free.dev}" \
  > "$employeeassist_log_dir/ngrok.log" 2>&1 &
echo $! > "$employeeassist_log_dir/ngrok.pid"
