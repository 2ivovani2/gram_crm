FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup --system --gid 10001 gramly \
    && adduser --system --uid 10001 --ingroup gramly --home /app gramly

WORKDIR /app
COPY services/welcome/pyproject.toml ./
COPY services/welcome/src ./src
COPY services/welcome/alembic.ini ./
COPY services/welcome/alembic ./alembic
RUN python -m pip install --upgrade pip \
    && python -m pip install .

USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "gramly_welcome.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
