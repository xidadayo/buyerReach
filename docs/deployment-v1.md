# BuyerReach V1 Deployment

## Portable local email verifier

The `email-verifier` service is built entirely by Docker from pinned Go and
AfterShip versions. The host does not need Go installed. The same service runs
on Linux Docker and Windows through WSL2 with `docker-compose.wsl.yml`.

Set a strong `VERIFIER_TOKEN` and save the same value for the “本地邮箱验证”
Vendor in System Settings. Configure outbound TCP 25 or `SMTP_PROXY_URL`. Never
reuse a campaign-sending IP.

```bash
docker compose up -d --build email-verifier
docker compose ps email-verifier

# WSL2
docker compose -f docker-compose.yml -f docker-compose.wsl.yml up -d --build email-verifier
```

The service has no local volume. Redis caches are disposable; durable history
remains in PostgreSQL and is covered by the existing backup/restore flow. Moving
hosts requires only the repository, environment settings and PostgreSQL backup.

Rollout: `disabled` -> `shadow` -> `active` at 10%, 30%, 60%, 100%. Disabling
the local Vendor restores the original ZeroBounce/Hunter path for new tasks.

## Prerequisites

- Linux server or company NAS that supports Docker and Docker Compose
- Ports available: `5173`, `8000`, `5432`, `6379`, `9000`, `9001`, `5678`
- Production environment variables copied from `.env.example` and changed before launch

## Start

```bash
docker compose up --build -d
```

## Health Checks

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/api/v1/health
```

## Migration

```bash
docker compose run --rm backend alembic upgrade head
```

Before production rollout, generate a concrete DDL migration from the SQLAlchemy models:

```bash
docker compose run --rm backend alembic revision --autogenerate -m "freeze v1 schema"
```

## Backup

PostgreSQL:

```bash
docker compose exec postgres pg_dump -U buyerreach buyerreach > backups/buyerreach.sql
```

MinIO files should be backed up from the `minio-data` volume or via an S3-compatible backup job.

## Version upgrade

Use a clean Git clone and select an explicit release tag or commit:

```bash
BACKUP_PASSPHRASE='read-from-a-password-manager' sh ./scripts/upgrade.sh v1.1.0
```

The script checks the current database, creates an encrypted PostgreSQL/MinIO
backup, builds the application images, runs `alembic upgrade head`, recreates
the API, workers, Beat, verifier, and frontend, and verifies `/ready` plus all
required containers. A failed rollout rebuilds the previous application commit.
Database downgrade is deliberately manual because a migration may have already
committed data changes.

Use `ENV_FILE` and `COMPOSE_FILES` when the NAS keeps production configuration
outside the repository. `COMPOSE_FILES` is colon-separated and its paths cannot
contain spaces:

```bash
BACKUP_PASSPHRASE='read-from-a-password-manager' \
ENV_FILE=/volume1/docker/buyerreach/.env \
COMPOSE_FILES=docker-compose.yml:/volume1/docker/buyerreach/compose.prod.yml \
  sh ./scripts/upgrade.sh v1.1.0
```

Set `AUDIT_DIR` to persistent NAS storage. Each attempt writes an append-only
JSONL record containing the requested version, old/new commits, migration
revisions, encrypted backup path, outcome, and rollback failure if applicable.
Secrets and environment values are excluded. Retain these records with the
release notes and off-device backups.
