FROM python:3.11-slim AS base

WORKDIR /app


ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

FROM node:22-alpine AS frontend-builder

WORKDIR /frontend
COPY package.json package-lock.json vite.config.js ./
RUN npm ci
COPY frontend ./frontend
RUN npm run build

FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
ARG INSTALL_DEV_DEPENDENCIES=false
RUN python3 -c "\
import os, tomllib, subprocess, sys; \
d = tomllib.load(open('pyproject.toml', 'rb')); \
deps = d['project']['dependencies']; \
dev = d.get('project', {}).get('optional-dependencies', {}).get('dev', []) if os.environ.get('INSTALL_DEV_DEPENDENCIES') == 'true' else []; \
subprocess.run([sys.executable, '-m', 'pip', 'install', '--prefix=/install'] + deps + dev, check=True)"

# The production layer contains only runtime libraries and main Python
# dependencies. Compilers, headers, pip caches, and test tooling stay behind in
# the builder stage. Local/CI test images can opt in with
# --build-arg INSTALL_DEV_DEPENDENCIES=true.
FROM base AS final

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY . .
COPY --from=frontend-builder /frontend/static/dist ./static/dist

RUN chmod +x scripts/entrypoint.sh

EXPOSE 8000

# Healthcheck is defined per-service in docker-compose.yml (only web gets one).
# Celery containers don't run uvicorn, so a global HTTP healthcheck is wrong.

ENTRYPOINT ["bash", "scripts/entrypoint.sh"]
