#!/usr/bin/env sh
set -eu

usage() {
  cat <<'EOF'
Usage: BACKUP_PASSPHRASE=... ./scripts/upgrade.sh <git-tag-or-commit>

Environment:
  ENV_FILE=.env                  Compose environment file (default: .env)
  COMPOSE_FILES=docker-compose.yml[:compose.prod.yml]
                                 Compose files, separated with ':'
  SKIP_FETCH=1                   Do not fetch tags/remotes before resolving target
  HEALTH_RETRIES=30              Number of readiness attempts (default: 30)
  HEALTH_INTERVAL=2              Seconds between readiness attempts (default: 2)
  AUDIT_DIR=./var/audit/upgrades Persistent upgrade audit directory

The script requires a clean Git checkout. It creates an encrypted backup,
builds the requested revision, runs Alembic, replaces the application services,
and verifies readiness. On failure it checks out and rebuilds the previous
application revision. Database downgrade is intentionally never automatic.
EOF
}

TARGET=${1:-}
if [ -z "$TARGET" ] || [ "$TARGET" = "-h" ] || [ "$TARGET" = "--help" ]; then
  usage
  [ -n "$TARGET" ] && exit 0
  exit 2
fi

BACKUP_PASSPHRASE=${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE before upgrading}
ENV_FILE=${ENV_FILE:-.env}
COMPOSE_FILES=${COMPOSE_FILES:-docker-compose.yml}
HEALTH_RETRIES=${HEALTH_RETRIES:-30}
HEALTH_INTERVAL=${HEALTH_INTERVAL:-2}
AUDIT_DIR=${AUDIT_DIR:-./var/audit/upgrades}

case "$HEALTH_RETRIES" in *[!0-9]*|'') echo 'HEALTH_RETRIES must be a positive integer.' >&2; exit 2;; esac
case "$HEALTH_INTERVAL" in *[!0-9]*|'') echo 'HEALTH_INTERVAL must be a positive integer.' >&2; exit 2;; esac
[ "$HEALTH_RETRIES" -gt 0 ] || { echo 'HEALTH_RETRIES must be greater than zero.' >&2; exit 2; }
[ "$HEALTH_INTERVAL" -gt 0 ] || { echo 'HEALTH_INTERVAL must be greater than zero.' >&2; exit 2; }

command -v git >/dev/null 2>&1 || { echo 'git is required.' >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo 'docker is required.' >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo 'openssl is required for encrypted backups.' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required.' >&2; exit 1; }

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo 'Run this script from a Git clone of BuyerReach.' >&2
  exit 1
}
cd "$ROOT"

[ -f "$ENV_FILE" ] || {
  echo "Production environment file not found: $ENV_FILE" >&2
  exit 1
}

if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  echo 'Refusing to upgrade a dirty working tree. Commit, stash, or remove local changes first.' >&2
  exit 1
fi

if [ "${SKIP_FETCH:-0}" != "1" ]; then
  echo 'Fetching release metadata...'
  git fetch --tags --prune
fi

TARGET_COMMIT=$(git rev-parse --verify "$TARGET^{commit}" 2>/dev/null) || {
  echo "Unknown Git tag or commit: $TARGET" >&2
  exit 1
}
PREVIOUS_COMMIT=$(git rev-parse HEAD)

if [ "$TARGET_COMMIT" = "$PREVIOUS_COMMIT" ]; then
  echo "Already running requested source revision: $TARGET_COMMIT"
  exit 0
fi

compose() {
  COMPOSE_FILE="$COMPOSE_FILES" APP_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" "$@"
}

# Compose paths containing spaces are not supported by the portable argument
# builder above. The repository path itself may contain spaces.
case "$COMPOSE_FILES" in *' '*) echo 'COMPOSE_FILES paths must not contain spaces.' >&2; exit 2;; esac

export COMPOSE_FILE="$COMPOSE_FILES"

mkdir -p "$AUDIT_DIR"
AUDIT_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
AUDIT_FILE="$AUDIT_DIR/upgrade-$AUDIT_STAMP-$$.jsonl"
BACKUP_OUTPUT_FILE=$(mktemp)
MIGRATION_BEFORE=unknown
MIGRATION_AFTER=unknown
BACKUP_ARCHIVE=unknown

audit_event() {
  event=$1
  detail=$2
  event_json=$(printf '%s' "$event" | sed 's/\\/\\\\/g; s/"/\\"/g')
  target_json=$(printf '%s' "$TARGET" | sed 's/\\/\\\\/g; s/"/\\"/g')
  detail_json=$(printf '%s' "$detail" | sed 's/\\/\\\\/g; s/"/\\"/g')
  printf '{"timestamp":"%s","event":"%s","target":"%s","previous_commit":"%s","target_commit":"%s","detail":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$event_json" "$target_json" "$PREVIOUS_COMMIT" "$TARGET_COMMIT" "$detail_json" \
    >> "$AUDIT_FILE"
}

