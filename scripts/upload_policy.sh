#!/usr/bin/env bash
set -euo pipefail

cat <<'EOF'
curl -X POST "http://localhost:8000/policies/upload" \
  -F "file=@/home/ojas/HR-Policy-NLU/policies/pdfs/Bereavement Leave Policy_Emergys Solutions.pdf" \
  -F "mode=upsert"

# With API key:
# curl -X POST "http://localhost:8000/policies/upload" \
#   -H "X-API-Key: your-api-key" \
#   -F "file=@/home/ojas/HR-Policy-NLU/policies/pdfs/Bereavement Leave Policy_Emergys Solutions.pdf" \
#   -F "mode=upsert"
EOF
