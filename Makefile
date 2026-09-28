PYTHON := .venv-runtime/bin/python
RASA := .venv-runtime/bin/rasa
include .env
export RASA_TELEMETRY_ENABLED := false
export USE_POLICY_GENERATION ?= 1
export POLICY_GENERATION_MODEL ?= HuggingFaceTB/SmolLM2-360M-Instruct
export MODEL_BACKEND ?= vllm
export MODEL_HOST ?= 127.0.0.1
export MODEL_PORT ?= 9000
export MODEL_BACKEND_PORT ?= 9001
export MODEL_DEVICE ?= cpu

.PHONY: install ingest validate train test actions chat serve
install:
	python3.10 -m venv .venv-runtime
	.venv-runtime/bin/pip install -r requirements.txt
ingest:
	$(PYTHON) -m hr_policy.engine ingest policies/sample
validate:
	$(RASA) data validate
train: validate
	$(RASA) train
test:
	$(PYTHON) -m unittest discover -s tests -v
actions:
	$(RASA) run actions --actions hr_policy.actions
chat:
	$(RASA) shell
serve:
	$(RASA) run --credentials credentials.yml --interface 127.0.0.1 --enable-api

.PHONY: api
api:
	.venv-runtime/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000

.PHONY: model-host model-host-stop
model-host:
	MODEL_DEVICE=$(MODEL_DEVICE) MODEL_HOST=$(MODEL_HOST) MODEL_BACKEND_PORT=$(MODEL_BACKEND_PORT) MODEL_BACKEND=$(MODEL_BACKEND) MODEL_NAME=$(MODEL_NAME) .venv/bin/python scripts/local_model_host.py
model-host-stop:
	pkill -f 'scripts/local_model_host.py' || true

.PHONY: model-adapter
model-adapter:
	MODEL_HOST=$(MODEL_HOST) MODEL_PORT=$(MODEL_PORT) POLICY_GENERATION_URL=http://$(MODEL_HOST):$(MODEL_PORT)/generate .venv-runtime/bin/python scripts/local_model_service.py

.PHONY: local-start local-status local-stop
local-start:
	MODEL_DEVICE=$(MODEL_DEVICE) MODEL_BACKEND=$(MODEL_BACKEND) MODEL_BACKEND_PORT=$(MODEL_BACKEND_PORT) MODEL_HOST=$(MODEL_HOST) MODEL_PORT=$(MODEL_PORT) MODEL_NAME=$(MODEL_NAME) USE_POLICY_GENERATION=$(USE_POLICY_GENERATION) $(PYTHON) scripts/local.py start
local-status:
	MODEL_DEVICE=$(MODEL_DEVICE) MODEL_BACKEND=$(MODEL_BACKEND) MODEL_BACKEND_PORT=$(MODEL_BACKEND_PORT) MODEL_HOST=$(MODEL_HOST) MODEL_PORT=$(MODEL_PORT) MODEL_NAME=$(MODEL_NAME) USE_POLICY_GENERATION=$(USE_POLICY_GENERATION) $(PYTHON) scripts/local.py status
local-stop:
	MODEL_DEVICE=$(MODEL_DEVICE) MODEL_BACKEND=$(MODEL_BACKEND) MODEL_BACKEND_PORT=$(MODEL_BACKEND_PORT) MODEL_HOST=$(MODEL_HOST) MODEL_PORT=$(MODEL_PORT) MODEL_NAME=$(MODEL_NAME) USE_POLICY_GENERATION=$(USE_POLICY_GENERATION) $(PYTHON) scripts/local.py stop

.PHONY: deploy-setup local-restart logs
deploy-setup:
	./scripts/setup.sh
local-restart:
	$(PYTHON) scripts/local.py stop
	$(PYTHON) scripts/local.py start
logs:
	./scripts/logs.sh

.PHONY: sync-intents reindex
sync-intents:
	$(PYTHON) scripts/sync_intents.py
reindex:
	$(PYTHON) -m hr_policy.store policies/pdfs --mode replace --registered-only
