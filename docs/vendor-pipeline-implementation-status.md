# Vendor Pipeline Implementation Status

- spec_version: 1.0
- current_phase: Phase 1 (Models, Migration, API)
- overall_status: in_progress
- last_updated_at: 2026-07-21T18:00:00+08:00
- last_updated_by: Claude Code

## Baseline

- command: python -m pytest -q
  result: 223 passed, 15 failed (query_plan_service — pre-existing, unrelated)
  evidence: backend\tests\
- command: python -m ruff check
  result: blocked (OSError 4551 — Windows application control policy)
  evidence: ruff binary blocked by system policy
- command: python -m compileall -q app migrations
  result: pass
- command: pnpm exec vue-tsc --noEmit / build
  result: not_run (pnpm unavailable)
- current_alembic_head: 20260721_0029

## Phase 1: Models, Migration, API — in_progress

- [ ] TaskVendorPlan new fields (execution_mode, selected_vendors, pipeline_source, vendor_routes)
- [ ] SearchTaskCreate.selected_vendors
- [ ] VendorCapabilities API (GET /vendor-capabilities)
- [ ] create_search_task validates + freezes vendor plan
- [ ] Alembic migration 20260721_0030
- [ ] Tests

## Phase 2: Strict Vendor Routing — pending
## Phase 3: Apollo Pipeline — pending
## Phase 4: Hunter Pipeline — pending
## Phase 5: Dual Vendor Merge — pending
## Phase 6: Brand Discovery Contact/Email — pending
## Phase 7: Frontend, Rollout, Docs — pending

## Decisions
See `docs/vendor-pipeline-implementation-decisions.md`.

## Next Exact Action
- Add TaskVendorPlan fields + migration
