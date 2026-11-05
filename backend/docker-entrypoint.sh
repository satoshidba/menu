#!/bin/sh
set -e
mkdir -p "$LARDER_DATA_DIR/media" "$LARDER_DATA_DIR/receipts" "$LARDER_DATA_DIR/backups"
case "$1" in
  web)
    # Migrations run before the API starts; they are transactional and safe to re-run.
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --workers "${LARDER_WEB_WORKERS:-2}"
    ;;
  worker)
    exec python -m app.worker
    ;;
  cli)
    shift
    exec python -m app.cli "$@"
    ;;
  test)
    shift
    exec python -m pytest "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
