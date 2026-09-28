#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${APP_PUBLIC_URL:-${PUBLIC_BASE_URL:-http://127.0.0.1:8000}}"

cat <<EOF
curl -X POST "${BASE_URL}/chat" \
  -H "Content-Type: application/json" \
  -d '{"sender":"tester","message":"What is the bereavement leave policy?"}'

curl -X GET "${BASE_URL}/policies" \
  -H "Content-Type: application/json"

# With API key:
# curl -X POST "${BASE_URL}/chat" \
#   -H "Content-Type: application/json" \
#   -H "X-API-Key: your-api-key" \
#   -d '{"sender":"tester","message":"What is the bereavement leave policy?"}'

# Enable generation on the action-server environment and restart it first:
# USE_POLICY_GENERATION=1
# POLICY_GENERATION_URL=http://127.0.0.1:9000/generate
# curl -X POST "${BASE_URL}/chat" \
#   -H "Content-Type: application/json" \
#   -d '{"sender":"tester","message":"What is the bereavement leave policy?"}'
EOF
