#!/bin/sh
set -e

echo "[HawkOffer] Starting container entrypoint..."

# 1. Run database migrations if configured
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "[HawkOffer] Applying database migrations with Alembic..."
    alembic upgrade head
    echo "[HawkOffer] Database migrations completed."
else
    echo "[HawkOffer] RUN_MIGRATIONS is false, skipping migration step."
fi

# 2. Determine host, port, and worker configuration
HOST="${HOST:-${HAWKOFFER_API_HOST:-0.0.0.0}}"
PORT="${PORT:-${HAWKOFFER_API_PORT:-8000}}"
WORKERS="${WORKERS:-1}"

echo "[HawkOffer] Launching Uvicorn ASGI server on ${HOST}:${PORT} (workers: ${WORKERS})..."

# 3. Exec replaces shell process so SIGTERM/SIGINT signals propagate directly to Uvicorn for graceful shutdown
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
