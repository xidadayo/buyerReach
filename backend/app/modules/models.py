import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, cast, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.shared.models import (
    ExternalRefMixin,
    OwnershipMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
)


class Organization(UUIDMixin, TimestampMixin, Base):
    name: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(40), default="active")


class OrganizationUnit(UUIDMixin, TimestampMixin, Base):
    """Hierarchical organizational unit (department, team, division, company)."""
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_unit.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(120))
    unit_type: Mapped[str] = mapped_column(String(40), default="department")
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    path: Mapped[str] = mapped_column(String(1000), default="/", index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint("organization_id", "parent_id", "name", name="uq_org_unit_org_parent_name"),
        UniqueConstraint("organization_id", "code", name="uq_org_unit_org_code"),
    )


class Role(UUIDMixin, TimestampMixin, Base):
    name: Mapped[str] = mapped_column(String(80))
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id"), nullable=True, index=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="active")
    permission_version: Mapped[int] = mapped_column(Integer, default=1)
    data_scopes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_role_org_name"),
    )


class DataShareGrant(UUIDMixin, TimestampMixin, Base):
    """A revocable, read-only cross-unit grant that never changes ownership."""

    resource: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id"), nullable=False, index=True
    )
    source_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_unit.id"), nullable=True, index=True
    )
    target_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization_unit.id"), nullable=False, index=True
    )
    permission: Mapped[str] = mapped_column(String(20), default="read")
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)

    __table_args__ = (
        Index("ix_share_grant_active_lookup", "resource", "entity_id", "target_unit_id", "revoked_at"),
    )


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organization.id"), nullable=True)
    department_id: Mapped[str | None] = mapped_column(nullable=True)
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_unit.id"), nullable=True, index=True
    )
    role_id: Mapped[str | None] = mapped_column(ForeignKey("role.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(40), default="active")
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Company(UUIDMixin, TimestampMixin, SoftDeleteMixin, OwnershipMixin, ExternalRefMixin, Base):
    legal_name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    company_type: Mapped[str | None] = mapped_column(String(80), nullable=True)

    brands: Mapped[list["Brand"]] = relationship(back_populates="company")


class Brand(UUIDMixin, TimestampMixin, SoftDeleteMixin, OwnershipMixin, ExternalRefMixin, Base):
    company_id: Mapped[str | None] = mapped_column(ForeignKey("company.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    primary_website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="new")
    discovery_score: Mapped[int] = mapped_column(Integer, default=0)

    company: Mapped[Company | None] = relationship(back_populates="brands")


class Website(UUIDMixin, TimestampMixin, SoftDeleteMixin, ExternalRefMixin, Base):
    brand_id: Mapped[str] = mapped_column(ForeignKey("brand.id"), index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    url: Mapped[str] = mapped_column(String(500))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    verification_status: Mapped[str] = mapped_column(String(40), default="candidate")
    verified_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Contact(UUIDMixin, TimestampMixin, SoftDeleteMixin, OwnershipMixin, ExternalRefMixin, Base):
    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120), default="")
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A discovered name/title is only a lead. Contact validity is derived from
    # the best associated email by refresh_contact_status().
    status: Mapped[str] = mapped_column(String(40), default="invalid")
    last_confirmed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContactPosition(UUIDMixin, TimestampMixin, SoftDeleteMixin, ExternalRefMixin, Base):
    contact_id: Mapped[str] = mapped_column(ForeignKey("contact.id"), index=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("company.id"), nullable=True)
    brand_id: Mapped[str | None] = mapped_column(ForeignKey("brand.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index(
            "uq_contact_position_active_identity",
            "contact_id",
            func.coalesce(cast(company_id, String), ""),
            func.coalesce(cast(brand_id, String), ""),
            func.lower(func.trim(title)),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )


class EmailAddress(UUIDMixin, TimestampMixin, SoftDeleteMixin, OwnershipMixin, ExternalRefMixin, Base):
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contact.id"), nullable=True)
    brand_id: Mapped[str | None] = mapped_column(ForeignKey("brand.id"), index=True, nullable=True)
    address: Mapped[str] = mapped_column(String(255), index=True)
    normalized_address: Mapped[str] = mapped_column(String(255), index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    type: Mapped[str] = mapped_column(String(40), default="personal")
    status: Mapped[str] = mapped_column(String(40), default="raw")
    pool: Mapped[str] = mapped_column(String(60), default="raw")
    score: Mapped[int] = mapped_column(Integer, default=0)
    deliverability_score: Mapped[int] = mapped_column(Integer, default=0)
    identity_score: Mapped[int] = mapped_column(Integer, default=0)
    evidence_score: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[int] = mapped_column(Integer, default=0)
    authenticity_level: Mapped[str] = mapped_column(String(40), default="unverified", index=True)
    is_catch_all: Mapped[bool] = mapped_column(Boolean, default=False)
    is_disposable: Mapped[bool] = mapped_column(Boolean, default=False)
    domain_matches_brand: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_verified_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "normalized_address", name="uq_email_org_address"),
    )


class EmailVerification(UUIDMixin, TimestampMixin, Base):
    email_id: Mapped[str] = mapped_column(ForeignKey("email_address.id"), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    result: Mapped[str] = mapped_column(String(40))
    score: Mapped[int] = mapped_column(Integer, default=0)
    deliverability_score: Mapped[int] = mapped_column(Integer, default=0)
    identity_score: Mapped[int] = mapped_column(Integer, default=0)
    evidence_score: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[int] = mapped_column(Integer, default=0)
    authenticity_level: Mapped[str] = mapped_column(String(40), default="unverified")
    is_catch_all: Mapped[bool] = mapped_column(Boolean, default=False)
    is_disposable: Mapped[bool] = mapped_column(Boolean, default=False)
    domain_matches_brand: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_result: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceEvidence(UUIDMixin, TimestampMixin, ExternalRefMixin, Base):
    entity_type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    source_type: Mapped[str] = mapped_column(String(80))
    url: Mapped[str | None] = mapped_column(String(800), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("search_task.id"), nullable=True, index=True)
    stage_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("pipeline_stage_run.id"), nullable=True, index=True
    )
    vendor_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adapter_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    normalized_evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class SearchTask(UUIDMixin, TimestampMixin, OwnershipMixin, Base):
    name: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    configuration_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    pipeline_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    search_intent: Mapped[dict] = mapped_column(JSON, default=dict)
    intent_schema_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    intent_prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    knowledge_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # ── Query slicing additive fields (2026-07-21) ────────────────────────
    active_query_plan_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("search_query_plan.id"), nullable=True
    )
    target_result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_fetch_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_provider_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repeat_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    queue_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    queued_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admitted_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_progress_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_slice_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    waiting_slice_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_slice_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TaskItem(UUIDMixin, TimestampMixin, Base):
    task_id: Mapped[str] = mapped_column(ForeignKey("search_task.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class DiscoveryCandidate(UUIDMixin, TimestampMixin, Base):
    name: Mapped[str] = mapped_column(String(255), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    normalized_domain: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(600), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    industry_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    industry_details: Mapped[dict] = mapped_column(JSON, default=dict)
    industry_enrichment_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    industry_enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry_enriched_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emails_count: Mapped[int] = mapped_column(Integer, default=0)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(80))
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[str] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[str] = mapped_column(DateTime(timezone=True))
    last_task_id: Mapped[str | None] = mapped_column(ForeignKey("search_task.id"), nullable=True)
    exact_task_id: Mapped[str | None] = mapped_column(ForeignKey("search_task.id"), nullable=True)
    promoted_brand_id: Mapped[str | None] = mapped_column(ForeignKey("brand.id"), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    match_evaluation: Mapped[dict] = mapped_column(JSON, default=dict)
    evaluation_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    target_relevance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relevance_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)


class DiscoveryCandidateHit(UUIDMixin, TimestampMixin, Base):
    candidate_id: Mapped[str] = mapped_column(ForeignKey("discovery_candidate.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("search_task.id"), index=True)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(80))
    # Task-scoped match facts. The candidate-level columns of the same names are
    # a compatibility mirror of the latest evaluation; this row is the source of
    # truth for "how did this candidate match THIS search task".
    evaluation_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_relevance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relevance_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)
    match_evaluation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evaluated_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scoring_policy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence_schema_version: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # ── Query slicing source-hit fields (2026-07-21) ─────────────────────
    plan_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("search_query_plan.id"), nullable=True
    )
    query_slice_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("search_query_slice.id"), nullable=True
    )
    slice_run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("search_query_slice_run.id"), nullable=True
    )
    source_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_edition: Mapped[str | None] = mapped_column(String(40), nullable=True)
    observed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    source_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("candidate_id", "task_id", name="uq_discovery_candidate_task"),
        Index("ix_discovery_candidate_hit_task_eval", "task_id", "evaluation_status"),
    )


class Tag(UUIDMixin, TimestampMixin, Base):
    name: Mapped[str] = mapped_column(String(80))
    module: Mapped[str] = mapped_column(String(60))

    __table_args__ = (
        UniqueConstraint("module", "name", name="uq_tag_module_name"),
    )


class EntityTag(UUIDMixin, TimestampMixin, Base):
    entity_type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tag.id"), index=True)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "tag_id", name="uq_entity_tag_assignment"),
    )


