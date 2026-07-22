#!/usr/bin/env sh
set -eu

BACKUP_DIR=${BACKUP_DIR:-./backups}
BACKUP_PASSPHRASE=${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE before running a backup}
ENV_FILE=${ENV_FILE:-.env}
COMPOSE_FILES=${COMPOSE_FILES:-docker-compose.yml}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
WORK_DIR=$(mktemp -d)
ARCHIVE="$BACKUP_DIR/buyerreach-$STAMP.tar.gz"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT INT TERM

mkdir -p "$BACKUP_DIR"
compose() {
  COMPOSE_FILE="$COMPOSE_FILES" APP_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" "$@"
}

compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$WORK_DIR/database.sql"
test -s "$WORK_DIR/database.sql"

MINIO_CONTAINER=$(compose ps -q minio)
test -n "$MINIO_CONTAINER"
docker run --rm --volumes-from "$MINIO_CONTAINER" -v "$WORK_DIR:/backup" alpine:3.20 \
  sh -c 'tar -czf /backup/minio-data.tar.gz -C / data'
test -s "$WORK_DIR/minio-data.tar.gz"

if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$WORK_DIR/environment.env"
else
  cp .env.example "$WORK_DIR/environment.env"
fi

cat > "$WORK_DIR/manifest.txt" <<EOF
created_at=$STAMP
database=postgresql
object_storage=minio
EOF

tar -czf "$ARCHIVE" -C "$WORK_DIR" database.sql minio-data.tar.gz environment.env manifest.txt
openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_PASSPHRASE -in "$ARCHIVE" -out "$ARCHIVE.enc"
rm "$ARCHIVE"
if [ -n "${BACKUP_OUTPUT_FILE:-}" ]; then
  printf '%s\n' "$ARCHIVE.enc" > "$BACKUP_OUTPUT_FILE"
fi
printf 'Encrypted backup created: %s\n' "$ARCHIVE.enc"
