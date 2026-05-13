.PHONY: help install dev test lint clean run-example down

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
COLLECTOR_PORT ?= 8080
EXPORT_PYTHONPATH := PYTHONPATH=services/collector:sdk/python

help:
	@echo "Targets:"
	@echo "  install     Create venv and install dev dependencies"
	@echo "  test        Run pytest"
	@echo "  dev         Start collector with SQLite (CTRL+C to stop)"
	@echo "  run-example Run AML example agent against local collector"
	@echo "  clean       Remove dev artifacts"

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install fastapi 'pydantic>=2,<3' 'uvicorn[standard]' httpx pytest pytest-asyncio 'sqlalchemy>=2,<3' pyyaml

test:
	$(EXPORT_PYTHONPATH) $(PYTHON) -m pytest tests/

dev:
	$(EXPORT_PYTHONPATH) \
		CONTROLDB_DATABASE_URL=sqlite:///./controldb_dev.sqlite \
		CONTROLDB_OBJECT_STORE_DIR=./controldb_artifacts \
		CONTROLDB_POLICIES_DIR=./deploy/policies \
		CONTROLDB_BOOTSTRAP_API_KEYS=dev-key \
		$(PYTHON) -m controldb_collector

run-example:
	$(EXPORT_PYTHONPATH) CONTROLDB_API_KEY=dev-key CONTROLDB_URL=http://localhost:$(COLLECTOR_PORT) \
		$(PYTHON) examples/aml-agent/run.py

down:
	rm -rf controldb_dev.sqlite controldb_artifacts

clean: down
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
