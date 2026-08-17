COMPOSE := docker compose -f compose.yaml
WELCOME_PY := .venv-welcome/bin/python

.PHONY: install build check lint lint-crm lint-welcome typecheck typecheck-crm typecheck-welcome security manifests test test-crm test-welcome test-frontend dev dev-tunnel down logs shell migrate

install:
	python -m pip install -e "services/crm[dev]"
	python -m venv .venv-welcome
	$(WELCOME_PY) -m pip install -e "services/welcome[dev]"
	npm ci

build:
	npm run build
	$(COMPOSE) build crm-web

check:
	cd services/crm && python manage.py check
	cd services/crm && python manage.py makemigrations --check --dry-run

lint: lint-crm lint-welcome

lint-crm:
	cd services/crm && python -m ruff check .

lint-welcome:
	cd services/welcome && ../../$(WELCOME_PY) -m ruff check .

typecheck: typecheck-crm typecheck-welcome

typecheck-crm:
	cd services/crm && python -m mypy apps/welcome_bots/crypto.py apps/crm/calculator.py

typecheck-welcome:
	cd services/welcome && ../../$(WELCOME_PY) -m mypy src

security:
	cd services/crm && python -m bandit -q -r . -x './**/tests,./**/migrations' -lll
	npm audit --audit-level=high

manifests:
	@bash ops/ci/render-kustomize.sh

test: test-crm test-welcome

test-crm:
	cd services/crm && pytest -q

test-welcome:
	cd services/welcome && ../../$(WELCOME_PY) -m pytest -q

test-frontend:
	npm run test:e2e
	npm run test:visual

dev:
	npm run build
	$(COMPOSE) up --build

dev-tunnel:
	npm run build
	$(COMPOSE) --profile tunnel up --build

down:
	$(COMPOSE) --profile tunnel down

logs:
	$(COMPOSE) logs -f crm-web crm-worker crm-beat welcome-api welcome-worker-events

shell:
	$(COMPOSE) exec crm-web bash

migrate:
	$(COMPOSE) run --rm crm-web python manage.py migrate
