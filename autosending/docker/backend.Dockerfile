# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — builder: install Python deps into an isolated prefix
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Copy requirements first → Docker layer-cached until the file changes
COPY backend/requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — dev: slim image + installed deps; source bind-mounted at runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS dev

WORKDIR /app

COPY --from=builder /install /usr/local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8000

# Source is bind-mounted via docker-compose.dev.yml; --reload picks up changes
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app"]


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — runtime: minimal production image (no pip, no build tools)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /install /usr/local

# Bake the application source into the image
COPY backend/ .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info"]
