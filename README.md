# BuyerReach 买手通

BuyerReach V1.0 is a self-hosted brand customer discovery and email-pool data platform.

> 开发前请阅读 [统一开发与架构规则](docs/DEVELOPMENT_RULES.md) 和 [贡献指南](CONTRIBUTING.md)。所有编辑器和 AI 工具均以该规则为准。

完整的中文操作说明请见 [使用手册](docs/USER_MANUAL.md)。

V1 scope follows the PRD baseline:

- Search task creation and background execution
- Company, brand, website, contact, email, source evidence, and verification data model
- Provider adapter layer for search, contact discovery, email finding, email verification, and notification
- Email status, scoring, email pools, blacklist, duplicate detection, and manual review foundations
- CSV/XLSX import-export API placeholders
- RBAC-ready identity structure, audit logs, provider configuration, API usage, events
- Docker Compose deployment for Linux/NAS environments

V1 explicitly does not implement bulk cold email sending, email warmup, complete CRM, or LinkedIn login automation.

## Local Development

Docker runs inside WSL on this workstation. Start BuyerReach with its isolated
WSL override so it reuses local base images without changing Docker's global
registry or any other Compose project:

```powershell
wsl.exe sh -lc "cd '/mnt/d/buyer reach' && docker compose -f docker-compose.yml -f docker-compose.wsl.yml --env-file .env.example up -d --no-build --pull never"
```

Use the same command with `--build` only after source or dependency changes.

For Windows, use the quick-entry script instead of manually entering Docker
commands. It checks Docker availability and verifies the frontend after start:

```powershell
# Start the existing stack quickly
.\scripts\buyerreach.ps1

# Build and hot-replace the frontend only after UI changes
.\scripts\buyerreach.ps1 -Action refresh-frontend

# Restart all existing services without rebuilding images
.\scripts\buyerreach.ps1 -Action restart
```

For a regular Linux/NAS host with direct Docker Hub access:

```powershell
docker compose up --build
```

Services:

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- PostgreSQL: localhost:15432
- Redis: localhost:16379
- n8n: http://localhost:15678
- MinIO Console: http://localhost:9001

## Backend Only

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Backup and Restore

From a Linux/NAS host in the project directory, create an encrypted database,
MinIO, and environment backup with:

```sh
BACKUP_PASSPHRASE='use-a-secret-manager-value' ./scripts/backup.sh
```

Restore is intentionally destructive and requires an explicit confirmation:

```sh
CONFIRM_RESTORE=YES BACKUP_PASSPHRASE='use-a-secret-manager-value' ./scripts/restore.sh backups/buyerreach-<timestamp>.tar.gz.enc
```

## Safe Upgrade on Linux/NAS

Deploy production from a clean Git clone and upgrade to an explicit release tag
or commit. The upgrade command validates Compose, creates an encrypted backup,
builds the new application images, applies Alembic migrations, replaces the
application services, and checks readiness:

```sh
BACKUP_PASSPHRASE='use-a-secret-manager-value' \
  ./scripts/upgrade.sh v1.1.0
```

For a NAS production override and a separately stored environment file:

```sh
BACKUP_PASSPHRASE='use-a-secret-manager-value' \
ENV_FILE=/volume1/docker/buyerreach/.env \
COMPOSE_FILES=docker-compose.yml:/volume1/docker/buyerreach/compose.prod.yml \
  ./scripts/upgrade.sh v1.1.0
```

`COMPOSE_FILES` uses `:` as its separator and its file paths must not contain
spaces. The script refuses a dirty Git working tree. If an upgrade fails after
checkout, it rebuilds the previous application commit; it never performs an
automatic database downgrade. Review the migration and encrypted backup before
any destructive database recovery. Append-only JSONL audit records are written
to `./var/audit/upgrades` by default. Set `AUDIT_DIR` to a persistent NAS path
outside the Git checkout for production retention.

## V1 Architecture Rule

Core business data belongs to PostgreSQL. Third-party services must enter through providers. Long-running work must enter the task layer. New modules should communicate through APIs or domain events.
