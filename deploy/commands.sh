#!/usr/bin/env bash
# Print copy-pasteable EmployeeAssist deployment commands. This file does not
# start or stop any service itself.
cat <<'COMMANDS'
cd /home/ojas/HR-Policy-NLU

# Local background deployment
make run_deployment
make restart-deployment
make status-deployment
make logs-deployment
make stop-deployment

# Foreground helper, useful for interactive debugging
./deploy/start.sh local

# Local Docker testing (foreground; Azure manages the production container)
./deploy/start.sh docker
./deploy/status.sh docker
./deploy/stop.sh docker
COMMANDS