class CustomField(UUIDMixin, TimestampMixin, Base):
    module: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(40), default="text")
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_searchable: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_list: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("module", "name", name="uq_custom_field_module_name"),
    )


class CustomValue(UUIDMixin, TimestampMixin, Base):
    field_id: Mapped[str] = mapped_column(ForeignKey("custom_field.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "field_id",
            "entity_type",
            "entity_id",
            name="uq_custom_value_field_entity",
        ),
    )


class SystemSetting(UUIDMixin, TimestampMixin, Base):
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class Blacklist(UUIDMixin, TimestampMixin, Base):
    type: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(255), unique=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(UUIDMixin, TimestampMixin, Base):
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)


# Outreach is deliberately kept separate from discovery data.  It references
# verified emails and snapshots content/configuration at scheduling time.
class SendingAccount(UUIDMixin, TimestampMixin, OwnershipMixin, Base):
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40), default="disabled")
    credential_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendor_credential.id"), nullable=True)
    from_email: Mapped[str] = mapped_column(String(255))
    from_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="disabled", index=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class EmailTemplate(UUIDMixin, TimestampMixin, OwnershipMixin, SoftDeleteMixin, Base):
    name: Mapped[str] = mapped_column(String(160))
    subject: Mapped[str] = mapped_column(String(500))
    body_html: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    variable_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_variable_policy: Mapped[str] = mapped_column(String(20), default="block")


