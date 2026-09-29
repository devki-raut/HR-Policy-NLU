SHELL := /bin/bash

PYTHON := .venv-runtime/bin/python
PIP := .venv-runtime/bin/pip
RASA := .venv-runtime/bin/rasa

-include .env

export RASA_TELEMETRY_ENABLED := false
export FAQ_EMBEDDING_MODEL ?= sentence-transformers/all-MiniLM-L6-v2
export FAQ_EMBEDDING_THRESHOLD ?= 0.75

.PHONY: setup install sync-intents validate train test actions chat serve start run_deployment docker_deployment clean

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

run_deployment:
	$(PYTHON) scripts/run_deployment.py web

# docker_web and docker_bot are intentionally disabled; deploy one endpoint.
docker_deployment:
	docker build --target deployment -f Dockerfile -t hr-policy-deployment .
	docker run --rm -p 8610:8610 -v "$(CURDIR)/data/policies:/app/data/policies" --env-file .env hr-policy-deployment

clean:
	rm -rf models .rasa __pycache__ actions/__pycache__ scripts/__pycache__
