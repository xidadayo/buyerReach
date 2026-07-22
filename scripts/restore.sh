#!/usr/bin/env sh
set -eu

ARCHIVE=${1:?Usage: CONFIRM_RESTORE=YES BACKUP_PASSPHRASE=... ./scripts/restore.sh backups/buyerreach-*.tar.gz.enc}
BACKUP_PASSPHRASE=${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE before restoring}

if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  echo 'Refusing to restore. Re-run with CONFIRM_RESTORE=YES after verifying the target environment.' >&2
  exit 1
fi

WORK_DIR=$(mktemp -d)
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT INT TERM

case "$ARCHIVE" in
  *.enc)
    openssl enc -d -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_PASSPHRASE -in "$ARCHIVE" -out "$WORK_DIR/backup.tar.gz"
    ;;
  *) cp "$ARCHIVE" "$WORK_DIR/backup.tar.gz" ;;
esac

tar -xzf "$WORK_DIR/backup.tar.gz" -C "$WORK_DIR"
test -s "$WORK_DIR/database.sql"
test -s "$WORK_DIR/minio-data.tar.gz"

docker compose exec -T postgres psql -U buyerreach -d buyerreach -v ON_ERROR_STOP=1 <<'SQL'
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
SQL
docker compose exec -T postgres psql -U buyerreach -d buyerreach -v ON_ERROR_STOP=1 < "$WORK_DIR/database.sql"

MINIO_CONTAINER=$(docker compose ps -q minio)
test -n "$MINIO_CONTAINER"
docker run --rm --volumes-from "$MINIO_CONTAINER" -v "$WORK_DIR:/backup" alpine:3.20 \
  sh -c 'rm -rf /data/* /data/.[!.]*; tar -xzf /backup/minio-data.tar.gz -C /'

docker compose up -d backend celery-worker celery-beat frontend
printf 'Restore completed. Verify /ready and application data before allowing users back in.\n'