class OutreachCampaign(UUIDMixin, TimestampMixin, OwnershipMixin, Base):
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    sending_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sending_account.id"), nullable=True)
    configuration_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)


class OutreachStep(UUIDMixin, TimestampMixin, Base):
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_campaign.id"), index=True)
    sequence_order: Mapped[int] = mapped_column(Integer)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("email_template.id"))
    template_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="active")
    __table_args__ = (UniqueConstraint("campaign_id", "sequence_order", name="uq_outreach_step_order"),)


class OutreachRecipient(UUIDMixin, TimestampMixin, OwnershipMixin, Base):
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_campaign.id"), index=True)
    email_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("email_address.id"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contact.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    stop_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    __table_args__ = (UniqueConstraint("campaign_id", "email_id", name="uq_outreach_campaign_email"),)


class OutreachMessage(UUIDMixin, TimestampMixin, Base):
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_recipient.id"), index=True)
    step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_step.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    subject_snapshot: Mapped[str] = mapped_column(String(500))
    body_text_snapshot: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (UniqueConstraint("recipient_id", "step_id", name="uq_outreach_recipient_step"),)


class OutreachEvent(UUIDMixin, TimestampMixin, Base):
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outreach_message.id"), nullable=True, index=True)
    email_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("email_address.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    provider_event_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class DataImportBatch(UUIDMixin, TimestampMixin, Base):
    """Durable staging metadata; raw rows live in DataImportRow until applied."""
    source_type: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_hash: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(40), default="uploaded", index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), index=True)
    organization_unit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organization_unit.id"), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    rollback_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DataImportRow(UUIDMixin, TimestampMixin, Base):
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_import_batch.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    entity_type: Mapped[str] = mapped_column(String(30))
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    normalized_data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    match_entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    conflict: Mapped[dict] = mapped_column(JSON, default=dict)
    applied_entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    __table_args__ = (UniqueConstraint("batch_id", "row_number", name="uq_data_import_row_number"),)


