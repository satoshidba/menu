#!/bin/sh
set -e
mkdir -p "$LARDER_DATA_DIR/media" "$LARDER_DATA_DIR/receipts" "$LARDER_DATA_DIR/backups"
for d in media receipts backups; do
  if ! touch "$LARDER_DATA_DIR/$d/.write-test" 2>/dev/null; then
    echo "WARNING: $LARDER_DATA_DIR/$d is not writable by uid $(id -u). If it is a host folder, run: sudo chown -R $(id -u) <folder>" >&2
  else
    rm -f "$LARDER_DATA_DIR/$d/.write-test"
  fi
done
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
