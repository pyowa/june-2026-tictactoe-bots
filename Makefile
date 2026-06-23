.ONESHELL:
SHELL := /bin/bash
.DEFAULT_GOAL := check

.PHONY: test js-test browser-test coverage lint lint-md lint-k8s \
        format typecheck check \
        seed-examples reset-db mutate acceptance \
        kind-up kind-down build-images kind-load reload-web

# Coverage data sinks. Populated by the test targets, read by `make coverage`.
COVERAGE_DIR := .coverage-data
NODE_COV_FILE := $(COVERAGE_DIR)/node.txt
BROWSER_COV_DIR := $(COVERAGE_DIR)/browser

# ── Python / dev ──────────────────────────────────────────────────────────────

test:
	uv run pytest

js-test:
	mkdir -p $(COVERAGE_DIR)
	set -o pipefail; node --test --experimental-test-coverage tests/js/*.test.mjs 2>&1 \
	  | awk -v out="$(NODE_COV_FILE)" 'BEGIN{cap=0} \
	      /start of coverage report/{cap=1; print > out; next} \
	      /end of coverage report/{print > out; cap=0; next} \
	      cap{print > out; next} \
	      {print}'

browser-test:
	mkdir -p $(BROWSER_COV_DIR)
	rm -f $(BROWSER_COV_DIR)/*.json
	JS_COVERAGE_DIR=$(BROWSER_COV_DIR) uv run pytest tests/browser/ --no-cov -o addopts=""

# Re-display the coverage data collected by the last `make check`. Reads
# from .coverage-data/ and the pytest-cov .coverage file — does NOT re-run
# any tests.
coverage:
	@if [ ! -f .coverage ] && [ ! -f $(NODE_COV_FILE) ] && [ ! -d $(BROWSER_COV_DIR) ]; then \
	  echo "No coverage data found. Run 'make check' first." >&2; exit 1; \
	fi
	@echo "════════════════════════════════════════════════════════════════════════════════"
	@echo "  Python — pytest + coverage.py"
	@echo "════════════════════════════════════════════════════════════════════════════════"
	@if [ -f .coverage ]; then uv run coverage report --show-missing; \
	  else echo "(no python coverage data)"; fi
	@echo
	@echo "════════════════════════════════════════════════════════════════════════════════"
	@echo "  Node — pure reducers (web/static/*-state.mjs)"
	@echo "════════════════════════════════════════════════════════════════════════════════"
	@if [ -f $(NODE_COV_FILE) ]; then cat $(NODE_COV_FILE); \
	  else echo "(no node coverage data)"; fi
	@echo
	@echo "════════════════════════════════════════════════════════════════════════════════"
	@echo "  Browser — DOM adapters (web/static/*.mjs)"
	@echo "════════════════════════════════════════════════════════════════════════════════"
	@if [ -d $(BROWSER_COV_DIR) ] && [ -n "$$(ls -A $(BROWSER_COV_DIR) 2>/dev/null)" ]; then \
	  JS_COVERAGE_DIR=$(BROWSER_COV_DIR) uv run python -m scripts.js_coverage_report; \
	  else echo "(no browser coverage data)"; fi

lint:
	uv run ruff check .

lint-md:
	uv run pymarkdown scan README.md TODO.md CLAUDE.md

lint-k8s:
	@command -v kubeconform >/dev/null 2>&1 \
	  && kubeconform -strict -summary k8s/ \
	  || echo "kubeconform not found — skipping k8s lint (install via: nix develop or brew install kubeconform)"

format:
	uv run ruff format .

typecheck:
	uv run ty check web/*.py db/ entities/ runner/ messaging/ scripts/ tests/

check: lint lint-md lint-k8s typecheck test js-test browser-test

seed-examples:
	uv run python -m scripts.seed_example_bots

reset-db:
	uv run python -m scripts.reset_db

mutate:
	docker-compose --profile mutmut run --rm mutmut uv run mutmut run $(MODULE)

acceptance:
	uv run pytest tests/acceptance/ -o addopts=--no-cov -v

# ── Kubernetes / infrastructure ───────────────────────────────────────────────
# Run these from inside the nix dev shell: nix develop

kind-up: build-images
	set -e
	kind get clusters 2>/dev/null | grep -q '^kind$$' \
	  && echo "cluster already exists" \
	  || kind create cluster --config k8s/kind-cluster.yaml
	kubectl apply -k k8s/
	echo "Waiting for postgres to be ready..."
	kubectl rollout status deployment/postgres -n platform --timeout=180s
	echo "Waiting for rabbitmq to be ready..."
	kubectl rollout status deployment/rabbitmq -n platform --timeout=180s
	$(MAKE) kind-load
	echo "Waiting for app deployments to be ready..."
	kubectl rollout status deployment/web             -n platform --timeout=180s
	kubectl rollout status deployment/match-scheduler -n platform --timeout=180s
	kubectl rollout status deployment/dispatcher      -n bots     --timeout=180s

kind-down:
	kind delete cluster

build-images:
	set -e
	for ver in 3.10 3.11 3.12 3.13 3.14; do
	  docker build -t pyowa/bot-runner-python:$$ver \
	    --build-arg PY_VERSION=$$ver \
	    bot-runner-images/python/
	done
	docker build -t pyowa/dispatcher:latest --target dispatcher .
	docker build -t pyowa/web:latest --target web .
	docker build -t pyowa/match-scheduler:latest --target match-scheduler .

kind-load:
	set -e
	for ver in 3.10 3.11 3.12 3.13 3.14; do
	  kind load docker-image pyowa/bot-runner-python:$$ver
	done
	kind load docker-image pyowa/dispatcher:latest
	kind load docker-image pyowa/web:latest
	kind load docker-image pyowa/match-scheduler:latest

reload-web:
	set -e
	docker build -t pyowa/web:latest --target web .
	kind load docker-image pyowa/web:latest
	kubectl rollout restart deployment/web -n platform
