FROM python:3.11-slim AS base

WORKDIR /app


ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- deps layer (cached separately from code) ---
FROM base AS deps
COPY pyproject.toml .
# Install all dependencies (main + dev) without building the project package.
# Python 3.11 stdlib tomllib parses pyproject.toml — no extra tools needed.
# Backslash continuations are consumed by Docker before the shell sees them,
# so the shell receives a single-line python3 -c "..." command.
RUN python3 -c "\
import tomllib, subprocess, sys; \
d = tomllib.load(open('pyproject.toml', 'rb')); \
deps = d['project']['dependencies']; \
dev = d.get('project', {}).get('optional-dependencies', {}).get('dev', []); \
subprocess.run([sys.executable, '-m', 'pip', 'install'] + deps + dev, check=True)"

# --- final ---
FROM deps AS final
COPY . .

RUN chmod +x scripts/entrypoint.sh

EXPOSE 8000

# Healthcheck is defined per-service in docker-compose.yml (only web gets one).
# Celery containers don't run uvicorn, so a global HTTP healthcheck is wrong.

ENTRYPOINT ["bash", "scripts/entrypoint.sh"]
