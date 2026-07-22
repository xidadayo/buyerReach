# BuyerReach versioned pipeline architecture v1

## Modules and responsibilities

`pipeline.definition` locks the six requested versions and stage graph. `pipeline.stages` owns the Stage contract and registry. `pipeline.runner` owns stage idempotency. `pipeline.state_machine` is the only supported candidate-stage mutation boundary. `pipeline.configuration` captures a secret-free immutable task snapshot. `pipeline.prompts` and `pipeline.policy` separate probabilistic dimension extraction from deterministic scoring. `pipeline.outbox` writes ordered events in the caller's database transaction. Vendor protocol details remain in versioned workflow adapters.

The database remains the source of truth. Redis/Celery only transports wake-ups. Stage completion is persisted before downstream scheduling; recovery scans queued and expired-running work. A completed idempotency key returns stored output, so a crash after a billable Vendor response cannot create a second billable call.

## Candidate state transitions

| From | Allowed targets |
|---|---|
| pending | filtering, rejected, cancelled, enriching (legacy approval) |
| filtering | evidence_pending, rejected, retryable, cancelled |
| evidence_pending | scoring, retryable, cancelled |
| scoring | review, qualified, rejected, retryable, cancelled |
| review | qualified, rejected, cancelled, enriching |
| qualified | enriching, cancelled |
| enriching | promoted, enrichment_failed, retryable, cancelled |
| enrichment_failed | enriching, rejected, cancelled |
| retryable | filtering, evidence_pending, scoring, enriching, cancelled |
| promoted, rejected, cancelled | terminal |

Every transition validates its idempotency key, cancellation, retry ceiling and cost budget.

## Pipeline version 1.0.0

The ordered registry contains `provider_search`, `candidate_filtering`, `website_evidence`, `industry_enrichment`, `ai_relevance_scoring`, `rule_validation`, `result_classification`, and `contact_enrichment`, each at `1.0.0`. Scoring policy is `relevance-1.0.0`, prompt is `relevance-1.0.0`, adapter is `v1`, and evidence/result schemas are `1.0.0`.

## Upgrade and rollback

Add a new immutable Prompt, Policy or Adapter version and update a new pipeline definition; never edit an active historical version. Promote rollout through `disabled -> shadow -> review -> active` with a percentage. Shadow results write non-official score history and cannot affect decisions. Roll back by selecting the prior snapshot version or moving active to review. Disabling AI produces `pending`, never a synthetic score. Disabling automatic contact enrichment prevents new jobs but retains existing history.

Migration `20260717_0025` is the expand step: it adds nullable compatibility columns/tables and does not delete legacy score fields. Deploy readers, backfill configuration snapshots and score history in resumable batches, optionally dual-read, then contract only in a future release. Rollback one revision drops only new structures; upgrade can then run again. Back up first and run `alembic upgrade head`, `alembic downgrade 20260717_0024`, `alembic upgrade head` in staging.

## Recovery, recomputation, and observability

On startup recover database `queued`, expired `running`, and `retryable` stage runs. Lease rows with compare-and-set semantics. Persist the Vendor request identifier and normalized result before acknowledging work. Cancellation is checked before every cost-incurring call.

Recompute modes are `policy_only` (reuse AI dimensions, zero AI calls), `ai_and_policy`, and `evidence_ai_policy`. Each creates a `RecomputeBatch` and new `RelevanceScoreHistory` rows. Batches retain budget/spend, status for pause/resume, and old/new comparison.

Correlate logs with `trace_id`, `task_id`, `candidate_id`, `stage_run_id`, and `vendor_request_id`. Measure time to first candidate/AI score; stage queue/run duration; Vendor success; AI parse failure; retry; task/candidate cost; A/B/C/D distribution; manual override; and automatic-development success. Redact API keys, Authorization headers, credentials, tokens, and unnecessary contact PII from logs, audit data, snapshots, and event payloads.

API and SSE schemas are additive and versioned. Old fields remain. Frontend compatibility helpers map unknown status/rating safely and merge repeated candidate events by ID.
