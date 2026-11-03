#!/bin/sh
set -e

echo "[HawkOffer] Starting container entrypoint..."

# If command starts with an option or is empty or is uvicorn, execute standard service startup
if [ "$#" -eq 0 ] || [ "${1#-}" != "$1" ] || [ "$1" = "uvicorn" ]; then
    # 1. Wait for database readiness before executing migrations
    echo "[HawkOffer] Checking database connectivity..."
    python -m app.database.wait_for_db

    # 2. Run database migrations if configured
    if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
        echo "[HawkOffer] Applying database migrations with Alembic..."
        alembic upgrade head
        echo "[HawkOffer] Database migrations completed."
    else
        echo "[HawkOffer] RUN_MIGRATIONS is not 'true', skipping migration step."
    fi

    # 3. Determine host, port, and worker configuration
    HOST="${HOST:-${HAWKOFFER_API_HOST:-0.0.0.0}}"
    PORT="${PORT:-${HAWKOFFER_API_PORT:-8000}}"
    WORKERS="${WORKERS:-1}"

    echo "[HawkOffer] Launching Uvicorn ASGI server on ${HOST}:${PORT} (workers: ${WORKERS})..."
    exec uvicorn app.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
fi

# If arbitrary command was provided (e.g. alembic, python, pytest, sh), execute it directly
exec "$@"
