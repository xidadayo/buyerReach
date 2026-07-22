# Query Slicing Implementation Status

- plan_version: 1.0
- current_phase: R3 (Phase B completion)
- overall_status: in_progress
- last_updated_at: 2026-07-21T16:00:00+08:00
- last_updated_by: Claude Code

## R0 — COMPLETE

- P0-SEC-01: Unified `require_task_access()` applied to all task-scoped endpoints
- Status corrected: Phase A/B/C demoted per audit
- 5 security tests: cross-org 404, same-org success, nonexistent 404, legacy no-org
- Files: `app/core/deps.py`, `app/api/v1/router.py`, `tests/test_query_slicing_security.py`

## R1 — COMPLETE

- R1.1: Organization isolation for all query plan/slice/run endpoints
- R1.2: Single-transaction lock: optimistic concurrency, slice validation, config snapshot, TaskVendorPlan freeze, Outbox → all or nothing
- R1.3: Version lifecycle: `max(version)+1`, supersede old locked plan, String(64) explicit FK types
- R1.4: 14 service tests (lock success, no slices, no enabled, duplicate hash, >20 cap, optimistic concurrency, config snapshot, idempotent, rollback, version lifecycle, legacy, locked edit, duplicate add, locked add)
- Files: `app/query_planning/service.py`, `app/modules/models.py`, `app/api/v1/router.py`, `tests/test_query_plan_service.py`

## R2 — COMPLETE

- P0-RUN-01 fixed: Slice path no longer fakes ProviderConfig; uses real single-provider execution
- P0-RUN-02 fixed: `ConfiguredProviderDiscoveryAdapter.execute()` targets SPECIFIC provider, not waterfall; uses caller's db session (no independent SessionLocal)
- P0-RUN-03 fixed: `lease_expires_at` now `datetime.now(UTC) + timedelta(minutes=5)` (was timestamp int)
- Pagination: `execute_slice_page` reads cursor from existing run, not hardcoded `{"page":1}`
- Stop conditions: qualified count, provider_call_count, cancellation checked before each slice
- Files: `app/providers/discovery.py`, `app/query_planning/scheduler.py`, `app/modules/services.py`

## Baseline Verification

| Command | Result |
|---|---|
| python -m pytest -q | **238 passed** |
| python -m ruff check | **All checks passed** |
| python -m compileall -q | **Clean** |

## R3 — Phase B Completion (pending)

Remaining:
- Quick-create must call preview API, then create+lock+start in one flow
- Drawer: edit, copy, priority sorting, revision conflict handling
- Responsive: 1440/1024/390px visual check
- Accessible: aria-label, keyboard, focus management
- Build: `pnpm exec vue-tsc --noEmit && pnpm run build` (blocked: pnpm not installed)

## R4 — Phase D (pending)

Remaining:
- Fair scheduling loop (org → task round-robin)
- Recovery scan for SliceRun and CapacityLease
- SSE authentication and reconnection
- Task center UI with slice progress
- Requires Docker environment for integration testing

## R5 — Phase E (pending)

Remaining:
- Cursor inheritance logic
- Funnel aggregation
- Rollout governance (disabled/shadow/review/active)
- Metrics, alerts, docs
- Requires staging PostgreSQL for migration rehearsal

## Decisions

See `docs/query-slicing-implementation-decisions.md`.

## Next Exact Action

- Install pnpm, run `pnpm exec vue-tsc --noEmit && pnpm run build` to verify Phase B frontend.
- In Docker environment: `alembic upgrade head; alembic downgrade 20260720_0028; alembic upgrade head` to verify migration.
