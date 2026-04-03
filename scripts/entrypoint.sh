#!/bin/bash
set -e

echo "==> Waiting for postgres..."
until python -c "import psycopg2; psycopg2.connect('${DATABASE_URL}')" 2>/dev/null; do
    sleep 1
done
echo "==> Postgres is ready."

# Only the web service runs migrations and collectstatic.
# Celery workers must NOT run migrations — parallel migrate causes race
# conditions when multiple containers try to create the same tables.
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "==> Running migrations..."
    python manage.py migrate --noinput

    echo "==> Collecting static files..."
    python manage.py collectstatic --noinput --clear
fi

echo "==> Starting: $@"
exec "$@"
