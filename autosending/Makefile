# ─────────────────────────────────────────────────────────────────────────────
#  AutoSending — Makefile
#  Usage:  make help
# ─────────────────────────────────────────────────────────────────────────────

# ── Compose commands ──────────────────────────────────────────────────────────
# Compose files live in docker/compose/; relative paths inside them are
# resolved from that directory (per Compose spec: "../../" = project root).
COMPOSE      := docker compose
COMPOSE_DEV  := $(COMPOSE) -f docker/compose/base.yml -f docker/compose/dev.yml
COMPOSE_PROD := $(COMPOSE) -f docker/compose/base.yml -f docker/compose/prod.yml

# ── Colour codes ──────────────────────────────────────────────────────────────
CYAN  := \033[1;36m
GREEN := \033[1;32m
RESET := \033[0m

.PHONY: help \
        dev dev-d dev-build \
        prod prod-build \
        stop stop-dev stop-prod \
        restart restart-dev \
        logs logs-backend logs-frontend logs-nginx \
        ps \
        shell-backend shell-frontend shell-nginx \
        db-init \
        clean clean-images prune

# ── Default target ────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "$(CYAN)AutoSending — available targets$(RESET)"
	@echo ""
	@echo "  $(GREEN)Development$(RESET)"
	@echo "    make dev             Build + start dev stack (foreground, hot-reload)"
	@echo "    make dev-d           Same, but detached (background)"
	@echo "    make dev-build       Rebuild dev images only"
	@echo ""
	@echo "  $(GREEN)Production$(RESET)"
	@echo "    make prod            Build + start prod stack (nginx on :80)"
	@echo "    make prod-build      Rebuild prod images only"
	@echo ""
	@echo "  $(GREEN)Control$(RESET)"
	@echo "    make stop            Stop whichever stack is running"
	@echo "    make stop-dev        Stop dev stack"
	@echo "    make stop-prod       Stop prod stack"
	@echo "    make restart         Restart prod stack"
	@echo "    make restart-dev     Restart dev stack"
	@echo "    make ps              Show container status"
	@echo ""
	@echo "  $(GREEN)Logs$(RESET)"
	@echo "    make logs            Follow all container logs"
	@echo "    make logs-backend    Follow backend only"
	@echo "    make logs-frontend   Follow frontend only"
	@echo "    make logs-nginx      Follow nginx only (prod)"
	@echo ""
	@echo "  $(GREEN)Shell$(RESET)"
	@echo "    make shell-backend   bash inside backend container"
	@echo "    make shell-frontend  sh inside frontend container"
	@echo "    make shell-nginx     sh inside nginx container (prod)"
	@echo ""
	@echo "  $(GREEN)Database$(RESET)"
	@echo "    make db-init         Create / migrate SQLite schema"
	@echo ""
	@echo "  $(GREEN)Cleanup$(RESET)"
	@echo "    make clean           Stop containers + remove volumes"
	@echo "    make clean-images    Remove autosending Docker images"
	@echo "    make prune           Full Docker system prune (careful!)"
	@echo ""

# ── Development ───────────────────────────────────────────────────────────────
dev:
	$(COMPOSE_DEV) up --build -d
	@echo "$(CYAN)Dev stack running:$(RESET)"
	@echo "  Frontend: http://localhost:3000"
	@echo "  Backend:  http://localhost:8000"
	@echo "  API docs: http://localhost:8000/docs"
	@echo ""
	@echo "  Logs: make logs"

dev-logs:
	$(COMPOSE_DEV) up --build

dev-build:
	$(COMPOSE_DEV) build

# ── Production ────────────────────────────────────────────────────────────────
prod:
	$(COMPOSE_PROD) up --build -d
	@echo "$(CYAN)Prod stack running:$(RESET)"
	@echo "  App:      http://localhost"
	@echo "  API docs: http://localhost/docs"

prod-build:
	$(COMPOSE_PROD) build

# ── Control ───────────────────────────────────────────────────────────────────
stop:
	$(COMPOSE_PROD) down 2>/dev/null || true
	$(COMPOSE_DEV)  down 2>/dev/null || true

stop-dev:
	$(COMPOSE_DEV) down

stop-prod:
	$(COMPOSE_PROD) down

restart:
	$(COMPOSE_PROD) restart

restart-dev:
	$(COMPOSE_DEV) restart

ps:
	$(COMPOSE_PROD) ps 2>/dev/null || $(COMPOSE_DEV) ps

# ── Logs ──────────────────────────────────────────────────────────────────────
logs:
	$(COMPOSE_PROD) logs -f 2>/dev/null || $(COMPOSE_DEV) logs -f

logs-backend:
	$(COMPOSE_PROD) logs -f backend 2>/dev/null || $(COMPOSE_DEV) logs -f backend

logs-frontend:
	$(COMPOSE_PROD) logs -f frontend 2>/dev/null || $(COMPOSE_DEV) logs -f frontend

logs-nginx:
	$(COMPOSE_PROD) logs -f nginx

# ── Shell ─────────────────────────────────────────────────────────────────────
shell-backend:
	$(COMPOSE_PROD) exec backend /bin/bash 2>/dev/null || $(COMPOSE_DEV) exec backend /bin/bash

shell-frontend:
	$(COMPOSE_PROD) exec frontend /bin/sh 2>/dev/null || $(COMPOSE_DEV) exec frontend /bin/sh

shell-nginx:
	$(COMPOSE_PROD) exec nginx /bin/sh

# ── Database ──────────────────────────────────────────────────────────────────
db-init:
	$(COMPOSE_PROD) exec backend python3 -c \
	  "from database import init_db; init_db(); print('✓ DB schema up to date')" 2>/dev/null || \
	$(COMPOSE_DEV) exec backend python3 -c \
	  "from database import init_db; init_db(); print('✓ DB schema up to date')"

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	$(COMPOSE_PROD) down -v --remove-orphans 2>/dev/null || true
	$(COMPOSE_DEV)  down -v --remove-orphans 2>/dev/null || true

clean-images:
	docker images --filter "reference=autosending*" -q | xargs -r docker rmi -f

prune:
	@echo "$(CYAN)WARNING: this removes ALL unused Docker data system-wide$(RESET)"
	@read -p "Continue? [y/N] " c && [ "$$c" = "y" ]
	docker system prune -af --volumes
