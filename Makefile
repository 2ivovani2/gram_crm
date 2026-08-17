COMPOSE := docker compose -f compose.yaml

.PHONY: install build check lint typecheck security manifests test test-frontend dev dev-tunnel down logs shell migrate

install:
	python -m pip install -e "services/crm[dev]"
	npm ci

build:
	npm run build
	$(COMPOSE) build crm-web

check:
	cd services/crm && python manage.py check
	cd services/crm && python manage.py makemigrations --check --dry-run

lint:
	cd services/crm && python -m ruff check .

typecheck:
	cd services/crm && python -m mypy apps/welcome_bots/crypto.py apps/crm/calculator.py

security:
	cd services/crm && python -m bandit -q -r . -x './**/tests,./**/migrations' -lll
	npm audit --audit-level=high

manifests:
	@bash ops/ci/render-kustomize.sh

test:
	cd services/crm && pytest -q

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
	$(COMPOSE) logs -f crm-web crm-worker crm-beat

shell:
	$(COMPOSE) exec crm-web bash

migrate:
	$(COMPOSE) run --rm crm-web python manage.py migrate
