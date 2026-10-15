#!/usr/bin/env bash
# Shared writable log directory and web listener defaults; no credential loading.
employeeassist_web_port="${WEB_PORT:-${PORT:-${WEBSITES_PORT:-}}}"
if [[ -z "$employeeassist_web_port" ]]; then
  employeeassist_web_port=8610
  [[ -z "${WEBSITE_INSTANCE_ID:-}" ]] || employeeassist_web_port=8608
fi
employeeassist_log_dir=artifacts/logs
if [[ -n "${DATA_ROOT:-}" ]]; then
  employeeassist_log_dir="${DATA_ROOT%/}/employeeassist/logs"
elif [[ -n "${WEBSITE_INSTANCE_ID:-}" ]]; then
  employeeassist_log_dir=/tmp/employeeassist/logs
fi
