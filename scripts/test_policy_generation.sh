#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${HR_API_KEY:-}"
PDF_PATH="${PDF_PATH:-policies/pdfs/Bereavement Leave Policy_Emergys Solutions.pdf}"
MODE="${MODE:-upsert}"

if [[ ! -f "$PDF_PATH" ]]; then
  echo "PDF not found: $PDF_PATH"
  exit 1
fi

if [[ -n "$API_KEY" ]]; then
  AUTH_HEADER=(-H "X-API-Key: $API_KEY")
else
  AUTH_HEADER=()
fi

printf '\n--- 1) upload policy ---\n'
curl -fsS -X POST "${BASE_URL}/policies/upload" \
  "${AUTH_HEADER[@]}" \
  -F "file=@${PDF_PATH}" \
  -F "mode=${MODE}"

printf '\n\n--- 2) ask a factual question ---\n'
curl -fsS -X POST "${BASE_URL}/chat" \
  "${AUTH_HEADER[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"sender":"tester","message":"How many days of bereavement leave are allowed?"}'

printf '\n\n--- 3) ask a policy question with generation enabled ---\n'
# Generation must already be enabled on the running action server.
# Exporting a variable in this client cannot change a running server.

curl -fsS -X POST "${BASE_URL}/chat" \
  "${AUTH_HEADER[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"sender":"tester","message":"How do I apply for bereavement leave?"}'

printf '\n\n--- 4) ask a second question for citation-sourced answer ---\n'
curl -fsS -X POST "${BASE_URL}/chat" \
  "${AUTH_HEADER[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"sender":"tester","message":"Is a death certificate needed for bereavement leave?"}'

printf '\n'
