PYTHON := .venv-runtime/bin/python
PIP := .venv-runtime/bin/pip
RASA := .venv-runtime/bin/rasa

-include .env

export RASA_TELEMETRY_ENABLED := false
export FAQ_EMBEDDING_MODEL ?= sentence-transformers/all-MiniLM-L6-v2
export FAQ_EMBEDDING_THRESHOLD ?= 0.75

.PHONY: setup install sync-intents validate train test actions chat serve clean

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
	$(RASA) run --enable-api

clean:
	rm -rf models .rasa __pycache__ actions/__pycache__ scripts/__pycache__
