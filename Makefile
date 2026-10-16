# Thin wrapper: every target delegates to scripts/dev.py, which works the same on
# Linux, macOS and Windows. No make? Run `python scripts/dev.py <target>` directly.
PYTHON ?= python3
ifeq ($(OS),Windows_NT)
PYTHON := python
endif
IMAGE ?= blindspot:local

.PHONY: setup demo test lint scan audit check clean docker-build docker-run docker-scan

setup:
	$(PYTHON) scripts/dev.py setup

demo:
	$(PYTHON) scripts/dev.py demo

test:
	$(PYTHON) scripts/dev.py test

lint:
	$(PYTHON) scripts/dev.py lint

scan:
	$(PYTHON) scripts/dev.py scan

audit:
	$(PYTHON) scripts/dev.py audit

check:
	$(PYTHON) scripts/dev.py check

clean:
	$(PYTHON) scripts/dev.py clean

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker compose up --build

docker-scan: docker-build
	$(PYTHON) scripts/scan_secrets.py --docker $(IMAGE)