class ProviderConfig(UUIDMixin, TimestampMixin, Base):
    provider: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(80))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class VendorCredential(UUIDMixin, TimestampMixin, Base):
    vendor: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_tested_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class VendorStrategy(UUIDMixin, TimestampMixin, Base):
    name: Mapped[str] = mapped_column(String(80), unique=True, default="default")
    primary_vendor: Mapped[str] = mapped_column(String(40))
    fallback_vendors: Mapped[list] = mapped_column(JSON, default=list)
    verification_vendor: Mapped[str | None] = mapped_column(String(40), nullable=True)
    adapter_version: Mapped[str] = mapped_column(String(40), default="v1")
    local_verification_mode: Mapped[str] = mapped_column(String(20), default="disabled")
    local_verification_rollout: Mapped[int] = mapped_column(Integer, default=0)
    local_verification_sample: Mapped[int] = mapped_column(Integer, default=10)


class TaskVendorPlan(UUIDMixin, TimestampMixin, Base):
    task_id: Mapped[str] = mapped_column(ForeignKey("search_task.id"), unique=True, index=True)
    primary_vendor: Mapped[str] = mapped_column(String(40))
    fallback_vendors: Mapped[list] = mapped_column(JSON, default=list)
    verification_vendor: Mapped[str | None] = mapped_column(String(40), nullable=True)
    adapter_version: Mapped[str] = mapped_column(String(40), default="v1")
    local_verification_mode: Mapped[str] = mapped_column(String(20), default="disabled")
    local_verification_rollout: Mapped[int] = mapped_column(Integer, default=0)
    local_verification_sample: Mapped[int] = mapped_column(Integer, default=10)
    # ── Vendor pipeline additive fields (2026-07-21) ─────────────────────
    execution_mode: Mapped[str] = mapped_column(
        String(30), default="legacy_waterfall"
    )
    # legacy_waterfall | apollo_only | hunter_only | apollo_hunter
    selected_vendors: Mapped[list] = mapped_column(JSON, default=list)
    # [] | ["apollo"] | ["hunter"] | ["apollo","hunter"]
    pipeline_source: Mapped[str] = mapped_column(
        String(30), default="legacy_global_strategy"
    )
    # legacy_global_strategy | user_selection
    vendor_routes: Mapped[dict] = mapped_column(JSON, default=dict)
    # Frozen per-stage vendor routes (no API keys)


class TaskStageCheckpoint(UUIDMixin, TimestampMixin, Base):
    task_id: Mapped[str] = mapped_column(ForeignKey("search_task.id"), index=True)
    stage: Mapped[str] = mapped_column(String(80), index=True)
    scope_key: Mapped[str] = mapped_column(String(255), default="task")
    vendor: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="running")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_output: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("task_id", "stage", "scope_key", "vendor", name="uq_task_stage_scope_vendor"),
    )


