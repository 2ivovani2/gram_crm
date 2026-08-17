COMPOSE := docker compose -f compose.yaml

.PHONY: install build check test test-frontend dev dev-tunnel down logs shell migrate

install:
	python -m pip install -e "services/crm[dev]"
	npm ci

build:
	npm run build
	$(COMPOSE) build crm-web

check:
	cd services/crm && python manage.py check
	cd services/crm && python manage.py makemigrations --check --dry-run

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