cleanup_upgrade_files() {
  rm -f "$BACKUP_OUTPUT_FILE"
}

audit_event started "compose_files=$COMPOSE_FILES;env_file=$ENV_FILE"

audit_early_failure() {
  status=$?
  trap - EXIT INT TERM
  if [ "$status" -ne 0 ]; then
    audit_event upgrade_failed "phase=preflight_or_backup;exit_status=$status"
  fi
  cleanup_upgrade_files
  exit "$status"
}
trap audit_early_failure EXIT INT TERM

echo 'Validating the current deployment...'
compose config --quiet
compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
MIGRATION_BEFORE=$(compose run --rm backend alembic current 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]*$//')
audit_event preflight_passed "migration_before=$MIGRATION_BEFORE"

echo "Creating encrypted pre-upgrade backup for $PREVIOUS_COMMIT..."
BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE \
BACKUP_OUTPUT_FILE=$BACKUP_OUTPUT_FILE \
ENV_FILE=$ENV_FILE \
COMPOSE_FILES=$COMPOSE_FILES \
  sh ./scripts/backup.sh
BACKUP_ARCHIVE=$(cat "$BACKUP_OUTPUT_FILE")
audit_event backup_created "archive=$BACKUP_ARCHIVE"
trap - EXIT INT TERM

checked_out=0
migration_attempted=0
upgrade_complete=0

rollback_application() {
  status=$?
  trap - EXIT INT TERM
  if [ "$upgrade_complete" -eq 1 ]; then
    exit "$status"
  fi
  if [ "$checked_out" -eq 1 ]; then
    echo "Upgrade failed; rebuilding previous application revision $PREVIOUS_COMMIT..." >&2
    if git checkout --detach "$PREVIOUS_COMMIT" && \
       compose build backend frontend email-verifier && \
       compose up -d backend celery-worker celery-enrichment-worker celery-beat email-verifier frontend; then
      echo 'Previous application revision restored.' >&2
    else
      echo 'Automatic application rollback failed. Manual recovery is required.' >&2
      audit_event rollback_failed "exit_status=$status;migration_attempted=$migration_attempted"
    fi
  fi
  if [ "$migration_attempted" -eq 1 ]; then
    echo 'Database downgrade was not attempted. Review the migration and the encrypted backup before any database restore.' >&2
  fi
  audit_event upgrade_failed "exit_status=$status;migration_before=$MIGRATION_BEFORE;backup=$BACKUP_ARCHIVE"
  cleanup_upgrade_files
  exit "$status"
}
trap rollback_application EXIT INT TERM

echo "Checking out $TARGET at $TARGET_COMMIT..."
git checkout --detach "$TARGET_COMMIT"
checked_out=1

compose config --quiet

echo 'Building application images...'
compose build backend frontend email-verifier

echo 'Applying database migrations...'
migration_attempted=1
compose run --rm backend alembic upgrade head

echo 'Replacing application services...'
compose up -d backend celery-worker celery-enrichment-worker celery-beat email-verifier frontend

echo 'Waiting for backend readiness...'
attempt=1
while [ "$attempt" -le "$HEALTH_RETRIES" ]; do
  if compose exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).read()" \
    >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq "$HEALTH_RETRIES" ]; then
    echo 'Backend did not become ready before the health-check deadline.' >&2
    exit 1
  fi
  sleep "$HEALTH_INTERVAL"
  attempt=$((attempt + 1))
done

for service in backend celery-worker celery-enrichment-worker celery-beat email-verifier frontend; do
  container_id=$(compose ps -q "$service")
  [ -n "$container_id" ] || {
    echo "Service has no container after upgrade: $service" >&2
    exit 1
  }
  running=$(docker inspect -f '{{.State.Running}}' "$container_id")
  [ "$running" = 'true' ] || {
    echo "Service is not running after upgrade: $service" >&2
    exit 1
  }
done

MIGRATION_AFTER=$(compose exec -T backend alembic current | tr '\n' ' ' | sed 's/[[:space:]]*$//')
audit_event upgrade_succeeded "migration_before=$MIGRATION_BEFORE;migration_after=$MIGRATION_AFTER;backup=$BACKUP_ARCHIVE"
upgrade_complete=1
trap - EXIT INT TERM
cleanup_upgrade_files

echo "BuyerReach upgrade completed: $PREVIOUS_COMMIT -> $TARGET_COMMIT"
echo "Upgrade audit: $AUDIT_FILE"
echo 'Verify one real search task and review worker, Beat, Outbox, and Vendor-spend signals before declaring the rollout complete.'
