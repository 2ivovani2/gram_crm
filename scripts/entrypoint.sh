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

    # Validate S3 connectivity before starting the server.
    # Exits with code 1 if the bucket is unreachable — gives a clear error
    # instead of silently starting and failing on the first file upload.
    echo "==> Checking S3 storage connectivity..."
    python - <<'PYEOF'
import sys
try:
    from django.core.files.storage import default_storage
    default_storage.exists("__healthcheck__")
    print("    S3 storage OK.")
except Exception as e:
    print(f"    [ERROR] S3 storage unreachable: {e}", file=sys.stderr)
    print("    Check AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME, AWS_S3_ENDPOINT_URL in .env", file=sys.stderr)
    sys.exit(1)
PYEOF
fi

echo "==> Starting: $@"
exec "$@"
