PYTHON := .venv/bin/python
RASA := .venv/bin/rasa
export RASA_TELEMETRY_ENABLED := false

.PHONY: install ingest validate train test actions chat serve
install:
	python3.10 -m venv .venv
	.venv/bin/pip install -r requirements.txt
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
	$(RASA) run --credentials credentials.yml --interface 127.0.0.1

.PHONY: api
api:
	.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000


.PHONY: local-start local-status local-stop
local-start:
	$(PYTHON) scripts/local.py start
local-status:
	$(PYTHON) scripts/local.py status
local-stop:
	$(PYTHON) scripts/local.py stop

.PHONY: deploy-setup local-restart logs
deploy-setup:
	./scripts/setup.sh
local-restart:
	$(PYTHON) scripts/local.py stop
	$(PYTHON) scripts/local.py start
logs:
	./scripts/logs.sh
