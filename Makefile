.PHONY: help install env dev db proxy proxy-down analysis-image migrate lint test run-fixture run-cartography run-mapping run-incident repository-map evals evals-cartography evals-incident clean deploy-check run-poller cron run-errors capture-datadog capture-errors
ANALYSIS_IMAGE ?= triage-analysis:dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Sync the virtualenv
	uv sync --all-extras

env: ## Create .env from the example if it does not exist
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

dev: install env db migrate ## Full local setup: deps, .env, Postgres, migrations

db: ## Start Postgres and wait for it
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U triage >/dev/null 2>&1; do sleep 1; done
	@echo "postgres ready"

proxy: env ## Start the local LiteLLM proxy (tier aliases + the daily cap)
	docker compose up -d litellm
	@until curl -fsS http://localhost:4000/health/liveliness >/dev/null 2>&1; do sleep 1; done
	@echo "litellm ready on http://localhost:4000 — aliases: triage analysis diagnosis"

proxy-down: ## Stop the local LiteLLM proxy
	docker compose stop litellm

analysis-image: ## Build the image one analysis runs in (docker/analysis/Dockerfile)
	docker build -f docker/analysis/Dockerfile -t $(ANALYSIS_IMAGE) .

migrate: ## Apply Alembic migrations
	uv run alembic upgrade head

lint: ## ruff + mypy
	uv run ruff check src tests evals scripts
	uv run ruff format --check src tests evals scripts
	uv run mypy

test: ## Run the test suite (no network, no spend)
	uv run pytest -q

deploy-check: ## Validate the cluster manifests offline (needs kubeconform)
	kubeconform -strict -summary -kubernetes-version 1.31.0 deploy/*.yaml

run-fixture: env ## Run the ticket pipeline on a fixture diagnosis in dry-run mode
	uv run python -m scripts.run_fixture $(FIXTURE)

run-cartography: env ## Run the cartography graph over config.yaml in dry-run mode
	uv run python -m scripts.run_cartography $(REPOS) $(if $(LOCAL),--local)

run-mapping: env ## Derive the service map from real Datadog events and print the report (read-only)
	uv run python -m scripts.run_mapping $(ARGS)

run-poller: env ## Tick the alert poller by hand, as the Platform cron would (read-only Datadog)
	uv run python -m scripts.run_poller $(ARGS)

run-errors: env ## Tick the code-exception poller by hand (read-only; ARGS="--analyse" spends)
	uv run python -m scripts.run_errors $(ARGS)

cron: env ## Show, or with ARGS="--apply" create, every Platform cron under deploy/platform
	uv run python -m scripts.apply_cron $(ARGS)

run-incident: env ## Run F1 end to end on one real alert: read-only Datadog, real models, fake Jira/Slack
	uv run python -m scripts.run_incident $(ARGS)

repository-map: ## Regenerate config/repository-map.yaml from the architecture document
	uv run python -m scripts.generate_repository_map

capture-datadog: ## Capture a real alert's telemetry as fixtures (read-only, needs a Datadog key)
	uv run python -m scripts.capture_datadog $(ARGS)

capture-errors: ## Capture one hour of the org's Error Tracking issues as fixtures (read-only)
	uv run python -m scripts.capture_errors $(ARGS)

evals: ## Score the fixture suite against the real models (spends money)
	uv run python -m evals.run

evals-cartography: ## Score F0 summaries against real public repos (network + spends money)
	uv run python -m evals.cartography

evals-incident: ## Score F1 classification and qualification on the captured alert (spends money)
	uv run python -m evals.incident

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache
