# job-hunt — single entry point for all workflows.
# Prereqs: bun, uv, Docker Desktop, claude CLI. TeX optional (docker fallback).

SHELL := /bin/sh
.DEFAULT_GOAL := help

.PHONY: help setup db-up db-down db-logs migrate sync run build run-docker \
        scrape stats queue compile-example test clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install deps, git hooks, submodules; sync framework if script present
	uv sync
	bun install
	git config core.hooksPath scripts/hooks
	git submodule update --init
	@if [ -f scripts/sync-framework.ts ]; then bun run scripts/sync-framework.ts; \
	else echo "setup: scripts/sync-framework.ts not present yet — skipping framework sync"; fi
	@echo "setup: done. Next: cp .env.example .env && make db-up"

db-up: ## Start Postgres + Adminer + MinIO (Docker) and apply migrations
	docker compose up -d --wait db adminer minio
	docker compose up minio-init
	$(MAKE) migrate
	@echo "db-up: Postgres :5436 · Adminer http://localhost:8082 · MinIO console http://localhost:9003"

db-down: ## Stop the Docker stack (data volumes under state/ are kept)
	docker compose down

db-logs: ## Tail database logs
	docker compose logs -f db

migrate: ## Apply pending SQL migrations
	sh scripts/migrate.sh

sync: ## Re-sync framework from submodule + re-apply overlays
	bun run scripts/sync-framework.ts

install-command: ## Install /shek-apply globally (usable from any directory)
	sh scripts/install-global-command.sh

run: ## Run the backend CLI locally (usage: make run ARGS="scrape" / "stats" / "apply <id>")
	@if [ ! -f src/cli.ts ]; then echo "run: src/cli.ts not implemented yet (Phase B/F)"; exit 1; fi
	bun run src/cli.ts $(ARGS)

build: ## Build the backend Docker image
	docker build -t job-hunt-backend .

run-docker: ## Run the backend inside Docker (usage: make run-docker ARGS="stats")
	docker run --rm --env-file .env \
		-e DATABASE_URL=postgres://jobhunt:jobhunt@host.docker.internal:5436/jobhunt \
		-e MINIO_ENDPOINT=http://host.docker.internal:9002 \
		job-hunt-backend $(ARGS)

scrape: ## Run the scrape pipeline headless
	bun run src/cli.ts scrape

stats: ## Applications by state + token usage
	bun run src/cli.ts stats

queue: ## What's awaiting your review
	bun run src/cli.ts queue

compile-example: ## Sanity-compile the master CV (local pdflatex, else texlive docker)
	@if [ ! -f cv/main_example.tex ]; then echo "compile-example: cv/main_example.tex not present yet (run make sync / /setup first)"; exit 1; fi
	@if command -v pdflatex >/dev/null 2>&1; then \
		cd cv && pdflatex -interaction=nonstopmode main_example.tex; \
	else \
		echo "compile-example: no local pdflatex — using texlive docker image"; \
		docker run --rm -v "$$PWD/cv":/work -w /work texlive/texlive:latest-small \
			pdflatex -interaction=nonstopmode main_example.tex; \
	fi

test: ## Run all tests and guards
	uv run pytest
	bun test
	@if [ -f tools/security_guards.py ]; then uv run tools/security_guards.py; \
	else echo "test: tools/security_guards.py not synced yet — skipping"; fi

clean: ## Remove LaTeX build artifacts
	find . -name '*.aux' -o -name '*.log' -o -name '*.out' -o -name '*.synctex.gz' | \
		grep -v packages/ | xargs rm -f 2>/dev/null || true
