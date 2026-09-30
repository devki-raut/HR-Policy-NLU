#!/usr/bin/env bash
# Print copy-pasteable EmployeeAssist service commands. This file does not
# start or stop any service itself.
cat <<'COMMANDS'
cd /home/ojas/HR-Policy-NLU

# Local deployment
./deploy/start.sh local
./deploy/status.sh local
./deploy/stop.sh local
journalctl --user -u employeeassist.service -f

# Docker deployment
./deploy/start.sh docker
./deploy/status.sh docker
./deploy/stop.sh docker
journalctl --user -u employeeassist-docker.service -f

# Check or stop both deployment types
./deploy/status.sh all
./deploy/stop.sh all
COMMANDS
