"""Pydantic schemas for Query Plan / Query Slice / Slice Run API.

Follows the same conventions as ``app.modules.schemas``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ───────────────────────────────────────────────────────────────────

PlanStatus = Literal["draft", "review", "locked", "superseded"]
SliceStatus = Literal["draft", "enabled", "disabled", "deleted"]
SlicePurpose = Literal["core", "synonym", "local_language", "business_type", "adjacent", "exploratory"]
SliceOrigin = Literal["generated", "user_added", "user_modified"]
RepeatMode = Literal["new_only", "refresh_stale", "re_evaluate"]
GeneratorType = Literal["ai", "local_rules", "user"]


# ── Query Slice ─────────────────────────────────────────────────────────────

class QuerySliceCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    purpose: SlicePurpose = "core"
    target_concept_ids: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    target_concepts: list[str] = Field(default_factory=list)
    business_types: list[str] = Field(default_factory=list)
    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    match_mode: Literal["any", "all"] = "any"
    priority: int = Field(default=0, ge=0)
    enabled: bool = True
    reason: str | None = Field(default=None, max_length=2000)
    target_count: int | None = Field(default=None, ge=0)
    candidate_limit: int | None = Field(default=None, ge=0)


class QuerySliceUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    purpose: SlicePurpose | None = None
    target_concept_ids: list[str] | None = None
    countries: list[str] | None = None
    target_concepts: list[str] | None = None
    business_types: list[str] | None = None
    include_terms: list[str] | None = None
    exclude_terms: list[str] | None = None
    match_mode: Literal["any", "all"] | None = None
    priority: int | None = Field(default=None, ge=0)
    enabled: bool | None = None
    reason: str | None = Field(default=None, max_length=2000)
    target_count: int | None = Field(default=None, ge=0)
    candidate_limit: int | None = Field(default=None, ge=0)


class QuerySliceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    slice_key: str
    label: str
    purpose: str
    target_concept_ids: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    target_concepts: list[str] = Field(default_factory=list)
    business_types: list[str] = Field(default_factory=list)
    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    match_mode: str
    priority: int
    enabled: bool
    origin: str
    reason: str | None = None
    target_count: int | None = None
    candidate_limit: int | None = None
    normalized_hash: str
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Query Plan ──────────────────────────────────────────────────────────────

class QueryPlanCreate(BaseModel):
    target_result_count: int = Field(default=100, ge=1, le=5000)
    candidate_fetch_limit: int | None = Field(default=None, ge=1)
    max_provider_calls: int | None = Field(default=None, ge=0)
    budget_limit: float | None = Field(default=None, ge=0)
    repeat_mode: RepeatMode = "new_only"
    generator_type: GeneratorType = "local_rules"


class QueryPlanUpdate(BaseModel):
    target_result_count: int | None = Field(default=None, ge=1, le=5000)
    candidate_fetch_limit: int | None = Field(default=None, ge=1)
    max_provider_calls: int | None = Field(default=None, ge=0)
    budget_limit: float | None = Field(default=None, ge=0)
    repeat_mode: RepeatMode | None = None
    updated_at: datetime  # optimistic concurrency control


class QueryPlanLockRequest(BaseModel):
    updated_at: datetime  # optimistic concurrency — must match server state


class QueryPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    organization_id: UUID | None = None
    version: int
    schema_version: str
    generator_type: str
    generator_version: str | None = None
    status: str
    target_result_count: int
    candidate_fetch_limit: int | None = None
    max_provider_calls: int | None = None
    budget_limit: float | None = None
    repeat_mode: str
    filter_policy: dict[str, Any] = Field(default_factory=dict)
    source_policy: dict[str, Any] = Field(default_factory=dict)
    created_by: UUID | None = None
    locked_by: UUID | None = None
    locked_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    slices: list[QuerySliceRead] = Field(default_factory=list)


# ── Preview (no task created yet) ───────────────────────────────────────────

class QueryPlanPreviewRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=2000)
    target_result_count: int = Field(default=100, ge=1, le=5000)
    countries: list[str] = Field(default_factory=list)
    preset: Literal["precision", "balanced", "volume"] = "balanced"


class QueryPlanPreviewResponse(BaseModel):
    intent: dict[str, Any]
    slices: list[QuerySliceCreate]
    summary: str
    estimated_provider_calls: int
    warnings: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    source: GeneratorType = "local_rules"


# ── Slice Run (read-only) ───────────────────────────────────────────────────

class SliceRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    plan_id: UUID
    query_slice_id: UUID
    plan_version: int
    provider: str
    operation: str
    adapter_version: str | None = None
    input_hash: str
    cursor_key: str
    cursor: dict[str, Any] = Field(default_factory=dict)
    status: str
    lease_owner: str | None = None
    lease_acquired_at: datetime | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    next_retry_at: datetime | None = None
    attempts: int
    raw_count: int
    new_count: int
    duplicate_count: int
    filtered_count: int
    qualified_count: int
    review_count: int
    call_count: int
    cost: float
    consecutive_empty_pages: int
    vendor_request_id: str | None = None
    normalized_output: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ── Task continue-new ───────────────────────────────────────────────────────

class ContinueNewRequest(BaseModel):
    repeat_mode: RepeatMode = "new_only"


class ContinueNewResponse(BaseModel):
    plan_id: UUID
    task_id: UUID
    version: int
    status: str
