SHELL := /bin/bash

PYTHON := .venv-runtime/bin/python
PIP := .venv-runtime/bin/pip
RASA := .venv-runtime/bin/rasa

-include .env

export RASA_TELEMETRY_ENABLED := false
export FAQ_EMBEDDING_MODEL ?= sentence-transformers/all-MiniLM-L6-v2
export FAQ_EMBEDDING_THRESHOLD ?= 0.75
export FAQ_EMBEDDING_MARGIN ?= 0.01

DEPLOYMENT_PID := artifacts/logs/deployment.pid
DEPLOYMENT_LOG := artifacts/logs/deployment.log

.PHONY: setup install sync-intents validate train test evaluate-final evaluate-unseen evaluate-fact-coverage benchmark-embeddings actions chat serve start run_deployment restart_deployment restart-deployment stop_deployment stop-deployment status_deployment status-deployment logs_deployment logs-deployment docker_deployment clean

setup:
	python3 setup.py

install: setup

sync-intents:
	$(PYTHON) scripts/sync_intents.py

validate:
	$(RASA) data validate

train: sync-intents validate
	$(RASA) train

test:
	@if [ -d tests ]; then $(PYTHON) -m unittest discover -s tests -v; else echo "No tests directory; skipping tests."; fi

evaluate-final:
	$(PYTHON) scripts/evaluate_final_pipeline.py

evaluate-unseen:
	$(PYTHON) scripts/evaluate_unseen_questions.py

evaluate-fact-coverage:
	$(PYTHON) scripts/evaluate_document_fact_coverage.py

benchmark-embeddings:
	$(PYTHON) scripts/benchmark_embeddings.py

actions:
	$(RASA) run actions

chat:
	$(RASA) shell

serve:
	$(RASA) run --enable-api --port 5005

# Run the action server and Rasa HTTP API together. Ctrl+C stops both.
start:
	@set -eu; \
	$(RASA) run actions --port 5055 & action_pid=$$!; \
	$(RASA) run --enable-api --port 5005 & rasa_pid=$$!; \
	trap 'kill $$action_pid $$rasa_pid 2>/dev/null || true; wait $$action_pid $$rasa_pid 2>/dev/null || true' EXIT INT TERM; \
	wait -n $$action_pid $$rasa_pid


# Separate web/bot launchers are intentionally disabled. The single deployment
# serves the web UI and Teams bot endpoint on port 8610.
# run_web:
#	$(PYTHON) scripts/run_deployment.py web
# run_bot:
#	$(PYTHON) scripts/run_deployment.py bot

# Run the combined local deployment in the background.
run_deployment:
	@mkdir -p artifacts/logs
	@if [ -f "$(DEPLOYMENT_PID)" ] && kill -0 "$$(cat "$(DEPLOYMENT_PID)")" 2>/dev/null; then \
		echo "EmployeeAssist is already running (PID $$(cat "$(DEPLOYMENT_PID)"))."; \
	else \
		rm -f "$(DEPLOYMENT_PID)"; \
		nohup $(PYTHON) scripts/run_deployment.py web >> "$(DEPLOYMENT_LOG)" 2>&1 & \
		echo $$! > "$(DEPLOYMENT_PID)"; \
		sleep 2; \
		if kill -0 "$$(cat "$(DEPLOYMENT_PID)")" 2>/dev/null; then \
			echo "EmployeeAssist started in the background (PID $$(cat "$(DEPLOYMENT_PID)"))."; \
			echo "Logs: $(DEPLOYMENT_LOG)"; \
		else \
			echo "EmployeeAssist failed to start. Recent logs:" >&2; \
			tail -n 40 "$(DEPLOYMENT_LOG)" >&2; \
			rm -f "$(DEPLOYMENT_PID)"; \
			exit 1; \
		fi; \
	fi

stop_deployment:
	@if [ -f "$(DEPLOYMENT_PID)" ]; then \
		pid="$$(cat "$(DEPLOYMENT_PID)")"; \
		if kill -0 "$$pid" 2>/dev/null; then \
			echo "Stopping EmployeeAssist (PID $$pid)..."; \
			kill -TERM "$$pid"; \
			for attempt in $$(seq 1 40); do \
				kill -0 "$$pid" 2>/dev/null || break; \
				sleep 0.25; \
			done; \
			if kill -0 "$$pid" 2>/dev/null; then kill -KILL "$$pid"; fi; \
		fi; \
		rm -f "$(DEPLOYMENT_PID)"; \
	fi
	@./deploy/stop.sh local
	@echo "EmployeeAssist stopped."

restart_deployment: stop_deployment run_deployment

status_deployment:
	@if [ -f "$(DEPLOYMENT_PID)" ] && kill -0 "$$(cat "$(DEPLOYMENT_PID)")" 2>/dev/null; then \
		echo "EmployeeAssist supervisor: running (PID $$(cat "$(DEPLOYMENT_PID)"))"; \
	else \
		echo "EmployeeAssist supervisor: stopped"; \
	fi
	@./deploy/status.sh local

logs_deployment:
	@mkdir -p artifacts/logs
	@touch "$(DEPLOYMENT_LOG)"
	@tail -f "$(DEPLOYMENT_LOG)"

restart-deployment: restart_deployment
stop-deployment: stop_deployment
status-deployment: status_deployment
logs-deployment: logs_deployment

# docker_web and docker_bot are intentionally disabled; deploy one endpoint.
docker_deployment:
	docker build --target deployment -f Dockerfile -t hr-policy-deployment .
	docker run --rm -p 8610:8610 -v "$(CURDIR)/data/policies:/app/data/policies" -v "$(CURDIR)/artifacts:/app/artifacts" --env-file .env hr-policy-deployment

clean:
	rm -rf models .rasa __pycache__ actions/__pycache__ scripts/__pycache__