class ApiUsage(UUIDMixin, TimestampMixin, Base):
    provider: Mapped[str] = mapped_column(String(80), index=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    date: Mapped[str] = mapped_column(String(10), index=True)


class DomainEvent(UUIDMixin, TimestampMixin, Base):
    event_name: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    event_type: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    aggregate_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="1")
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PipelineStageRun(UUIDMixin, TimestampMixin, Base):
    task_id: Mapped[str] = mapped_column(ForeignKey("search_task.id"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("discovery_candidate.id"), nullable=True, index=True)
    stage_name: Mapped[str] = mapped_column(String(80), index=True)
    stage_version: Mapped[str] = mapped_column(String(40))
    input_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    vendor_request_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cost: Mapped[float] = mapped_column(Float, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RelevanceScoreHistory(UUIDMixin, TimestampMixin, Base):
    task_id: Mapped[str | None] = mapped_column(ForeignKey("search_task.id"), nullable=True, index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("discovery_candidate.id"), index=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(40))
    ai_dimension_result: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[str] = mapped_column(String(20), default="pending")
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    adapter_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scoring_policy_version: Mapped[str] = mapped_column(String(40))
    is_official: Mapped[bool] = mapped_column(Boolean, default=True)


class RecomputeBatch(UUIDMixin, TimestampMixin, Base):
    mode: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_spent: Mapped[float] = mapped_column(Float, default=0)
    policy_version: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    adapter_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    comparison: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgePack(UUIDMixin, TimestampMixin, Base):
    pack_key: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    scope: Mapped[list] = mapped_column(JSON, default=list)
    concepts: Mapped[list] = mapped_column(JSON, default=list)
    relationships: Mapped[list] = mapped_column(JSON, default=list)
    ambiguity_rules: Mapped[list] = mapped_column(JSON, default=list)
    industry_mappings: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="disabled", index=True)

    __table_args__ = (UniqueConstraint("pack_key", "version", name="uq_knowledge_pack_version"),)


class RelevanceFeedback(UUIDMixin, TimestampMixin, OwnershipMixin, Base):
    task_id: Mapped[str] = mapped_column(ForeignKey("search_task.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("discovery_candidate.id"), index=True)
    original_decision: Mapped[str] = mapped_column(String(40))
    human_decision: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    false_positive: Mapped[bool] = mapped_column(Boolean, default=False)
    false_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    version_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


Index("ix_brand_org_name", Brand.organization_id, Brand.normalized_name)
Index("ix_contact_org_name", Contact.organization_id, Contact.full_name)


# ── Query Slicing (additive, 2026-07-21) ────────────────────────────────────


class SearchQueryPlan(UUIDMixin, TimestampMixin, Base):
    """A versioned, lockable query plan attached to a search task."""

    task_id: Mapped[str] = mapped_column(
        ForeignKey("search_task.id"), index=True
    )
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    generator_type: Mapped[str] = mapped_column(String(20), default="local_rules")
    generator_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    target_result_count: Mapped[int] = mapped_column(Integer, default=100)
    candidate_fetch_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_provider_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    repeat_mode: Mapped[str] = mapped_column(String(40), default="new_only")
    filter_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    source_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_search_query_plan_task_version"),
        Index("ix_search_query_plan_task_status", "task_id", "status"),
    )


class SearchQuerySlice(UUIDMixin, TimestampMixin, Base):
    """A single vendor-neutral query direction within a plan."""

    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("search_query_plan.id"), index=True
    )
    slice_key: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(40), default="core")
    target_concept_ids: Mapped[list] = mapped_column(JSON, default=list)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    target_concepts: Mapped[list] = mapped_column(JSON, default=list)
    business_types: Mapped[list] = mapped_column(JSON, default=list)
    include_terms: Mapped[list] = mapped_column(JSON, default=list)
    exclude_terms: Mapped[list] = mapped_column(JSON, default=list)
    match_mode: Mapped[str] = mapped_column(String(10), default="any")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    origin: Mapped[str] = mapped_column(String(20), default="generated")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    normalized_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint("plan_id", "slice_key", name="uq_search_query_slice_plan_key"),
        UniqueConstraint("plan_id", "normalized_hash", name="uq_search_query_slice_plan_hash"),
        Index("ix_search_query_slice_plan_enabled", "plan_id", "enabled"),
    )


