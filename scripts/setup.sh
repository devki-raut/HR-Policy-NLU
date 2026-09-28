#!/usr/bin/env bash
# Install and prepare this checkout without overwriting policies or credentials.
set -euo pipefail
DEPLOY_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DEPLOY_ROOT"
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt
if [[ ! -e credentials.yml ]]; then
  cp credentials.example.yml credentials.yml
fi
if [[ ! -f "${POLICY_INDEX:-artifacts/policies.json}" ]]; then
  .venv/bin/python -m hr_policy.engine ingest policies/sample --output "${POLICY_INDEX:-artifacts/policies.json}"
fi
export RASA_TELEMETRY_ENABLED=false
.venv/bin/rasa data validate
if ! compgen -G 'models/*.tar.gz' > /dev/null; then
  .venv/bin/rasa train
fi
.venv/bin/python -m unittest discover -s tests -v
printf '%s\n' 'Setup complete. Run make local-start.'
