# BuyerReach production integrity audit — 2026-07-17

## Release decision

The core search journey is deployable after migration `20260717_0025`: task creation freezes configuration and Vendor choice, execution writes durable versioned stage records, candidate changes pass through the state machine, domain events use a transactional Outbox, and workers recover queued and expired work. PostgreSQL is the system of record.

## Closed release blockers

- Task-critical configuration and `TaskVendorPlan` are frozen at creation; API keys are excluded and only governed credential IDs are retained.
- Runtime Provider and AI model selection resolve from the task snapshot, with compatibility fallback for pre-snapshot tasks.
- Candidate status changes in API/worker/service paths use `transition_candidate`; task lifecycle uses `transition_task`.
- All eight pipeline stage names write versioned `PipelineStageRun` records on real execution paths.
- `emit` writes the event and business mutation in the same transaction. Celery Beat publishes pending events and retains failures for retry.
- Worker startup and the periodic recovery scan restore queued tasks, stale running tasks, queued enrichment, and expired stage leases.
- Versioned task SSE reads ordered events from the database and tolerates additive payload fields.
- Logs/audit/events and task snapshots recursively redact credential-shaped fields.
- Empty database bootstrap, existing-database upgrade, one-version downgrade, and re-upgrade are supported.

## Compatibility and operational boundaries

- Tasks created before snapshot support use the legacy compatibility path and freeze their Vendor Plan on first queue.
- Vendor APIs that support an idempotency header should receive the stage idempotency key in a future adapter version. For Vendors without such a contract, no local database can prove exactly-once behavior if the process dies after the remote side commits but before its response is durably received. Current checkpoints prevent replay after a successful normalized response is committed.
- The frontend currently uses polling for its primary progress UX; the authenticated SSE endpoint is available for the next UI transport switch. Polling remains compatible and database-backed.
- Recompute persistence models exist, but operator-facing batch creation/pause/resume APIs are not enabled in this release. This does not block the existing search workflow; do not advertise recomputation as user-available yet.
- The production bundle has a known JavaScript chunk-size warning. It is a performance optimization item, not a correctness blocker.

## Mandatory gates

| Gate | Result | Evidence |
|---|---|---|
| Product | Pass for existing search journey | Create/start/pause/resume/cancel/retry UI and API retained |
| Architecture | Pass | Pipeline registry, runner, state machine, adapters, Outbox separated |
| Data | Pass | Additive migration; old fields retained; clone and empty-db migration rehearsals |
| Reliability | Pass with documented Vendor boundary | Checkpoints, stage leases, recovery scan, cancellation and budget guards |
| Evolution | Pass | Independent pipeline/prompt/policy/adapter/schema versions and frozen snapshots |
| Operations | Pass | Database truth, durable events, recovery jobs, trace/stage/vendor identifiers |
| Security | Pass for reviewed paths | Permission dependencies and recursive secret redaction |
| Experience | Pass for compatibility; SSE UI switch deferred | Existing polling UX retained; unknown status fallback added |
| Verification | Pass | Full tests, static checks, compile, type check, build, and migration rehearsals |

## Rollout and rollback

1. Back up PostgreSQL.
2. Build the backend and frontend images.
3. Start backend; it runs `alembic upgrade head` before accepting traffic.
4. Recreate both workers and Celery Beat so recovery and Outbox schedules load.
5. Recreate the frontend and verify `/health`, `/ready`, task API, worker and Beat logs.
6. Abort on migration failure, repeated Outbox failures, task retry spikes, or unexpected Vendor spend.

Rollback application images first. If the old backend cannot tolerate additive columns, run `alembic downgrade 20260717_0024`; this removes only the new structures and preserves legacy candidate scoring fields.