class SearchQuerySliceRun(UUIDMixin, TimestampMixin, Base):
    """One page from one slice × one source — the minimum unit of billable work."""

    task_id: Mapped[str] = mapped_column(
        ForeignKey("search_task.id"), index=True
    )
    plan_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("search_query_plan.id"), index=True
    )
    query_slice_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("search_query_slice.id"), index=True
    )
    plan_version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(80))
    adapter_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    cursor_key: Mapped[str] = mapped_column(String(255))
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_acquired_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    raw_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    filtered_count: Mapped[int] = mapped_column(Integer, default=0)
    qualified_count: Mapped[int] = mapped_column(Integer, default=0)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    consecutive_empty_pages: Mapped[int] = mapped_column(Integer, default=0)
    vendor_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_output: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "query_slice_id",
            "provider",
            "operation",
            "adapter_version",
            "input_hash",
            "cursor_key",
            name="uq_slice_run_idempotency",
        ),
        Index("ix_slice_run_status_lease", "status", "lease_expires_at"),
    )


class SchedulerCapacityLease(UUIDMixin, TimestampMixin, Base):
    """Database-backed concurrency slots for fair scheduling across workers."""

    scope_type: Mapped[str] = mapped_column(String(40))
    scope_key: Mapped[str] = mapped_column(String(255))
    holder_type: Mapped[str] = mapped_column(String(40))
    holder_id: Mapped[str] = mapped_column(String(64))
    slots: Mapped[int] = mapped_column(Integer, default=1)
    lease_owner: Mapped[str] = mapped_column(String(120), index=True)
    lease_expires_at: Mapped[str] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        UniqueConstraint(
            "scope_type", "scope_key", "holder_type", "holder_id",
            name="uq_capacity_lease_scope_holder",
        ),
        Index("ix_capacity_lease_expires", "lease_expires_at"),
    )


# ── Batch Exact Brand (additive, 2026-07-22) ────────────────────────────────


class BatchImport(UUIDMixin, TimestampMixin, Base):
    """A single uploaded file of companies for batch exact-brand search."""

    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization.id"), nullable=True, index=True
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_unit.id"), nullable=True, index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    template_version: Mapped[str] = mapped_column(String(40), default="exact-brand-import-v1")
    file_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(40), default="uploaded", index=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parsed_preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parent_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_task.id"), nullable=True, index=True
    )
    confirmed_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)

    targets: Mapped[list["ExactBrandTarget"]] = relationship(back_populates="batch_import")


class ExactBrandTarget(UUIDMixin, TimestampMixin, Base):
    """A single company-domain row within a batch import — independently executed."""

    batch_import_id: Mapped[str] = mapped_column(
        ForeignKey("batch_import.id"), nullable=False
    )
    search_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("search_task.id"), nullable=True, index=True
    )
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organization.id"), nullable=True, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str] = mapped_column(String(500))
    normalized_company_name: Mapped[str] = mapped_column(String(500))
    official_domain: Mapped[str] = mapped_column(String(500))
    normalized_domain: Mapped[str] = mapped_column(String(255), index=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_input: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(40), default="pending")
    validation_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    execution_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    current_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("brand.id"), nullable=True
    )
    contact_count: Mapped[int] = mapped_column(Integer, default=0)
    reliable_email_count: Mapped[int] = mapped_column(Integer, default=0)
    review_email_count: Mapped[int] = mapped_column(Integer, default=0)
    vendor_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    batch_import: Mapped[BatchImport | None] = relationship(back_populates="targets")

    __table_args__ = (
        UniqueConstraint("batch_import_id", "row_number", name="uq_target_batch_row"),
        UniqueConstraint("batch_import_id", "normalized_domain", name="uq_target_batch_domain"),
    )
