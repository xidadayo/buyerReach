import csv
import hashlib
import io
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import urlparse
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.crypto import (
    decrypt_provider_config,
    decrypt_secret,
    encrypt_provider_config,
    encrypt_secret,
    is_sensitive_config_key,
)
from app.modules.brand_discovery import (
    filter_discovery_companies,
    filter_exact_brand_companies,
    score_brand_relevance,
)
from app.modules.email_inference import infer_emails
from app.modules.models import (
    ApiUsage,
    AuditLog,
    Blacklist,
    Brand,
    Company,
    Contact,
    ContactPosition,
    DiscoveryCandidate,
    DiscoveryCandidateHit,
    DomainEvent,
    EmailAddress,
    EmailVerification,
    EntityTag,
    OrganizationUnit,
    ProviderConfig,
    RelevanceScoreHistory,
    SearchQueryPlan,
    SearchQuerySlice,
    SearchTask,
    SourceEvidence,
    SystemSetting,
    Tag,
    TaskItem,
    TaskStageCheckpoint,
    TaskVendorPlan,
    VendorCredential,
    VendorStrategy,
    CustomField,
    CustomValue,
    DataShareGrant,
    Role,
    User,
    Website,
)
from app.modules.schemas import (
    AITaskPlanRequest,
    BlacklistCreate,
    BrandCreate,
    BrandUpdate,
    ContactCreate,
    ContactUpdate,
    CustomFieldCreate,
    CustomFieldUpdate,
    DedupMergeRequest,
    EmailCreate,
    EmailUpdate,
    ProviderConfigCreate,
    ProviderConfigUpdate,
    VendorCredentialUpdate,
    VendorStrategyUpdate,
    RoleCreate,
    RoleUpdate,
    SearchTaskCreate,
    SystemSettingsUpdate,
    TagCreate,
    TagUpdate,
    UserCreate,
    UserUpdate,
)
from app.providers.http import execute_provider, extract_items
from app.providers.base import ProviderResult
from app.providers.vendors import (
    CATALOG_SUPPORTED_TYPES,
    CONFIGURABLE_CATALOG_ADAPTERS,
    check_vendor_provider_quota,
    test_catalog_provider_connection,
)
from app.providers.local import slugify, title_priority
from app.providers.workflows import (
    ADAPTER_VERSION,
    SEARCH_VENDORS,
    VERIFICATION_VENDORS,
    adapter_for,
)
from app.shared.enums import EmailPool, EmailStatus, SourceType, TaskStatus
from app.shared.models import utc_now
from app.pipeline.configuration import capture_configuration
from app.pipeline.definition import PIPELINE_V1, PIPELINE_V2
from app.pipeline.concepts import SearchIntent, intent_from_legacy
from app.pipeline.matching import plan_provider_queries
from app.pipeline.outbox import add_event
from app.pipeline.runner import begin_stage, complete_stage, fail_stage
from app.pipeline.state_machine import (
    TransitionContext,
    transition_candidate,
    transition_task,
)

DEFAULT_SYSTEM_SETTINGS = {
    "title_dictionary": {
        "p1": ["Buyer", "Head of Buying", "Sourcing Manager", "Procurement Manager"],
        "p2": ["Product Development Manager", "Merchandising Manager", "Category Manager"],
        "p3": ["Founder", "Owner", "Managing Director", "Operations Director"],
        "excluded": ["Intern", "Student", "Recruiter", "Former Employee"],
    },
    "email_rules": {
        "valid_score": 70,
        "risky_score": 40,
        "verified_confidence": 80,
        "probable_confidence": 65,
    },
    "task_rules": {
        "max_attempts": 3,
        "retry_delay_seconds": 60,
        "max_concurrency": 4,
        "default_contact_limit": 5,
    },
    "ai": {
        "enabled": False,
        "provider": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model_name": "gpt-4o-mini",
        "request_timeout_seconds": 60,
        "api_key": "",
    },
}


def list_page(db: Session, model: type, page: int = 1, page_size: int = 50) -> dict:
    statement = select(model)
    if hasattr(model, "deleted_at"):
        statement = statement.where(model.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(
        statement.order_by(model.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return page_result(total, page, page_size, [to_dict(item) for item in items])


def add_ownership_labels(db: Session, items: list[dict]) -> list[dict]:
    """Add human-readable unit and owner labels without per-row queries."""
    unit_ids = {
        UUID(str(item["department_id"]))
        for item in items
        if item.get("department_id")
    }
    owner_ids = {
        UUID(str(item["owner_id"]))
        for item in items
        if item.get("owner_id")
    }
    unit_names = {
        str(unit.id): unit.name
        for unit in db.scalars(
            select(OrganizationUnit).where(OrganizationUnit.id.in_(unit_ids))
        )
    } if unit_ids else {}
    owner_names = {
        str(owner.id): owner.name
        for owner in db.scalars(select(User).where(User.id.in_(owner_ids)))
    } if owner_ids else {}
    for item in items:
        item["department_name"] = unit_names.get(str(item.get("department_id")), "未分组")
        item["owner_name"] = owner_names.get(str(item.get("owner_id")), "组共享")
    return items


def _shared_group_emails(db: Session, authorization) -> list[EmailAddress]:
    """Emails shared with the current scope but not owned by an individual."""
    if authorization is None:
        return []
    from app.authz.scope import apply_scope

    statement = apply_scope(
        select(EmailAddress)
        .where(EmailAddress.deleted_at.is_(None), EmailAddress.owner_id.is_(None)),
        EmailAddress,
        db,
        authorization,
        "emails",
    )
    return list(db.scalars(statement))


def list_search_tasks(
    db: Session,
    page: int = 1,
    page_size: int = 50,
    organization_id: UUID | None = None,
    authorization=None,
) -> dict:
    """List active task history while keeping cancelled records for audit access."""
    statement = select(SearchTask).where(SearchTask.status != TaskStatus.cancelled)
    if organization_id is not None:
        statement = statement.where(SearchTask.organization_id == organization_id)
    if authorization is not None:
        from app.authz.scope import apply_scope

        statement = apply_scope(statement, SearchTask, db, authorization, "tasks", include_shared=True)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(
        statement.order_by(SearchTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    result = [to_dict(item) for item in items]

    # Enrich batch tasks with target progress
    for i, item in enumerate(items):
        if item.mode == "batch_exact_brand":
            from app.modules.models import ExactBrandTarget
            targets = list(
                db.scalars(
                    select(ExactBrandTarget).where(
                        ExactBrandTarget.search_task_id == str(item.id)
                    )
                ).all()
            )
            batch_progress = {
                "targets_total": len(targets),
                "targets_completed": sum(1 for t in targets if t.execution_status == "completed"),
                "targets_running": sum(1 for t in targets if t.execution_status == "running"),
                "targets_pending": sum(1 for t in targets if t.execution_status in ("pending", "queued")),
                "targets_no_match": sum(1 for t in targets if t.execution_status == "no_match"),
                "targets_failed": sum(1 for t in targets if t.execution_status in ("failed", "retryable")),
                "targets_cancelled": sum(1 for t in targets if t.execution_status == "cancelled"),
                "total_reliable_emails": sum(t.reliable_email_count or 0 for t in targets),
            }
            result[i]["progress"] = {**result[i].get("progress", {}), **batch_progress}

    return page_result(total, page, page_size, add_ownership_labels(db, result))


def list_brands(db: Session, page: int, page_size: int, *, authorization=None) -> dict:
    current_brand_contact_ids = (
        select(ContactPosition.contact_id)
        .join(Contact, ContactPosition.contact_id == Contact.id)
        .where(
            ContactPosition.brand_id == Brand.id,
            ContactPosition.is_current.is_(True),
            ContactPosition.deleted_at.is_(None),
            Contact.deleted_at.is_(None),
        )
        .correlate(Brand)
    )
    email_count = (
        select(func.count(EmailAddress.id))
        .where(
            EmailAddress.deleted_at.is_(None),
            or_(
                EmailAddress.brand_id == Brand.id,
                EmailAddress.contact_id.in_(current_brand_contact_ids),
            ),
        )
        .correlate(Brand)
        .scalar_subquery()
    )
    statement = (
        select(Brand, Company.legal_name, email_count.label("email_count"))
        .outerjoin(Company, Brand.company_id == Company.id)
        .where(
            Brand.deleted_at.is_(None),
            Brand.status.notin_({"pending_review", "migrated_candidate"}),
        )
        .order_by(Brand.created_at.desc())
    )
    if authorization is not None:
        from app.authz.scope import apply_scope

        statement = apply_scope(statement, Brand, db, authorization, "brands", include_shared=True)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    brand_ids = [str(brand.id) for brand, _company_name, _email_count in rows]
    evaluated_brand_ids: set[str] = set()
    if brand_ids:
        evidence_rows = db.execute(
            select(SourceEvidence.entity_id, SourceEvidence.normalized_evidence).where(
                SourceEvidence.entity_type == "brand",
                SourceEvidence.entity_id.in_(brand_ids),
            )
        ).all()
        evaluated_brand_ids = {
            entity_id
            for entity_id, evidence in evidence_rows
            if isinstance(evidence, dict)
            and bool(evidence.get("industry_source"))
            and evidence.get("industry_confidence") is not None
        }
    items = []
    for brand, company_name, brand_email_count in rows:
        item = to_dict(brand)
        item["company_name"] = company_name
        item["email_count"] = brand_email_count
        item["industry_status"] = "available" if brand.category else "missing"
        item["relevance_status"] = (
            "evaluated" if str(brand.id) in evaluated_brand_ids else "pending"
        )
        items.append(item)
    visible_ids = {str(item["id"]) for item in items}
    shared_by_brand: dict[UUID, list[EmailAddress]] = {}
    for email in _shared_group_emails(db, authorization):
        if email.brand_id:
            shared_by_brand.setdefault(email.brand_id, []).append(email)
    if shared_by_brand:
        shared_rows = db.execute(
            select(Brand, Company.legal_name)
            .outerjoin(Company, Brand.company_id == Company.id)
            .where(Brand.id.in_(set(shared_by_brand)))
        ).all()
        for brand, company_name in shared_rows:
            if str(brand.id) in visible_ids:
                continue
            item = to_dict(brand)
            item.update(
                {
                    "company_name": company_name,
                    "email_count": len(shared_by_brand[brand.id]),
                    "industry_status": "available" if brand.category else "missing",
                    "relevance_status": "pending",
                    "is_shared_context": True,
                    "department_name": "共享关联",
                    "owner_name": "只读",
                }
            )
            items.append(item)
    result = page_result(total + sum(1 for item in items if item.get("is_shared_context")), page, page_size, add_ownership_labels(db, items))
    for item in result["items"]:
        if item.get("is_shared_context"):
            item["department_name"] = "共享关联"
            item["owner_name"] = "只读"
    return result


def list_brand_hierarchy(db: Session, page: int, page_size: int, *, authorization=None) -> dict:
    brand_statement = (
        select(Brand)
        .where(
            Brand.deleted_at.is_(None),
            Brand.status.notin_({"pending_review", "migrated_candidate"}),
        )
        .order_by(Brand.created_at.desc())
    )
    if authorization is not None:
        from app.authz.scope import apply_scope

        brand_statement = apply_scope(brand_statement, Brand, db, authorization, "brands")
    total = db.scalar(select(func.count()).select_from(brand_statement.subquery())) or 0
    brands = db.scalars(brand_statement.offset((page - 1) * page_size).limit(page_size)).all()

    brand_ids = [brand.id for brand in brands]
    positions_statement = (
        select(ContactPosition, Contact)
        .join(Contact, ContactPosition.contact_id == Contact.id)
        .where(
            ContactPosition.brand_id.in_(brand_ids),
            ContactPosition.is_current.is_(True),
            ContactPosition.deleted_at.is_(None),
            Contact.deleted_at.is_(None),
        )
        .order_by(Contact.full_name)
    )
    if authorization is not None:
        from app.authz.scope import apply_scope

        positions_statement = apply_scope(
            positions_statement, Contact, db, authorization, "contacts"
        )
    positions = db.execute(positions_statement).all()
    contacts_by_brand: dict[UUID, list[dict]] = {brand_id: [] for brand_id in brand_ids}
    # A contact may hold current positions at more than one brand. Keep every
    # brand-specific view instead of overwriting the first one by contact ID.
    contacts_by_id: dict[UUID, list[dict]] = {}
    for position, contact in positions:
        item = {**to_dict(contact), "title": position.title, "emails": []}
        contacts_by_brand[position.brand_id].append(item)
        contacts_by_id.setdefault(contact.id, []).append(item)

    emails_statement = (
        select(EmailAddress)
        .where(
            EmailAddress.deleted_at.is_(None),
            or_(
                EmailAddress.brand_id.in_(brand_ids),
                EmailAddress.contact_id.in_(list(contacts_by_id)),
            ),
        )
        .order_by(EmailAddress.address)
    )
    if authorization is not None:
        from app.authz.scope import apply_scope

        emails_statement = apply_scope(
            emails_statement, EmailAddress, db, authorization, "emails"
        )
    emails = db.scalars(emails_statement).all()
    direct_emails_by_brand: dict[UUID, list[dict]] = {brand_id: [] for brand_id in brand_ids}
    for email in emails:
        email_item = to_dict(email)
        if email.contact_id in contacts_by_id:
            for contact_item in contacts_by_id[email.contact_id]:
                contact_item["emails"].append(email_item)
        elif email.brand_id in direct_emails_by_brand:
            direct_emails_by_brand[email.brand_id].append(email_item)

    for contact_items in contacts_by_id.values():
        for contact in contact_items:
            contact_emails = contact["emails"]
            valid_email_count = sum(
                1 for email in contact_emails if _email_makes_contact_valid(email)
            )
            contact["email_count"] = len(contact_emails)
            contact["valid_email_count"] = valid_email_count
            contact["is_valid"] = valid_email_count > 0

    # A group-shared email can retain a relationship to a contact or brand
    # outside the viewer's normal brand scope. It is still a record the group
    # is authorized to use, so expose it in a separate, explicitly labelled
    # section rather than silently dropping it or presenting it as local data.
    shared_email_relationships: list[dict] = []
    if authorization is not None:
        from app.authz.scope import apply_scope

        visible_email_ids = [email.id for email in emails]
        shared_statement = select(EmailAddress).where(
            EmailAddress.deleted_at.is_(None),
            EmailAddress.owner_id.is_(None),
        )
        if visible_email_ids:
            shared_statement = shared_statement.where(EmailAddress.id.notin_(visible_email_ids))
        shared_statement = apply_scope(
            shared_statement, EmailAddress, db, authorization, "emails"
        )
        shared_emails = db.scalars(shared_statement.order_by(EmailAddress.address)).all()
        if shared_emails:
            contact_ids = {email.contact_id for email in shared_emails if email.contact_id}
            related_brand_ids = {email.brand_id for email in shared_emails if email.brand_id}
            related_contacts = {
                contact.id: contact
                for contact in db.scalars(
                    select(Contact).where(Contact.id.in_(contact_ids))
                )
            } if contact_ids else {}
            related_brands = {
                brand.id: brand
                for brand in db.scalars(select(Brand).where(Brand.id.in_(related_brand_ids)))
            } if related_brand_ids else {}
            company_ids = {
                brand.company_id for brand in related_brands.values() if brand.company_id
            }
            company_names = {
                company.id: company.legal_name
                for company in db.scalars(select(Company).where(Company.id.in_(company_ids)))
            } if company_ids else {}
            related_unit_ids = {
                entity.department_id
                for entity in [*related_contacts.values(), *related_brands.values()]
                if entity.department_id
            }
            related_unit_names = {
                unit.id: unit.name
                for unit in db.scalars(
                    select(OrganizationUnit).where(OrganizationUnit.id.in_(related_unit_ids))
                )
            } if related_unit_ids else {}
            for email in add_ownership_labels(db, [to_dict(item) for item in shared_emails]):
                contact = related_contacts.get(UUID(str(email["contact_id"]))) if email.get("contact_id") else None
                brand = related_brands.get(UUID(str(email["brand_id"]))) if email.get("brand_id") else None
                shared_email_relationships.append(
                    {
                        "email": email,
                        "contact": (
                            {
                                "id": str(contact.id),
                                "name": contact.full_name,
                                "department_name": related_unit_names.get(contact.department_id),
                            }
                            if contact is not None
                            else None
                        ),
                        "brand": (
                            {
                                "id": str(brand.id),
                                "name": brand.name,
                                "company_name": company_names.get(brand.company_id),
                            }
                            if brand is not None
                            else None
                        ),
                    }
                )

    items = []
    for brand in brands:
        contacts = contacts_by_brand[brand.id]
        direct_emails = direct_emails_by_brand[brand.id]
        item = to_dict(brand)
        item.update(
            {
                "contacts": contacts,
                "brand_emails": direct_emails,
                "contact_count": sum(1 for contact in contacts if contact["is_valid"]),
                "valid_contact_count": sum(1 for contact in contacts if contact["is_valid"]),
                "discovered_contact_count": len(contacts),
                "invalid_contact_count": sum(1 for contact in contacts if not contact["is_valid"]),
                "email_count": len(direct_emails)
                + sum(len(contact["emails"]) for contact in contacts),
                "verified_email_count": sum(
                    1 for email in direct_emails if _email_makes_contact_valid(email)
                )
                + sum(contact["valid_email_count"] for contact in contacts),
            }
        )
        items.append(item)
    result = page_result(total, page, page_size, add_ownership_labels(db, items))
    result["shared_email_relationships"] = shared_email_relationships
    return result


def list_contacts(
    db: Session,
    page: int,
    page_size: int,
    search: str | None = None,
    organization_id: UUID | None = None,
    authorization=None,
) -> dict:
    email_count = (
        select(func.count(EmailAddress.id))
        .where(EmailAddress.contact_id == Contact.id, EmailAddress.deleted_at.is_(None))
        .correlate(Contact)
        .scalar_subquery()
    )
    valid_email_count = (
        select(func.count(EmailAddress.id))
        .where(
            EmailAddress.contact_id == Contact.id,
            EmailAddress.deleted_at.is_(None),
            EmailAddress.authenticity_level == "verified",
            EmailAddress.pool == EmailPool.valid,
        )
        .correlate(Contact)
        .scalar_subquery()
    )
    position_title = (
        select(ContactPosition.title)
        .where(
            ContactPosition.contact_id == Contact.id,
            ContactPosition.is_current.is_(True),
            ContactPosition.deleted_at.is_(None),
        )
        .order_by(ContactPosition.priority.desc(), ContactPosition.created_at.desc())
        .limit(1)
        .correlate(Contact)
        .scalar_subquery()
    )
    position_brand_name = (
        select(Brand.name)
        .join(ContactPosition, ContactPosition.brand_id == Brand.id)
        .where(
            ContactPosition.contact_id == Contact.id,
            ContactPosition.is_current.is_(True),
            ContactPosition.deleted_at.is_(None),
            Brand.deleted_at.is_(None),
        )
        .order_by(ContactPosition.priority.desc(), ContactPosition.created_at.desc())
        .limit(1)
        .correlate(Contact)
        .scalar_subquery()
    )
    filters = [Contact.deleted_at.is_(None)]
    filters.append(
        Contact.organization_id.is_(None)
        if organization_id is None
        else Contact.organization_id == organization_id
    )
    cleaned_search = (search or "").strip()
    if cleaned_search:
        escaped = cleaned_search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        filters.append(
            or_(
                Contact.full_name.ilike(pattern, escape="\\"),
                Contact.linkedin_url.ilike(pattern, escape="\\"),
                select(ContactPosition.id)
                .where(
                    ContactPosition.contact_id == Contact.id,
                    ContactPosition.deleted_at.is_(None),
                    ContactPosition.title.ilike(pattern, escape="\\"),
                )
                .exists(),
                select(ContactPosition.id)
                .join(Brand, ContactPosition.brand_id == Brand.id)
                .where(
                    ContactPosition.contact_id == Contact.id,
                    ContactPosition.deleted_at.is_(None),
                    Brand.deleted_at.is_(None),
                    Brand.name.ilike(pattern, escape="\\"),
                )
                .exists(),
                select(EmailAddress.id)
                .where(
                    EmailAddress.contact_id == Contact.id,
                    EmailAddress.deleted_at.is_(None),
                    EmailAddress.normalized_address.ilike(pattern, escape="\\"),
                )
                .exists(),
            )
        )
    statement = (
        select(
            Contact,
            position_title.label("title"),
            position_brand_name.label("brand_name"),
            email_count.label("email_count"),
            valid_email_count.label("valid_email_count"),
        )
        .where(*filters)
        .order_by(Contact.created_at.desc())
    )
    if authorization is not None:
        from app.authz.scope import apply_scope

        statement = apply_scope(statement, Contact, db, authorization, "contacts", include_shared=True)
        scoped_ids = apply_scope(
            select(Contact.id).where(*filters), Contact, db, authorization, "contacts", include_shared=True
        )
    else:
        scoped_ids = select(Contact.id).where(*filters)
    total = (
        db.scalar(
            select(func.count()).select_from(scoped_ids.subquery())
        )
        or 0
    )
    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for contact, title, brand_name, contact_email_count, contact_valid_email_count in rows:
        item = to_dict(contact)
        item.update(
            {
                "title": title,
                "brand_name": brand_name,
                "email_count": contact_email_count,
                "valid_email_count": contact_valid_email_count,
                "is_valid": contact_valid_email_count > 0,
            }
        )
        items.append(item)
    visible_ids = {str(item["id"]) for item in items}
    shared_by_contact: dict[UUID, list[EmailAddress]] = {}
    for email in _shared_group_emails(db, authorization):
        if email.contact_id:
            shared_by_contact.setdefault(email.contact_id, []).append(email)
    if shared_by_contact:
        shared_rows = db.execute(
            select(Contact, position_title.label("title"), position_brand_name.label("brand_name"))
            .where(Contact.id.in_(set(shared_by_contact)), *filters)
        ).all()
        for contact, title, brand_name in shared_rows:
            if str(contact.id) in visible_ids:
                continue
            emails_for_contact = shared_by_contact[contact.id]
            item = to_dict(contact)
            item.update(
                {
                    "title": title,
                    "brand_name": brand_name,
                    "email_count": len(emails_for_contact),
                    "valid_email_count": sum(
                        1 for email in emails_for_contact if _email_makes_contact_valid(email)
                    ),
                    "is_valid": any(
                        _email_makes_contact_valid(email) for email in emails_for_contact
                    ),
                    "is_shared_context": True,
                    "department_name": "共享关联",
                    "owner_name": "只读",
                }
            )
            items.append(item)
    result = page_result(total + sum(1 for item in items if item.get("is_shared_context")), page, page_size, add_ownership_labels(db, items))
    for item in result["items"]:
        if item.get("is_shared_context"):
            item["department_name"] = "共享关联"
            item["owner_name"] = "只读"
    return result


def list_emails(
    db: Session,
    page: int,
    page_size: int,
    contact_id: UUID | None = None,
    authenticity_level: str | None = None,
    pool: str | None = None,
    min_confidence: int | None = None,
    brand_id: UUID | None = None,
    authorization=None,
) -> dict:
    filters = [EmailAddress.deleted_at.is_(None)]
    if contact_id is not None:
        filters.append(EmailAddress.contact_id == contact_id)
    elif brand_id is not None:
        current_brand_contact_ids = (
            select(ContactPosition.contact_id)
            .join(Contact, ContactPosition.contact_id == Contact.id)
            .where(
                ContactPosition.brand_id == brand_id,
                ContactPosition.is_current.is_(True),
                ContactPosition.deleted_at.is_(None),
                Contact.deleted_at.is_(None),
            )
        )
        filters.append(
            or_(
                EmailAddress.brand_id == brand_id,
                EmailAddress.contact_id.in_(current_brand_contact_ids),
            )
        )
    if authenticity_level:
        filters.append(EmailAddress.authenticity_level == authenticity_level)
    if pool:
        filters.append(EmailAddress.pool == pool)
    if min_confidence is not None:
        filters.append(EmailAddress.confidence_score >= max(0, min(100, min_confidence)))
    statement = (
        select(EmailAddress, Contact.full_name, Brand.name)
        .outerjoin(Contact, EmailAddress.contact_id == Contact.id)
        .outerjoin(Brand, EmailAddress.brand_id == Brand.id)
        .where(*filters)
        .order_by(EmailAddress.created_at.desc())
    )
    scoped_ids = select(EmailAddress.id).where(*filters)
    if authorization is not None:
        from app.authz.scope import apply_scope

        statement = apply_scope(statement, EmailAddress, db, authorization, "emails", include_shared=True)
        scoped_ids = apply_scope(scoped_ids, EmailAddress, db, authorization, "emails", include_shared=True)
    total = (
        db.scalar(
            select(func.count()).select_from(scoped_ids.subquery())
        )
        or 0
    )
    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).all()
    items = []
    for email, contact_name, brand_name in rows:
        item = to_dict(email)
        item["contact_name"] = contact_name
        item["brand_name"] = brand_name
        items.append(item)
    return page_result(total, page, page_size, add_ownership_labels(db, items))


def list_task_items(db: Session, task_id: UUID, page: int, page_size: int) -> dict:
    statement = (
        select(TaskItem).where(TaskItem.task_id == task_id).order_by(TaskItem.created_at.desc())
    )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return page_result(total, page, page_size, [to_dict(item) for item in items])


def list_task_checkpoints(db: Session, task_id: UUID) -> dict:
    plan = db.scalar(select(TaskVendorPlan).where(TaskVendorPlan.task_id == task_id))
    checkpoints = db.scalars(
        select(TaskStageCheckpoint)
        .where(TaskStageCheckpoint.task_id == task_id)
        .order_by(TaskStageCheckpoint.created_at.asc())
    ).all()
    return {
        "plan": to_dict(plan) if plan is not None else None,
        "checkpoints": [to_dict(item) for item in checkpoints],
    }


def create_search_task(
    db: Session,
    payload: SearchTaskCreate,
    *,
    organization_id: UUID | None = None,
    owner_id: UUID | None = None,
    organization_unit_id: UUID | None = None,
) -> SearchTask:
    if payload.selected_vendors is not None:
        unavailable: list[str] = []
        for vendor in payload.selected_vendors:
            credential = _vendor_credential(db, vendor)
            if (
                credential is None
                or not credential.encrypted_api_key
                or credential.last_test_ok is False
            ):
                unavailable.append(vendor)
        if unavailable:
            raise ValueError(
                "Selected Vendors are unavailable or failed their connection test: "
                + ", ".join(unavailable)
            )
    configuration_version, configuration_snapshot = capture_configuration(db)
    if payload.search_intent:
        intent = SearchIntent.model_validate(payload.search_intent)
        pipeline = PIPELINE_V2
    elif payload.original_prompt and payload.mode == "brand_discovery":
        intent = intent_from_legacy(
            payload.original_prompt,
            payload.categories,
            payload.countries,
            payload.company_types,
            payload.category_match_mode,
        )
        pipeline = PIPELINE_V2
    else:
        intent = None
        pipeline = PIPELINE_V1
    task_filters = payload.model_dump(mode="json")
    if intent is not None and payload.mode == "brand_discovery":
        task_filters["categories"] = [item.normalized_label for item in intent.target_concepts]
        scoped_countries = [
            item.value for item in intent.global_qualifiers if item.type == "country"
        ]
        if scoped_countries:
            task_filters["countries"] = list(dict.fromkeys(scoped_countries))
        task_filters["company_types"] = intent.business_types
    if intent is not None:
        configuration_snapshot = {
            **configuration_snapshot,
            "concept_matching": {
                "pipeline_version": pipeline.pipeline_version,
                "intent_schema_version": "2.0.0",
                "intent_prompt_version": "intent-2.0.2",
                "company_profile_prompt_version": "company-profile-2.0.0",
                "concept_match_prompt_version": "concept-match-2.0.0",
                "scoring_policy_version": pipeline.scoring_policy_version,
                "evidence_schema_version": pipeline.evidence_schema_version,
                "result_schema_version": pipeline.result_schema_version,
                "knowledge_sources": intent.knowledge_sources,
                "rollout": {"status": "review", "rollout_percentage": 0},
            },
        }
    task = SearchTask(
        organization_id=organization_id,
        owner_id=owner_id,
        department_id=organization_unit_id,
        name=payload.name,
        mode=payload.mode,
        status=TaskStatus.draft,
        filters=task_filters,
        progress={"brands": 0, "websites": 0, "contacts": 0, "emails": 0},
        budget_limit=payload.budget_limit,
        configuration_version=configuration_version,
        configuration_snapshot=configuration_snapshot,
        pipeline_version=pipeline.pipeline_version,
        search_intent=intent.model_dump(mode="json") if intent else {},
        intent_schema_version="2.0.0" if intent else None,
        intent_prompt_version="intent-2.0.2" if intent else None,
        knowledge_snapshot={"sources": intent.knowledge_sources} if intent else {},
        trace_id=hashlib.sha256(
            f"task:{utc_now().isoformat()}:{payload.name}".encode()
        ).hexdigest()[:32],
    )
    db.add(task)
    db.flush()
    ensure_task_vendor_plan(db, task)
    audit(
        db, "search_task.create", "search_task", str(task.id), after=payload.model_dump(mode="json")
    )
    emit(db, "task.created", {"task_id": task.id})
    return task


def queue_search_task(db: Session, task_id: UUID) -> SearchTask:
    task = db.get(SearchTask, task_id)
    if task is None:
        raise ValueError("Search task not found")
    if task.status in {TaskStatus.queued, TaskStatus.running, TaskStatus.completed}:
        return task
    if task.status == TaskStatus.cancelled:
        raise ValueError("Cancelled tasks must be copied before running again")
    plan = ensure_task_vendor_plan(db, task)
    if not plan.selected_vendors:
        raise ValueError(
            "Legacy tasks cannot be started. Copy the task and select Apollo, Hunter, or both."
        )
    transition_task(task, TaskStatus.queued)
    task.error_message = None
    audit(db, "search_task.queue", "search_task", str(task.id))
    emit(db, "task.queued", {"task_id": task.id})
    return task


def pause_search_task(db: Session, task_id: UUID) -> SearchTask:
    task = db.get(SearchTask, task_id)
    if task is None:
        raise ValueError("Search task not found")
    if task.status not in {TaskStatus.queued, TaskStatus.running}:
        raise ValueError("Only queued or running tasks can be paused")
    transition_task(task, TaskStatus.paused)
    audit(db, "search_task.pause", "search_task", str(task.id))
    emit(db, "task.paused", {"task_id": task.id})
    return task


def cancel_search_task(db: Session, task_id: UUID) -> SearchTask:
    task = db.get(SearchTask, task_id)
    if task is None:
        raise ValueError("Search task not found")
    if task.status not in {
        TaskStatus.draft,
        TaskStatus.queued,
        TaskStatus.running,
        TaskStatus.paused,
        TaskStatus.failed,
    }:
        raise ValueError("Only unfinished or failed tasks can be cancelled")
    transition_task(task, TaskStatus.cancelled)
    audit(db, "search_task.cancel", "search_task", str(task.id))
    emit(db, "task.cancelled", {"task_id": task.id})
    return task


def copy_search_task(db: Session, task_id: UUID) -> SearchTask:
    source = db.get(SearchTask, task_id)
    if source is None:
        raise ValueError("Search task not found")
    copied_filters = json.loads(json.dumps(source.filters or {}))
    if source.mode == "brand_discovery" and len(copied_filters.get("categories") or []) > 1:
        copied_filters["category_match_mode"] = "any"
    task = SearchTask(
        organization_id=source.organization_id,
        department_id=source.department_id,
        owner_id=source.owner_id,
        name=f"{source.name} (copy)",
        mode=source.mode,
        status=TaskStatus.draft,
        filters=copied_filters,
        progress={"brands": 0, "websites": 0, "contacts": 0, "emails": 0},
        budget_limit=source.budget_limit,
    )
    db.add(task)
    db.flush()
    audit(
        db,
        "search_task.copy",
        "search_task",
        str(task.id),
        after={"source_task_id": str(source.id)},
    )
    emit(db, "task.created", {"task_id": task.id, "source_task_id": source.id})
    return task


def retry_failed_task_items(db: Session, task_id: UUID) -> int:
    task = db.get(SearchTask, task_id)
    if task is None:
        raise ValueError("Search task not found")
    max_attempts = int(get_system_settings(db)["task_rules"]["max_attempts"])
    failed_items = db.scalars(
        select(TaskItem).where(
            TaskItem.task_id == task_id,
            TaskItem.status == TaskStatus.failed,
            TaskItem.attempts < max_attempts,
        )
    ).all()
    if not failed_items:
        raise ValueError("No retryable failed task items found")
    for item in failed_items:
        item.status = TaskStatus.queued
        item.attempts += 1
        item.error_code = None
        item.error_message = None
    transition_task(task, TaskStatus.queued)
    task.error_message = None
    audit(db, "task.retry", "search_task", str(task_id), after={"retried": len(failed_items)})
    return len(failed_items)


def execute_search_task(db: Session, task_id: UUID) -> SearchTask:
    task = db.get(SearchTask, task_id)
    if task is None:
        raise ValueError("Search task not found")
    if task.status in {TaskStatus.paused, TaskStatus.cancelled, TaskStatus.completed}:
        return task
    ensure_task_vendor_plan(db, task)
    transition_task(task, TaskStatus.running)
    # Persist execution immediately; Provider waterfalls can take several
    # minutes and must not look permanently queued in the UI meanwhile.
    db.commit()
    active_stage = None
    try:
        # ── Batch exact brand: execution is target-driven, skip normal pipeline ──
        if task.mode == "batch_exact_brand":
            # The schedule_batch_targets job picks up individual targets.
            # We just transition from queued → running so the UI reflects progress.
            transition_task(task, TaskStatus.running)
            emit(db, "task.running", {"task_id": task.id, "mode": "batch_exact_brand"})
            audit(db, "search_task.start", "search_task", str(task.id))
            return task

        if task.pipeline_version == PIPELINE_V2.pipeline_version:
            intent = SearchIntent.model_validate(task.search_intent)
            parsing_run = begin_stage(db, task.id, "intent_parsing", {"intent": task.search_intent})
            complete_stage(parsing_run, {"intent": task.search_intent, "validated": True})
            knowledge_run = begin_stage(
                db, task.id, "knowledge_resolution", {"knowledge": task.knowledge_snapshot}
            )
            complete_stage(
                knowledge_run,
                {
                    "sources": intent.knowledge_sources,
                    "precedence": ["user", "task", "reviewed", "ai", "default"],
                },
            )
            query_plans = plan_provider_queries(intent, {"supports_keyword_tags": True})
            planning_run = begin_stage(
                db, task.id, "provider_query_planning", {"intent": task.search_intent}
            )
            complete_stage(planning_run, {"queries": query_plans})
        if task.mode == "excel_import":
            raise ValueError("Excel import tasks must be started from the import page")
        if task.mode == "exact_brand" and not any(
            str(value).strip() for value in task.filters.get("official_domains", [])
        ):
            raise ValueError(
                "精准品牌任务必须填写已确认的官方官网或域名（例如 mango.com），"
                "系统会用它排除同名公司，避免把错误域名的联系人和邮箱写入品牌。"
            )
        plan = ensure_task_vendor_plan(db, task)
        if not plan.selected_vendors or plan.execution_mode not in {
            "apollo_only",
            "hunter_only",
            "apollo_hunter",
        }:
            raise ValueError(
                "Legacy Vendor routing is no longer supported. Copy the task and select its Vendors."
            )

        # The only supported runtime path is the task-frozen full Vendor pipeline.
        if plan.selected_vendors:
            from app.pipeline.vendor_pipeline import execute_pipeline_mode

            companies, company_errors, primary_name = execute_pipeline_mode(db, task, plan)
            if not companies:
                detail = (
                    "; ".join(company_errors)
                    or "No enabled vendor returned matching companies"
                )
                raise ValueError(
                    f"所有选中的来源均未找到匹配的公司。详情：{detail}"
                )
            active_stage = begin_stage(db, task.id, "provider_search", task.filters)
            complete_stage(
                active_stage,
                {"provider": primary_name or "pipeline", "candidate_count": len(companies)},
            )
            if task.pipeline_version == PIPELINE_V2.pipeline_version:
                normalization_run = begin_stage(
                    db, task.id, "candidate_normalization",
                    {"candidate_count": len(companies)},
                )
                complete_stage(normalization_run, {"candidate_count": len(companies)})
            active_stage = begin_stage(
                db, task.id, "candidate_filtering",
                {"candidate_count": len(companies), "filters": task.filters},
            )
            complete_stage(
                active_stage,
                {
                    "accepted_count": len(companies),
                    "rejected_count": 0,
                    "filtering_owner": "vendor_pipeline",
                },
            )
            active_stage = None
            # Pipeline mode handles its own brand/contact/email ingestion.
            # Skip the remaining brand_discovery/exact_brand paths below.
            task.progress = _task_progress(db, task)
            db.flush()
            db.refresh(task)
            if task.status in {TaskStatus.paused, TaskStatus.cancelled}:
                audit(db, f"search_task.{task.status}", "search_task", str(task.id), after=task.progress)
                return task
            transition_task(task, TaskStatus.completed)
            task.error_message = None
            emit(db, "task.completed", {"task_id": task.id, "progress": task.progress})
            audit(db, "search_task.complete", "search_task", str(task.id), after=task.progress)
            return task
        elif (approved_candidate := _candidate_for_task(db, task)) is not None:
            if approved_candidate.status in {"pending", "enrichment_failed", "review", "qualified"}:
                transition_candidate(
                    db,
                    approved_candidate,
                    "enriching",
                    TransitionContext(
                        idempotency_key=f"execute-enrichment:{approved_candidate.id}:{task.id}"
                    ),
                    exact_task_id=task.id,
                )
            company_provider = _candidate_source_provider(db, approved_candidate)
            if company_provider is None:
                raise ValueError("候选品牌的来源 Vendor 无法识别，无法继续精准丰富")
            companies = [_company_payload_from_candidate(approved_candidate)]
        else:
            # ── Slice-based execution path ──────────────────────────────────
            locked_plan = None
            if task.active_query_plan_id:
                locked_plan = db.scalar(
                    select(SearchQueryPlan).where(
                        SearchQueryPlan.id == task.active_query_plan_id,
                        SearchQueryPlan.status == "locked",
                    )
                )
            if locked_plan is not None:
                # Execute via slices — one page per call, respecting stop conditions
                from app.query_planning.scheduler import execute_slice_page as exec_slice_page
                plan_id_str = str(locked_plan.id)
                enabled_slices = db.scalars(
                    select(SearchQuerySlice)
                    .where(
                        SearchQuerySlice.plan_id == plan_id_str,
                        SearchQuerySlice.enabled.is_(True),
                    )
                    .order_by(SearchQuerySlice.priority)
                ).all()
                if not enabled_slices:
                    raise ValueError("Locked plan has no enabled slices")
                # Collect vendor plan for provider routing
                vendor_plan = db.scalar(
                    select(TaskVendorPlan).where(TaskVendorPlan.task_id == task.id)
                )
                providers = (
                    [vendor_plan.primary_vendor, *vendor_plan.fallback_vendors]
                    if vendor_plan
                    else ["hunter"]
                )
                company_errors: list[str] = []
                provider_call_count = 0
                for sl in enabled_slices:
                    if task.status in {TaskStatus.cancelled, TaskStatus.paused}:
                        break
                    # Check stop conditions before each slice
                    qualified = _task_result_counts(db, task.id).get("brands", 0)
                    if qualified >= (locked_plan.target_result_count or 100):
                        break
                    if locked_plan.max_provider_calls and provider_call_count >= locked_plan.max_provider_calls:
                        break
                    for provider in providers:
                        if task.status in {TaskStatus.cancelled, TaskStatus.paused}:
                            break
                        try:
                            result = exec_slice_page(
                                db, task.id, UUID(sl.id), provider, cursor=None
                            )
                            if result.get("status") == "skipped":
                                continue
                            if result.get("error"):
                                company_errors.append(f"Slice {sl.slice_key} @ {provider}: {result['error']}")
                            else:
                                provider_call_count += 1
                        except Exception as exc:
                            company_errors.append(f"Slice {sl.slice_key} @ {provider}: {exc}")
                company_provider_name = providers[0] if providers else ""
                # Fall back to waterfall results for ingestion compatibility
                if provider_call_count == 0:
                    from app.modules.models import DiscoveryCandidate as DC
                    raw_companies = db.scalars(
                        select(DC).where(
                            DC.last_task_id == task.id,
                            DC.status == "pending",
                        )
                    ).all()
                    companies_list = [
                        {
                            "brand_name": dc.name, "domain": dc.domain,
                            "website": dc.website, "country": dc.country,
                            "category": dc.industry, "provider": dc.provider,
                        }
                        for dc in raw_companies
                    ]
                active_stage = begin_stage(db, task.id, "provider_search", task.filters)
                companies = companies_list
                company_provider = None  # no single ProviderConfig object; use name string
                # Reconstruct for complete_stage
                if companies:
                    complete_stage(active_stage, {"provider": company_provider_name, "candidate_count": len(companies)})
                else:
                    complete_stage(active_stage, {"provider": company_provider_name, "candidate_count": 0})
                active_stage = None  # prevent double-complete below
            else:
                # ── Legacy path (no locked plan) ────────────────────────────
                active_stage = begin_stage(db, task.id, "provider_search", task.filters)
                company_provider, companies, company_errors = execute_provider_waterfall(
                    db,
                    "company_search",
                    task.filters,
                    "companies",
                    task=task,
                    entity_type="company",
                    item_filter=(
                        lambda items: (
                            filter_discovery_companies(items, task.filters)
                            if task.mode == "brand_discovery"
                            else items
                        )
                    ),
                )
            if company_provider is None:
                detail = (
                    "; ".join(company_errors) or "No enabled company_search Provider is configured"
                )
                if task.mode == "brand_discovery" and task.filters.get("countries"):
                    targets = "、".join(str(value) for value in task.filters["countries"])
                    raise ValueError(
                        f"未找到同时满足品牌归属国家（{targets}）和目标品类的品牌。"
                        "仅在目标国家销售或运营的品牌不会被收录；请检查 Provider 是否返回了"
                        " headquarters_country / registered_country / origin_country，"
                        f"并确认品类字段映射正确。详情：{detail}"
                    )
                if task.mode == "brand_discovery":
                    raise ValueError(
                        "暂未找到符合当前条件的品牌。请检查品类拼写，或适当降低相关度；"
                        "如果要查找指定品牌，请使用“精准品牌”模式。"
                    )
                raise ValueError(f"All company_search Providers were unavailable: {detail}")
            complete_stage(
                active_stage,
                {"provider": company_provider.provider, "candidate_count": len(companies)},
            )
            if task.pipeline_version == PIPELINE_V2.pipeline_version:
                normalization_run = begin_stage(
                    db, task.id, "candidate_normalization", {"candidate_count": len(companies)}
                )
                complete_stage(normalization_run, {"candidate_count": len(companies)})
            active_stage = begin_stage(
                db,
                task.id,
                "candidate_filtering",
                {"candidate_count": len(companies), "filters": task.filters},
            )
        if task.mode == "brand_discovery":
            # The waterfall already applied strict country/category validation per Provider.
            companies = filter_discovery_companies(companies, task.filters)
            if not companies:
                raise ValueError(
                    "暂未找到符合当前条件的品牌。请检查品类拼写，或适当降低相关度；"
                    "如果要查找指定品牌，请使用“精准品牌”模式。"
                )
            complete_stage(active_stage, {"accepted_count": len(companies)})
            active_stage = begin_stage(
                db, task.id, "result_classification", {"candidate_count": len(companies)}
            )
            finished = _ingest_discovery_candidates(
                db,
                task,
                company_provider,
                companies,
                result_limit=int(task.filters.get("brand_limit") or 100),
            )
            if company_errors:
                task.progress = {
                    **(task.progress or {}),
                    "provider_warnings": company_errors,
                    "partial_failure_count": len(company_errors),
                }
            complete_stage(active_stage, {"finished": finished, "candidate_count": len(companies)})
        elif task.mode == "exact_brand":
            companies = filter_exact_brand_companies(companies, task.filters)
            if not companies:
                raise ValueError(
                    "未找到同时匹配品牌名称和官方域名的公司；请核对官网域名，"
                    "并避免把目标市场国家误填为品牌总部国家。"
                )
            if active_stage is not None and active_stage.status != "completed":
                complete_stage(active_stage, {"accepted_count": len(companies)})
            active_stage = begin_stage(
                db, task.id, "contact_enrichment", {"company_count": len(companies)}
            )
            finished = _ingest_discovery(
                db,
                task,
                company_provider,
                companies[: int(task.filters.get("brand_limit") or 100)],
            )
            complete_stage(active_stage, {"finished": finished, "company_count": len(companies)})
        else:
            finished = False
        # SessionLocal disables autoflush. Keep the progress written by the
        # ingestion stage before refreshing task state from the database.
        db.flush()
        db.refresh(task)
        if not finished or task.status in {TaskStatus.paused, TaskStatus.cancelled}:
            audit(
                db, f"search_task.{task.status}", "search_task", str(task.id), after=task.progress
            )
            return task
        transition_task(task, TaskStatus.completed)
        task.error_message = None
        emit(db, "task.completed", {"task_id": task.id, "progress": task.progress})
        audit(db, "search_task.complete", "search_task", str(task.id), after=task.progress)
    except Exception as exc:
        if active_stage is not None and active_stage.status == "running":
            fail_stage(active_stage, exc)
        db.refresh(task)
        if task.status in {TaskStatus.paused, TaskStatus.cancelled}:
            audit(
                db, f"search_task.{task.status}", "search_task", str(task.id), after=task.progress
            )
            return task
        transition_task(task, TaskStatus.failed)
        task.error_message = str(exc)[:2000]
        task.progress = _task_progress(db, task)
        _mark_candidate_enrichment_failed(db, task)
        emit(db, "task.failed", {"task_id": task.id, "error": task.error_message})
        audit(
            db, "search_task.fail", "search_task", str(task.id), after={"error": task.error_message}
        )
    return task


def _title_matches_targets(title: str, target_titles: list[str]) -> bool:
    if not target_titles:
        return True
    normalized_title = " ".join(re.findall(r"[^\W_]+", title.casefold()))
    normalized_targets = [
        " ".join(re.findall(r"[^\W_]+", target.casefold()))
        for target in target_titles
        if target.strip()
    ]
    return bool(normalized_title) and any(
        target in normalized_title for target in normalized_targets if target
    )


def _usable_contact_items(
    items: list[dict],
    target_titles: list[str] | None = None,
    *,
    allow_provider_id: bool = False,
) -> list[dict]:
    targets = target_titles or []
    return [
        item
        for item in items
        if str(item.get("first_name") or "").strip()
        and (
            str(item.get("last_name") or "").strip()
            or (allow_provider_id and str(item.get("provider_person_id") or "").strip())
        )
        and str(item.get("title") or "").strip()
        and _title_matches_targets(str(item.get("title") or ""), targets)
    ]


def _ingest_discovery(
    db: Session, task: SearchTask, company_provider: ProviderConfig, companies: list[dict]
) -> bool:
    source_type = _provider_source_type(company_provider)
    for company_payload in companies:
        if not _task_is_running(db, task):
            task.progress = _task_progress(db, task)
            return False
        brand_name = str(
            company_payload.get("brand_name") or company_payload.get("name") or ""
        ).strip()
        if not brand_name:
            continue
        _resolve_company_domain(db, task, company_payload)
        company = get_or_create_company(db, company_payload)
        brand = create_brand(
            db,
            BrandCreate(
                name=brand_name,
                company_name=company.legal_name,
                website=company_payload.get("website") or company_payload.get("url"),
                country=company_payload.get("country"),
                category=company_payload.get("category"),
            ),
            company=company,
            source_type=source_type,
            provider=company_provider.provider,
            source_url=company_payload.get("source_url"),
            source_title=company_payload.get("source_title"),
            source_excerpt=company_payload.get("source_excerpt"),
            discovery_score=int(company_payload.get("relevance_score") or 0),
            organization_id=task.organization_id,
            organization_unit_id=task.department_id,
            owner_id=task.owner_id,
        )
        db.add(
            TaskItem(
                task_id=task.id,
                entity_type="brand",
                entity_id=str(brand.id),
                stage="brand_discovered",
                status=TaskStatus.completed,
                provider=company_provider.provider,
            )
        )

        candidate = _candidate_for_task(db, task)
        if candidate is not None:
            transition_candidate(
                db,
                candidate,
                "promoted",
                TransitionContext(idempotency_key=f"promote:{candidate.id}:{brand.id}"),
                promoted_brand_id=brand.id,
            )

        # ── Website parsing ──────────────────────────────────────────
        if brand.primary_website:
            _parse_brand_website(db, task, brand)

        target_titles = [
            str(value).strip()
            for value in task.filters.get("target_titles", [])
            if str(value).strip()
        ]
        contact_provider, contacts, _ = execute_provider_waterfall(
            db,
            "contact_search",
            {
                "company": company_payload,
                "brand": to_dict(brand),
                "titles": task.filters.get("target_titles") or [],
                "limit": task.filters.get("contacts_limit_per_brand") or 5,
            },
            "contacts",
            task=task,
            entity_type="contact",
            item_filter=lambda items: _usable_contact_items(
                items,
                target_titles,
                allow_provider_id=True,
            ),
        )
        usable_contacts = contacts
        if contact_provider is None or not usable_contacts:
            _discover_emails_by_domain(db, task, company, brand)
            continue
        usable_contacts = _usable_contact_items(
            _enrich_contacts_with_apollo(
                db, task, contact_provider, company_payload, usable_contacts
            ),
            target_titles,
        )
        for contact_payload in usable_contacts[
            : int(task.filters.get("contacts_limit_per_brand") or 5)
        ]:
            if not _task_is_running(db, task):
                task.progress = _task_progress(db, task)
                return False
            first_name = str(contact_payload.get("first_name") or "").strip()
            title = str(contact_payload.get("title") or "").strip()
            if not first_name or not title:
                continue
            contact = create_contact(
                db,
                ContactCreate(
                    brand_id=brand.id,
                    company_id=company.id,
                    first_name=first_name,
                    last_name=str(contact_payload.get("last_name") or ""),
                    title=title,
                    linkedin_url=contact_payload.get("linkedin_url"),
                ),
                provider=contact_provider.provider,
                organization_id=task.organization_id,
                organization_unit_id=task.department_id,
                owner_id=task.owner_id,
            )
            _record_task_result(
                db,
                task,
                "contact",
                contact.id,
                "contact_discovered",
                contact_provider.provider,
            )

            # ── Email discovery (with inference fallback) ────────────
            emails_found = False
            enriched_addresses = (
                contact_payload.get("emails")
                if isinstance(contact_payload.get("emails"), list)
                else []
            )
            for address in _valid_email_addresses(
                [contact_payload.get("email"), *enriched_addresses]
            ):
                email = create_email(
                    db,
                    EmailCreate(contact_id=contact.id, address=address, type="personal"),
                    provider=contact_provider.provider,
                    organization_id=task.organization_id,
                    organization_unit_id=task.department_id,
                    owner_id=task.owner_id,
                )
                _record_task_result(
                    db, task, "email", email.id, "email_enriched", contact_provider.provider
                )
                emails_found = True

            if not emails_found:
                email_provider, email_payloads, _ = execute_provider_waterfall(
                    db,
                    "email_finder",
                    {"contact": contact_payload, "domain": company.domain, "brand": to_dict(brand)},
                    "emails",
                    task=task,
                    entity_type="email",
                )
                if email_provider is not None:
                    for email_payload in email_payloads:
                        for address in _valid_email_addresses(
                            [email_payload.get("address"), email_payload.get("email")]
                        ):
                            email = create_email(
                                db,
                                EmailCreate(
                                    contact_id=contact.id,
                                    address=address,
                                    type=str(email_payload.get("type") or "personal"),
                                ),
                                provider=email_provider.provider,
                                organization_id=task.organization_id,
                                organization_unit_id=task.department_id,
                                owner_id=task.owner_id,
                            )
                            _record_task_result(
                                db,
                                task,
                                "email",
                                email.id,
                                "email_discovered",
                                email_provider.provider,
                            )
                            emails_found = True

            # Fallback: pattern inference when provider unavailable or found nothing
            if not emails_found and company.domain:
                last_name = str(contact_payload.get("last_name") or "")
                if last_name:
                    candidates = infer_emails(
                        first_name, last_name, company.domain, min_confidence=40
                    )
                    for candidate in candidates:
                        email = create_email(
                            db,
                            EmailCreate(
                                contact_id=contact.id,
                                address=candidate["address"],
                                type="personal",
                            ),
                            provider="pattern_inference",
                            organization_id=task.organization_id,
                            organization_unit_id=task.department_id,
                            owner_id=task.owner_id,
                        )
                        db.add(
                            SourceEvidence(
                                entity_type="email",
                                entity_id=str(email.id),
                                source_type="pattern_inference",
                                title=f"Inferred via {candidate['pattern']}",
                                confidence=candidate["confidence"],
                                provider="pattern_inference",
                            )
                        )
                        _record_task_result(
                            db,
                            task,
                            "email",
                            email.id,
                            "email_inferred",
                            "pattern_inference",
                        )
                        verify_email(db, email.id, task=task)
                        emails_found = True

            # Verify emails that were created but not yet verified
            if emails_found:
                _verify_unverified_emails(db, contact.id, task=task)
    task.progress = _task_progress(db, task)
    return True


def _valid_email_addresses(values: list[object]) -> list[str]:
    """Normalize Provider emails and discard null placeholders or malformed values."""
    addresses: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.casefold() in {"none", "null", "undefined", "n/a"}:
            continue
        try:
            normalized = validate_email(text, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            continue
        if normalized not in addresses:
            addresses.append(normalized)
    return addresses


def _resolve_company_domain(db: Session, task: SearchTask, company_payload: dict) -> None:
    """Fill a missing domain through the configured Hunter Domain Finder endpoint."""
    if domain_from_url(
        company_payload.get("domain")
        or company_payload.get("website")
        or company_payload.get("url")
    ):
        return
    company_name = str(
        company_payload.get("brand_name") or company_payload.get("name") or ""
    ).strip()
    if not company_name:
        return
    for provider in enabled_providers(db, "company_search", task):
        config = decrypt_provider_config(provider.config or {})
        if (
            str(config.get("adapter") or "").lower() != "hunter"
            or not str(config.get("domain_finder_endpoint_url") or "").strip()
        ):
            continue
        quota_check = provider_quota_available(provider, config)
        if quota_check is not None and not quota_check.ok:
            _record_provider_fallback(
                db, task, "company", provider, quota_check.error_message or "quota check failed"
            )
            continue
        result = execute_provider(
            provider, {"operation": "domain_finder", "company_name": company_name}
        )
        record_usage(db, provider, result.cost)
        if not result.ok:
            _record_provider_fallback(
                db, task, "company", provider, result.error_message or "domain lookup failed"
            )
            continue
        for item in result.data.get("companies", []):
            domain = domain_from_url(item.get("domain") or item.get("website"))
            if domain:
                company_payload["domain"] = domain
                company_payload.setdefault("website", item.get("website") or f"https://{domain}")
                return


def _enrich_contacts_with_apollo(
    db: Session,
    task: SearchTask,
    provider: ProviderConfig,
    company_payload: dict,
    contacts: list[dict],
) -> list[dict]:
    """Add Apollo bulk-enrichment fields without replacing usable search results."""
    config = decrypt_provider_config(provider.config or {})
    if (
        str(config.get("adapter") or "").lower() != "apollo"
        or not str(config.get("bulk_enrichment_endpoint_url") or "").strip()
    ):
        return contacts
    checkpoint = _stage_checkpoint(
        db,
        task,
        "contact_enrichment",
        _checkpoint_scope({"company": company_payload, "contacts": contacts}),
        provider,
    )
    if checkpoint is not None and checkpoint.status == "completed":
        cached = (
            checkpoint.normalized_output.get("contacts")
            if isinstance(checkpoint.normalized_output, dict)
            else None
        )
        if isinstance(cached, list):
            return cached
    _start_checkpoint(checkpoint)
    quota_check = provider_quota_available(provider, config)
    if quota_check is not None and not quota_check.ok:
        message = quota_check.error_message or "quota check failed"
        _fail_checkpoint(checkpoint, quota_check.error_code, message)
        _record_provider_fallback(db, task, "contact", provider, message)
        return contacts
    result = execute_provider(
        provider, {"operation": "bulk_enrich", "company": company_payload, "contacts": contacts}
    )
    record_usage(db, provider, result.cost)
    if not result.ok:
        message = result.error_message or "bulk enrichment failed"
        _fail_checkpoint(checkpoint, result.error_code, message)
        _record_provider_fallback(db, task, "contact", provider, message)
        return contacts
    enriched_by_id = {
        str(item.get("provider_person_id") or "").strip(): item
        for item in result.data.get("contacts", [])
        if str(item.get("provider_person_id") or "").strip()
    }
    enriched_by_name = {
        (
            str(item.get("first_name") or "").strip().casefold(),
            str(item.get("last_name") or "").strip().casefold(),
        ): item
        for item in result.data.get("contacts", [])
        if str(item.get("first_name") or "").strip()
    }
    enriched = [
        contact
        | {
            key: value
            for key, value in (
                enriched_by_id.get(str(contact.get("provider_person_id") or "").strip())
                or enriched_by_name.get(
                    (
                        str(contact.get("first_name") or "").strip().casefold(),
                        str(contact.get("last_name") or "").strip().casefold(),
                    ),
                    {},
                )
            ).items()
            if value not in (None, "", [])
        }
        for contact in contacts
    ]
    _complete_checkpoint(checkpoint, "contacts", enriched)
    return enriched


def _ingest_discovery_candidates(
    db: Session,
    task: SearchTask,
    provider: ProviderConfig,
    companies: list[dict],
    *,
    result_limit: int | None = None,
) -> bool:
    stats = {
        "discovered": 0,
        "new_candidates": 0,
        "refreshed_candidates": 0,
        "excluded_customers": 0,
        "excluded_blacklist": 0,
        "excluded_rejected": 0,
    }
    accepted = 0
    for payload in companies:
        if result_limit is not None and accepted >= max(result_limit, 0):
            break
        stats["discovered"] += 1
        if not _task_is_running(db, task):
            task.progress = {**_task_result_counts(db, task.id), **stats}
            return False
        name = str(payload.get("brand_name") or payload.get("name") or "").strip()
        if not name:
            continue
        domain = domain_from_url(
            payload.get("domain") or payload.get("website") or payload.get("url")
        )
        normalized_name = slugify(name)
        country = str(payload.get("country") or "").strip() or None
        dedupe_key = (
            f"domain:{domain}"
            if domain
            else f"name:{normalized_name}|country:{str(country or '').casefold()}"
        )
        if domain and db.scalar(
            select(Blacklist.id).where(
                Blacklist.type == "domain",
                func.lower(Blacklist.value) == domain,
            )
        ):
            stats["excluded_blacklist"] += 1
            continue
        if _candidate_matches_customer(db, normalized_name, domain, country):
            stats["excluded_customers"] += 1
            continue

        candidate = db.scalar(
            select(DiscoveryCandidate).where(DiscoveryCandidate.dedupe_key == dedupe_key)
        )
        now = utc_now()
        if candidate is None:
            raw_data = _discovery_candidate_raw_data(payload)
            candidate = DiscoveryCandidate(
                name=name,
                normalized_name=normalized_name,
                domain=domain,
                normalized_domain=domain,
                dedupe_key=dedupe_key,
                website=payload.get("website") or (f"https://{domain}" if domain else None),
                country=country,
                industry=payload.get("category"),
                emails_count=int(payload.get("emails_count") or 0),
                relevance_score=0,
                provider=provider.provider,
                raw_data=jsonable_encoder(raw_data),
                status="pending",
                seen_count=1,
                first_seen_at=now,
                last_seen_at=now,
                last_task_id=task.id,
            )
            db.add(candidate)
            db.flush()
            stats["new_candidates"] += 1
        elif candidate.status in {"rejected", "promoted"}:
            stats[
                "excluded_rejected" if candidate.status == "rejected" else "excluded_customers"
            ] += 1
            continue
        else:
            candidate.last_seen_at = now
            candidate.last_task_id = task.id
            candidate.seen_count += 1
            if not candidate.industry_source:
                candidate.relevance_score = 0
            candidate.emails_count = max(
                int(candidate.emails_count or 0), int(payload.get("emails_count") or 0)
            )
            # Refresh from actual Provider data. Do not preserve a historical
            # category that may have been copied from an earlier search input.
            if not candidate.industry_source:
                candidate.industry = payload.get("category") or None
            candidate.raw_data = jsonable_encoder(_discovery_candidate_raw_data(payload))
            stats["refreshed_candidates"] += 1

        hit = db.scalar(
            select(DiscoveryCandidateHit).where(
                DiscoveryCandidateHit.candidate_id == candidate.id,
                DiscoveryCandidateHit.task_id == task.id,
            )
        )
        if hit is None:
            db.add(
                DiscoveryCandidateHit(
                    candidate_id=candidate.id,
                    task_id=task.id,
                    relevance_score=0,
                    provider=provider.provider,
                )
            )
        db.add(
            TaskItem(
                task_id=task.id,
                entity_type="discovery_candidate",
                entity_id=str(candidate.id),
                stage="candidate_discovered",
                status=TaskStatus.completed,
                provider=provider.provider,
            )
        )
        accepted += 1
    task.progress = {**_task_result_counts(db, task.id), **stats}
    audit(db, "discovery.candidates_ingested", "search_task", str(task.id), after=stats)
    return True


def _discovery_candidate_raw_data(payload: dict) -> dict:
    raw_data = payload.get("raw_data") if isinstance(payload.get("raw_data"), dict) else payload
    if raw_data is payload:
        return raw_data
    return {
        **raw_data,
        "_buyerreach_country_evidence": {
            "country": payload.get("country"),
            "headquarters_country": payload.get("headquarters_country"),
            "registered_country": payload.get("registered_country"),
            "origin_country": payload.get("origin_country"),
            "country_scope": payload.get("country_scope"),
            "country_evidence": payload.get("country_evidence"),
            "provider_filters": payload.get("provider_filters"),
        },
    }


def _candidate_matches_customer(
    db: Session,
    normalized_name: str,
    domain: str | None,
    country: str | None,
) -> bool:
    if domain:
        company_match = db.scalar(
            select(Company.id)
            .join(Brand, Brand.company_id == Company.id)
            .where(
                Company.deleted_at.is_(None),
                Brand.deleted_at.is_(None),
                Brand.status.notin_({"pending_review", "migrated_candidate"}),
                func.lower(Company.domain) == domain,
            )
        )
        if company_match is not None:
            return True
        website_match = db.scalar(
            select(Website.id)
            .join(Brand, Brand.id == Website.brand_id)
            .where(
                Website.deleted_at.is_(None),
                Brand.deleted_at.is_(None),
                Brand.status.notin_({"pending_review", "migrated_candidate"}),
                func.lower(Website.domain) == domain,
            )
        )
        if website_match is not None:
            return True
    statement = select(Brand.id).where(
        Brand.deleted_at.is_(None),
        Brand.status.notin_({"pending_review", "migrated_candidate"}),
        Brand.normalized_name == normalized_name,
    )
    if country:
        statement = statement.where(func.lower(Brand.country) == country.casefold())
    return db.scalar(statement) is not None


def list_discovery_candidates(
    db: Session,
    page: int,
    page_size: int,
    candidate_status: str | None = None,
) -> dict:
    from app.modules.discovery_review import list_candidates

    return list_candidates(db, page, page_size, status=candidate_status)


def queue_candidate_industry_enrichment(db: Session, candidate: DiscoveryCandidate) -> None:
    if not candidate.domain and not candidate.website:
        raise ValueError("Candidate has no official website or domain")
    if candidate.industry_enrichment_status in {"queued", "running"}:
        raise ValueError("Candidate industry enrichment is already queued or running")
    candidate.industry_enrichment_status = "queued"
    candidate.industry_enrichment_error = None
    audit(db, "discovery.industry_enrichment_queued", "discovery_candidate", str(candidate.id))


def enrich_candidate_industry(db: Session, candidate_id: UUID) -> dict:
    """Website-first industry enrichment with Hunter and AI fallbacks."""
    from app.modules.industry_enrichment import standardize_industry
    from app.modules.website_parser import parse_website

    candidate = db.scalar(
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.id == candidate_id)
        .with_for_update(skip_locked=True)
    )
    if candidate is None:
        return {"candidate_id": str(candidate_id), "industry_enrichment_status": "skipped"}
    if (
        candidate.industry_enrichment_status == "completed"
        and candidate.industry
        and candidate.evaluation_status == "completed"
    ):
        return to_dict(candidate)
    candidate.industry_enrichment_status = "running"
    candidate.industry_enrichment_error = None
    # Publish the running state before slow website/AI/Provider calls so the UI
    # does not keep showing a task as queued for the entire transaction.
    db.commit()
    errors: list[str] = []
    snapshot_task = db.get(SearchTask, candidate.last_task_id) if candidate.last_task_id else None
    ai_settings = get_ai_settings(db, snapshot_task)
    website_url = candidate.website or (f"https://{candidate.domain}" if candidate.domain else "")

    if website_url:
        website_result = parse_website(website_url, timeout=8, max_pages=2)
        if not website_result.error and len(str(website_result.text_snippet or "").strip()) >= 120:
            website_evidence = {
                "source": "official_website",
                "company_name": candidate.name,
                "url": website_result.url,
                "page_title": website_result.page_title,
                "website_text": website_result.text_snippet[:4000],
            }
            try:
                classified = standardize_industry(website_evidence, ai_settings)
            except Exception as exc:
                classified = None
                errors.append(f"AI website classification: {str(exc)[:300]}")
            if classified is not None:
                return _save_candidate_industry(
                    db,
                    candidate,
                    classified,
                    "official_website_ai",
                    website_evidence,
                    website_result.url,
                    ai_settings,
                )
        elif website_result.error:
            errors.append(f"Official website: {website_result.error[:300]}")
        else:
            errors.append("Official website did not provide enough descriptive text")

    hunter = next(
        (
            provider
            for provider in enabled_providers(db, "company_search", snapshot_task)
            if str((provider.config or {}).get("adapter") or "").casefold() == "hunter"
        ),
        None,
    )
    if hunter is not None and candidate.domain:
        quota = provider_quota_available(
            hunter,
            decrypt_provider_config(hunter.config or {}),
            {"operation": "company_enrichment"},
        )
        if quota is None or quota.ok:
            result = execute_provider(
                hunter, {"operation": "company_enrichment", "domain": candidate.domain}
            )
            record_usage(db, hunter, result.cost)
            company = (
                result.data.get("company")
                if result.ok and isinstance(result.data.get("company"), dict)
                else None
            )
            if company:
                hunter_evidence = _hunter_industry_evidence(candidate, company)
                try:
                    classified = standardize_industry(hunter_evidence, ai_settings)
                except Exception as exc:
                    classified = None
                    errors.append(f"AI Hunter classification: {str(exc)[:300]}")
                if classified is None:
                    classified = _industry_from_hunter_company(company)
                if classified is not None:
                    return _save_candidate_industry(
                        db,
                        candidate,
                        classified,
                        "hunter_company_enrichment" + ("_ai" if ai_settings.get("enabled") else ""),
                        hunter_evidence,
                        None,
                        ai_settings,
                    )
                errors.append(
                    "Hunter Company Enrichment returned no usable category, description, or tags"
                )
            else:
                errors.append(
                    f"Hunter Company Enrichment: {result.error_message or result.error_code or 'no data'}"
                )
        else:
            errors.append(f"Hunter quota unavailable: {quota.error_message or quota.error_code}")
    else:
        errors.append("Enabled Hunter Company Enrichment is unavailable")

    candidate.industry_enrichment_status = "failed"
    candidate.industry_enrichment_error = "; ".join(errors)[:2000]
    candidate.industry_enriched_at = utc_now()
    audit(
        db,
        "discovery.industry_enrichment_failed",
        "discovery_candidate",
        str(candidate.id),
        after={"errors": errors},
    )
    return to_dict(candidate)


def _hunter_industry_evidence(candidate: DiscoveryCandidate, company: dict) -> dict:
    return {
        "source": "hunter_company_enrichment",
        "company_name": company.get("name") or candidate.name,
        "domain": candidate.domain,
        "category": company.get("category"),
        "description": company.get("description"),
        "tags": company.get("tags"),
        "location": company.get("location"),
    }


def _industry_from_hunter_company(company: dict) -> dict | None:
    category = company.get("category") if isinstance(company.get("category"), dict) else {}
    industry = str(
        category.get("subIndustry")
        or category.get("industry")
        or category.get("industryGroup")
        or category.get("sector")
        or ""
    ).strip()
    tags = (
        [str(item).strip() for item in company.get("tags", []) if str(item).strip()]
        if isinstance(company.get("tags"), list)
        else []
    )
    if not industry and not tags:
        return None
    return {
        "standard_industry": (industry or tags[0])[:255],
        "subcategories": tags[:20],
        "confidence": 80 if industry else 65,
        "summary": str(company.get("description") or "")[:1000],
        "evidence_terms": ([industry] if industry else []) + tags[:10],
    }


def _save_candidate_industry(
    db: Session,
    candidate: DiscoveryCandidate,
    classified: dict,
    source: str,
    evidence: dict,
    evidence_url: str | None,
    ai_settings: dict,
) -> dict:
    candidate.industry = str(classified["standard_industry"])[:255]
    candidate.industry_source = source
    candidate.industry_confidence = int(classified.get("confidence") or 0)
    candidate.industry_details = {**classified, "source_evidence": evidence}
    candidate.industry_enrichment_status = "completed"
    candidate.industry_enrichment_error = None
    candidate.industry_enriched_at = utc_now()
    _rescore_enriched_candidate(db, candidate, ai_settings)
    db.add(
        SourceEvidence(
            entity_type="discovery_candidate",
            entity_id=str(candidate.id),
            source_type=(
                SourceType.official_website
                if source.startswith("official_website")
                else SourceType.commercial_api
            ),
            url=evidence_url,
            title=f"Industry enrichment for {candidate.name}",
            excerpt=str(classified.get("summary") or candidate.industry)[:500],
            confidence=candidate.industry_confidence,
            provider=(
                "website_parser+ai"
                if source.startswith("official_website")
                else "hunter_company_enrichment"
            ),
        )
    )
    audit(
        db,
        "discovery.industry_enriched",
        "discovery_candidate",
        str(candidate.id),
        after={
            "industry": candidate.industry,
            "source": source,
            "confidence": candidate.industry_confidence,
        },
    )
    _record_candidate_pipeline(db, candidate, source, evidence)
    db.flush()
    return to_dict(candidate)


def _record_candidate_pipeline(
    db: Session, candidate: DiscoveryCandidate, source: str, evidence: dict
) -> None:
    if candidate.last_task_id is None:
        return
    payload = {"candidate_id": str(candidate.id), "evidence_source": source}
    website_run = begin_stage(db, candidate.last_task_id, "website_evidence", payload, candidate.id)
    complete_stage(
        website_run,
        {"available": source.startswith("official_website"), "evidence": evidence},
    )
    industry_run = begin_stage(
        db, candidate.last_task_id, "industry_enrichment", payload, candidate.id
    )
    complete_stage(
        industry_run,
        {
            "industry": candidate.industry,
            "confidence": candidate.industry_confidence,
            "source": source,
        },
    )
    ai_run = begin_stage(db, candidate.last_task_id, "ai_relevance_scoring", payload, candidate.id)
    complete_stage(
        ai_run,
        {
            "evaluated": source.endswith("_ai"),
            "dimensions": (candidate.industry_details or {}).get("dimensions", {}),
            "status": "completed" if source.endswith("_ai") else "insufficient_data",
        },
    )
    validation_run = begin_stage(
        db, candidate.last_task_id, "rule_validation", payload, candidate.id
    )
    complete_stage(
        validation_run,
        {
            "target_relevance_score": candidate.target_relevance_score,
            "evaluation_status": candidate.evaluation_status,
            "policy_version": (
                PIPELINE_V2.scoring_policy_version
                if db.get(SearchTask, candidate.last_task_id).pipeline_version == "2.0.0"
                else PIPELINE_V1.scoring_policy_version
            ),
        },
    )


def _rescore_enriched_candidate(
    db: Session, candidate: DiscoveryCandidate, ai_settings: dict
) -> None:
    """Replace the provisional discovery score with company-level evidence."""
    task = db.get(SearchTask, candidate.last_task_id) if candidate.last_task_id else None
    if task is None:
        return
    if task.pipeline_version == PIPELINE_V2.pipeline_version:
        from app.modules.industry_enrichment import match_company_concepts
        from app.pipeline.matching import evaluate_matches

        details = candidate.industry_details or {}
        evidence = [
            {
                "field": "company_profile",
                "value": ", ".join(
                    [
                        *details.get("products", []),
                        *details.get("services", []),
                        candidate.industry or "",
                    ]
                ),
                "url": candidate.website,
                "excerpt": details.get("summary"),
                "source_type": candidate.industry_source or "unknown",
                "confidence": int(candidate.industry_confidence or 0),
            }
        ]
        candidate.company_profile = {
            "schema_version": "2.0.0",
            "industry": candidate.industry,
            "industry_confidence": candidate.industry_confidence,
            "products": details.get("products", []),
            "services": details.get("services", []),
            "business_types": details.get("business_types", []),
            "physical_goods": details.get("physical_goods"),
            "market_contexts": details.get("market_contexts", []),
            "country_evidence": {"headquarters": candidate.country} if candidate.country else {},
            "evidence": evidence,
        }
        try:
            matches = match_company_concepts(
                candidate.company_profile, task.search_intent, ai_settings
            )
            candidate.match_evaluation = evaluate_matches(
                task.search_intent, candidate.company_profile, matches
            )
        except Exception as exc:
            candidate.match_evaluation = {
                "evaluation_status": "insufficient_data",
                "decision": "pending",
                "target_relevance_score": None,
                "intent_match_confidence": None,
                "matched_concepts": [],
                "conflicting_concepts": [],
                "dimension_scores": {},
                "penalties": [],
                "reason_codes": ["concept_match_unavailable"],
                "error": str(exc)[:300],
                "policy_version": PIPELINE_V2.scoring_policy_version,
                "evidence_schema_version": "2.0.0",
                "rating": "Pending",
            }
        candidate.evaluation_status = candidate.match_evaluation["evaluation_status"]
        candidate.target_relevance_score = candidate.match_evaluation.get("target_relevance_score")
        candidate.relevance_rating = candidate.match_evaluation.get("rating", "Pending")
        from app.modules.discovery_review import record_task_evaluation

        record_task_evaluation(db, task, candidate)
        db.add(
            RelevanceScoreHistory(
                task_id=task.id,
                candidate_id=candidate.id,
                mode="evidence_ai_policy",
                ai_dimension_result=candidate.match_evaluation,
                evidence_snapshot=candidate.company_profile,
                score=candidate.target_relevance_score,
                rating=candidate.relevance_rating,
                prompt_version="concept-match-2.0.0",
                adapter_version=task.configuration_snapshot.get("concept_matching", {}).get(
                    "adapter_version"
                ),
                scoring_policy_version=PIPELINE_V2.scoring_policy_version,
                is_official=True,
            )
        )
        return
    filters = task.filters if isinstance(task.filters, dict) else {}
    company = {
        "brand_name": candidate.name,
        "website": candidate.website,
        "domain": candidate.domain,
        "country": candidate.country,
        "headquarters_country": candidate.country,
        "country_scope": "headquarters" if candidate.country else None,
        "category": candidate.industry,
        "industry": candidate.industry,
        "industry_details": candidate.industry_details,
        "industry_source": candidate.industry_source,
        "industry_confidence": candidate.industry_confidence,
    }
    score, _ = score_brand_relevance(company, filters)
    candidate.relevance_score = score


def approve_discovery_candidate(
    db: Session,
    candidate: DiscoveryCandidate,
    target_titles: list[str] | None = None,
    contacts_limit_per_brand: int = 5,
    source_task: SearchTask | None = None,
) -> SearchTask:
    if candidate.status not in {"pending", "enrichment_failed"}:
        raise ValueError("只有待审核或丰富失败的候选品牌可以发起精准丰富")
    official_domain = candidate.domain or candidate.website
    if not official_domain:
        raise ValueError("候选品牌缺少官网域名，无法进行精准品牌丰富")
    origin_task = source_task or (
        db.get(SearchTask, candidate.last_task_id) if candidate.last_task_id else None
    )
    task = create_search_task(
        db,
        SearchTaskCreate(
            name=f"精准丰富 {candidate.name}",
            mode="exact_brand",
            brand_keywords=[candidate.name],
            official_domains=[official_domain],
            countries=[candidate.country] if candidate.country else [],
            categories=[candidate.industry] if candidate.industry else [],
            target_titles=target_titles
            or ["Buyer", "Head of Buying", "Sourcing Manager", "Procurement Manager"],
            contacts_limit_per_brand=contacts_limit_per_brand,
            brand_limit=1,
            discovery_candidate_id=candidate.id,
        ),
        organization_id=origin_task.organization_id if origin_task else None,
        owner_id=origin_task.owner_id if origin_task else None,
    )
    transition_candidate(
        db,
        candidate,
        "enriching",
        TransitionContext(idempotency_key=f"approve:{candidate.id}:{task.id}"),
        exact_task_id=task.id,
        rejection_reason=None,
    )
    audit(
        db,
        "discovery_candidate.approve",
        "discovery_candidate",
        str(candidate.id),
        after={"task_id": str(task.id)},
    )
    return task


def reject_discovery_candidate(
    db: Session, candidate: DiscoveryCandidate, reason: str | None = None
) -> DiscoveryCandidate:
    if candidate.status == "promoted":
        raise ValueError("已转入客户数据的候选不能拒绝")
    transition_candidate(
        db,
        candidate,
        "rejected",
        TransitionContext(
            idempotency_key=f"reject:{candidate.id}:{hashlib.sha256(str(reason).encode()).hexdigest()[:12]}"
        ),
        rejection_reason=reason,
    )
    audit(
        db,
        "discovery_candidate.reject",
        "discovery_candidate",
        str(candidate.id),
        after={"reason": reason},
    )
    return candidate


def _candidate_for_task(db: Session, task: SearchTask) -> DiscoveryCandidate | None:
    candidate_id = task.filters.get("discovery_candidate_id")
    if not candidate_id:
        return None
    try:
        return db.get(DiscoveryCandidate, UUID(str(candidate_id)))
    except ValueError:
        return None


def _candidate_source_provider(db: Session, candidate: DiscoveryCandidate) -> ProviderConfig | None:
    """Resolve persisted candidate provenance without requiring its old config row to exist."""
    provider = db.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.provider == candidate.provider)
        .order_by(ProviderConfig.created_at.asc())
    )
    if provider is not None:
        return provider

    vendor = str(candidate.provider or "").partition("-")[0].strip().lower()
    adapter = adapter_for(vendor)
    if adapter is None:
        return None
    archived_provider = adapter.provider("company_search", "")
    if archived_provider is None:
        return None
    archived_provider.provider = candidate.provider
    archived_provider.enabled = False
    archived_provider.config = {
        **(archived_provider.config or {}),
        "api_key": "",
        "archived_source": True,
    }
    return archived_provider


def _company_payload_from_candidate(candidate: DiscoveryCandidate) -> dict:
    return {
        "brand_name": candidate.name,
        "legal_name": candidate.name,
        "domain": candidate.domain,
        "website": candidate.website
        or (f"https://{candidate.domain}" if candidate.domain else None),
        "country": candidate.country,
        "category": candidate.industry,
        "source_title": "Approved Hunter Discover candidate",
        "source_excerpt": "Approved for exact enrichment using its discovered official domain",
        "emails_count": candidate.emails_count,
        "raw_data": candidate.raw_data,
    }


def _mark_candidate_enrichment_failed(db: Session, task: SearchTask) -> None:
    candidate = _candidate_for_task(db, task)
    if candidate is not None and candidate.status == "enriching":
        transition_candidate(
            db,
            candidate,
            "enrichment_failed",
            TransitionContext(idempotency_key=f"enrichment-failed:{candidate.id}:{task.id}"),
        )


def _discover_emails_by_domain(
    db: Session, task: SearchTask, company: Company, brand: Brand
) -> int:
    """Use Hunter Domain Search when no usable named contact was discovered."""
    domain = str(company.domain or domain_from_url(brand.primary_website) or "").strip()
    if not domain:
        return 0
    limit = int(task.filters.get("contacts_limit_per_brand") or 5)
    provider, email_payloads, errors = execute_provider_waterfall(
        db,
        "brand_email_search",
        {
            "domain_search": True,
            "domain": domain,
            "brand": to_dict(brand),
            "limit": limit,
        },
        "emails",
        task=task,
        entity_type="email",
    )
    if provider is None:
        db.add(
            TaskItem(
                task_id=task.id,
                entity_type="email",
                stage="email_provider_unavailable",
                status=TaskStatus.partial,
                attempts=1,
                error_message="; ".join(errors)
                or "No enabled brand_email_search Provider is configured",
            )
        )
        return 0

    target_titles = [
        str(value).strip() for value in task.filters.get("target_titles", []) if str(value).strip()
    ]
    matching_payloads = [
        item
        for item in email_payloads
        if _title_matches_targets(str(item.get("title") or ""), target_titles)
    ]
    if email_payloads and not matching_payloads:
        _record_provider_fallback(
            db,
            task,
            "email",
            provider,
            f"{provider.provider}: {len(email_payloads)} domain emails did not match target titles",
        )

    discovered = 0
    for email_payload in matching_payloads[:limit]:
        address = str(email_payload.get("address") or email_payload.get("email") or "").strip()
        if not address:
            continue
        contact_id = None
        first_name = str(email_payload.get("first_name") or "").strip()
        title = str(email_payload.get("title") or "").strip()
        if first_name and title:
            contact = create_contact(
                db,
                ContactCreate(
                    brand_id=brand.id,
                    company_id=company.id,
                    first_name=first_name,
                    last_name=str(email_payload.get("last_name") or "").strip(),
                    title=title,
                ),
                provider=provider.provider,
                organization_id=task.organization_id,
                organization_unit_id=task.department_id,
                owner_id=task.owner_id,
            )
            contact_id = contact.id
            _record_task_result(
                db,
                task,
                "contact",
                contact.id,
                "contact_domain_discovered",
                provider.provider,
            )
        email = create_email(
            db,
            EmailCreate(
                contact_id=contact_id,
                brand_id=brand.id,
                address=address,
                type=str(email_payload.get("type") or "personal"),
            ),
            provider=provider.provider,
            organization_id=task.organization_id,
            organization_unit_id=task.department_id,
            owner_id=task.owner_id,
        )
        _record_task_result(
            db,
            task,
            "email",
            email.id,
            "email_domain_discovered",
            provider.provider,
        )
        # Hunter Domain Search already returns verification.status — apply it so
        # we don't waste credits re-verifying the same email through ZeroBounce.
        _apply_provider_verification(db, email, email_payload, provider.provider)
        _ensure_email_verified(db, email, task=task)
        discovered += 1
    return discovered


def _task_is_running(db: Session, task: SearchTask) -> bool:
    db.refresh(task)
    return task.status == TaskStatus.running


def parse_brand_website(db: Session, brand: Brand) -> dict:
    if brand.status == "pending_review":
        raise ValueError("Approve the brand discovery candidate before parsing its website")
    result = _parse_brand_website(db, None, brand)
    audit(
        db,
        "brand.parse_website",
        "brand",
        str(brand.id),
        after={"ok": not bool(result.get("error"))},
    )
    return result


def _parse_brand_website(db: Session, task: SearchTask | None, brand: Brand) -> dict:
    """Parse a website on explicit request and backfill missing industry evidence."""
    from app.modules.industry_enrichment import standardize_industry
    from app.modules.website_parser import parse_website

    try:
        result = parse_website(brand.primary_website)
    except Exception as exc:
        return _website_parse_payload(brand, None, str(exc)[:500])

    if result.error:
        if task is not None:
            db.add(
                TaskItem(
                    task_id=task.id,
                    entity_type="website",
                    entity_id=str(brand.id),
                    stage="website_parsed",
                    status=TaskStatus.failed,
                    error_message=result.error,
                )
            )
        return _website_parse_payload(brand, result)

    source_task = task or _latest_brand_discovery_task(db, brand.id)
    industry_result: dict | None = None
    industry_error: str | None = None
    if not brand.category and len(str(result.text_snippet or "").strip()) >= 120:
        website_evidence = {
            "source": "official_website",
            "company_name": brand.name,
            "url": result.url,
            "page_title": result.page_title,
            "website_text": result.text_snippet[:4000],
        }
        ai_settings = get_ai_settings(db, source_task)
        if not ai_settings.get("enabled") or not str(ai_settings.get("api_key") or "").strip():
            industry_error = "系统未启用 AI 行业识别，已保存官网原始证据但未生成行业"
        else:
            try:
                industry_result = standardize_industry(website_evidence, ai_settings)
            except Exception as exc:
                industry_error = f"官网行业识别失败：{str(exc)[:300]}"
        if industry_result is not None:
            brand.category = str(industry_result["standard_industry"])[:255]
            if source_task is not None and source_task.mode == "brand_discovery":
                brand.discovery_score, _ = score_brand_relevance(
                    {
                        "brand_name": brand.name,
                        "category": brand.category,
                        "industry": brand.category,
                        "industry_source": "official_website_ai",
                        "industry_confidence": int(industry_result.get("confidence") or 0),
                    },
                    source_task.filters or {},
                )
        elif industry_error is None:
            industry_error = "官网内容不足，未识别出可靠行业"
    elif not brand.category:
        industry_error = "官网描述内容不足，无法生成行业证据"

    existing_evidence = db.scalar(
        select(SourceEvidence).where(
            SourceEvidence.entity_type == "brand",
            SourceEvidence.entity_id == str(brand.id),
            SourceEvidence.content_hash == result.content_hash,
        )
    )
    if existing_evidence is None:
        existing_evidence = SourceEvidence(
                entity_type="brand",
                entity_id=str(brand.id),
                source_type=SourceType.official_website,
                url=result.url,
                title=result.page_title or brand.name,
                excerpt=result.text_snippet[:500] if result.text_snippet else None,
                content_hash=result.content_hash,
                confidence=int(industry_result.get("confidence") or 0) if industry_result else 0,
                provider="website_parser+ai" if industry_result is not None else "website_parser",
                task_id=source_task.id if source_task is not None else None,
                normalized_evidence=(
                    {
                        "category": brand.category,
                        "industry": brand.category,
                        "industry_source": "official_website_ai",
                        "industry_confidence": int(industry_result.get("confidence") or 0),
                        "classification": industry_result,
                    }
                    if industry_result is not None
                    else None
                ) or {},
            )
        db.add(existing_evidence)
    elif industry_result is not None:
        existing_evidence.confidence = int(industry_result.get("confidence") or 0)
        existing_evidence.provider = "website_parser+ai"
        existing_evidence.task_id = source_task.id if source_task is not None else None
        existing_evidence.normalized_evidence = {
            "category": brand.category,
            "industry": brand.category,
            "industry_source": "official_website_ai",
            "industry_confidence": int(industry_result.get("confidence") or 0),
            "classification": industry_result,
        }

    for parsed in result.emails:
        # Create email without a contact (generic/sales emails found on homepage)
        email = db.scalar(
            select(EmailAddress).where(
                EmailAddress.normalized_address == parsed.address.lower(),
                EmailAddress.organization_id == brand.organization_id,
                EmailAddress.department_id == brand.department_id,
                EmailAddress.deleted_at.is_(None),
            )
        )
        if email is None:
            email = EmailAddress(
                brand_id=brand.id,
                address=parsed.address,
                normalized_address=parsed.address.lower(),
                domain=parsed.address.split("@")[1],
                type=parsed.type,
                status=EmailStatus.raw,
                pool=EmailPool.raw,
                provider="website_parser",
                organization_id=brand.organization_id,
                department_id=brand.department_id,
                owner_id=brand.owner_id,
            )
            db.add(email)
            db.flush()
        elif email.brand_id is None:
            email.brand_id = brand.id
        if task is not None:
            _record_task_result(
                db,
                task,
                "email",
                email.id,
                "email_website_discovered",
                "website_parser",
            )
        evidence_exists = db.scalar(
            select(SourceEvidence.id).where(
                SourceEvidence.entity_type == "email",
                SourceEvidence.entity_id == str(email.id),
                SourceEvidence.url == result.url,
            )
        )
        if evidence_exists is None:
            db.add(
                SourceEvidence(
                    entity_type="email",
                    entity_id=str(email.id),
                    source_type=SourceType.official_website,
                    url=parsed.url or result.url,
                    title=f"Found on {domain_from_url(parsed.url) if parsed.url else result.domain}",
                    excerpt=f"Extracted via {parsed.source}",
                    confidence=parsed.confidence,
                    provider="website_parser",
                )
            )
        db.flush()
        _ensure_email_verified(db, email, task=task)

    if task is not None:
        db.add(
            TaskItem(
                task_id=task.id,
                entity_type="website",
                entity_id=str(brand.id),
                stage="website_parsed",
                status=TaskStatus.completed,
                provider="website_parser",
            )
        )
    return _website_parse_payload(
        brand, result, industry=industry_result, industry_error=industry_error
    )


def _latest_brand_discovery_task(db: Session, brand_id: UUID) -> SearchTask | None:
    task_id = db.scalar(
        select(SourceEvidence.task_id)
        .join(SearchTask, SearchTask.id == SourceEvidence.task_id)
        .where(
            SourceEvidence.entity_type == "brand",
            SourceEvidence.entity_id == str(brand_id),
            SourceEvidence.task_id.is_not(None),
            SearchTask.mode == "brand_discovery",
        )
        .order_by(SourceEvidence.created_at.desc())
        .limit(1)
    )
    return db.get(SearchTask, task_id) if task_id is not None else None


def _website_parse_payload(
    brand: Brand,
    result: object | None,
    error: str | None = None,
    industry: dict | None = None,
    industry_error: str | None = None,
) -> dict:
    return {
        "brand_id": str(brand.id),
        "url": getattr(result, "url", brand.primary_website),
        "domain": getattr(result, "domain", domain_from_url(brand.primary_website)),
        "title": getattr(result, "page_title", ""),
        "emails": [
            {"address": email.address, "type": email.type, "confidence": email.confidence}
            for email in getattr(result, "emails", [])
        ],
        "phones": getattr(result, "phones", []),
        "social_links": getattr(result, "social_links", {}),
        "error": error or getattr(result, "error", None),
        "elapsed_ms": getattr(result, "elapsed_ms", 0),
        "pages_scanned": getattr(result, "pages_scanned", 0),
        "attempted_urls": getattr(result, "attempted_urls", []),
        "industry": brand.category,
        "industry_confidence": (
            int(industry.get("confidence") or 0) if industry is not None else None
        ),
        "industry_source": "official_website_ai" if industry is not None else None,
        "industry_error": industry_error,
        "discovery_score": brand.discovery_score,
    }


def _verify_unverified_emails(
    db: Session, contact_id: UUID, task: SearchTask | None = None
) -> None:
    """Ensure all newly discovered or inconclusive contact emails have a verification record."""
    emails = db.scalars(
        select(EmailAddress).where(
            EmailAddress.contact_id == contact_id,
            EmailAddress.deleted_at.is_(None),
        )
    ).all()
    for email in emails:
        _ensure_email_verified(db, email, task=task)


def _ensure_email_verified(
    db: Session, email: EmailAddress, task: SearchTask | None = None
) -> EmailAddress:
    if _email_needs_verification(email):
        return (
            verify_email(db, email.id, task=task)
            if task is not None
            else verify_email(db, email.id)
        )
    return email


def _apply_provider_verification(
    db: Session, email: EmailAddress, email_payload: dict, provider_name: str
) -> None:
    """Persist a Vendor's conclusive email status as verification evidence.

    Hunter Domain Search and Apollo enrichment can return an email status with
    the discovered address. Only a status that the Vendor defines as verified
    is promoted to the valid pool; inconclusive statuses remain reviewable.
    """
    verification_status = str(email_payload.get("verification_status") or "").strip().lower()
    if not verification_status:
        return
    verification_status = verification_status.replace("_", " ")
    vendor = str(email_payload.get("verification_provider") or provider_name).casefold()
    is_apollo = "apollo" in vendor
    valid_statuses = (
        {"verified", "unverified", "likely to engage", "unavailable", "invalid"}
        if is_apollo
        else {"valid", "invalid", "accept all", "webmail", "risky", "unknown"}
    )
    if verification_status not in valid_statuses:
        return
    if is_apollo:
        mapped, score = _apollo_verification_result(verification_status)
    else:
        hunter_status = verification_status.replace(" ", "_")
        mapped = _hunter_verification_status(hunter_status)
        score = _hunter_verification_score(hunter_status)
    result_data = {
        "result": mapped,
        "score": score,
        "is_catch_all": verification_status in {"accept all", "webmail"},
        "is_disposable": False,
        "domain_deliverable": True,
        "provider": provider_name,
        "pre_verified": True,
        "vendor_status": verification_status,
        "verification_source": email_payload.get("verification_source"),
    }
    email.status = mapped
    email.deliverability_score = score
    assess_email_authenticity(db, email, result_data, provider_name=provider_name)
    db.add(
        EmailVerification(
            email_id=email.id,
            provider=provider_name,
            result=email.status,
            score=email.confidence_score,
            deliverability_score=email.deliverability_score,
            identity_score=email.identity_score,
            evidence_score=email.evidence_score,
            confidence_score=email.confidence_score,
            authenticity_level=email.authenticity_level,
            is_catch_all=email.is_catch_all,
            is_disposable=email.is_disposable,
            domain_matches_brand=email.domain_matches_brand,
            raw_result=jsonable_encoder({**result_data, "authenticity": {}}),
            checked_at=utc_now(),
        )
    )


def _apollo_verification_result(status: str) -> tuple[str, int]:
    if status == "verified":
        return "valid", 100
    if status == "invalid":
        return "invalid", 0
    if status == "likely to engage":
        return "risky", 60
    if status == "unverified":
        return "risky", 40
    return "unknown", 0


def _hunter_verification_status(status: str) -> str:
    lowered = status.lower()
    if lowered == "valid":
        return "valid"
    if lowered in {"accept_all", "webmail"}:
        return "risky"
    if lowered == "unknown":
        return "unknown"
    return "invalid"


def _hunter_verification_score(status: str) -> int:
    lowered = status.lower()
    if lowered == "valid":
        return 100
    if lowered in {"accept_all", "webmail"}:
        return 50
    if lowered == "unknown":
        return 50
    return 0


def _email_needs_verification(email: EmailAddress) -> bool:
    if email.last_verified_at is None:
        return True
    if str(email.status) in {EmailStatus.raw, EmailStatus.pending, EmailStatus.unknown}:
        return True
    return str(email.authenticity_level or "") in {"", "unverified"}


def _provider_error_item(
    db: Session, task: SearchTask, entity_type: str, provider: ProviderConfig, error: str | None
) -> None:
    db.add(
        TaskItem(
            task_id=task.id,
            entity_type=entity_type,
            stage=f"{entity_type}_provider",
            status=TaskStatus.failed,
            attempts=1,
            provider=provider.provider,
            error_message=error,
        )
    )


def get_or_create_company(db: Session, data: dict) -> Company:
    domain = data.get("domain") or domain_from_url(data.get("website") or data.get("url"))
    legal_name = str(
        data.get("legal_name")
        or data.get("company_name")
        or data.get("brand_name")
        or data.get("name")
        or ""
    ).strip()
    company = None
    if domain:
        company = db.scalar(
            select(Company).where(Company.domain == domain, Company.deleted_at.is_(None))
        )
    if company is None and legal_name:
        company = db.scalar(
            select(Company).where(Company.legal_name == legal_name, Company.deleted_at.is_(None))
        )
    if company:
        return company
    company = Company(
        legal_name=legal_name,
        country=data.get("country"),
        city=data.get("city"),
        domain=domain,
        company_type=data.get("company_type"),
    )
    db.add(company)
    db.flush()
    return company


def create_brand(
    db: Session,
    payload: BrandCreate,
    company: Company | None = None,
    source_type: SourceType = SourceType.manual_entry,
    provider: str = "manual",
    source_url: str | None = None,
    source_title: str | None = None,
    source_excerpt: str | None = None,
    discovery_score: int = 0,
    organization_id: UUID | None = None,
    organization_unit_id: UUID | None = None,
    owner_id: UUID | None = None,
) -> Brand:
    normalized_name = slugify(payload.name)
    existing = db.scalar(
        select(Brand).where(
            Brand.normalized_name == normalized_name,
            Brand.organization_id == organization_id,
            Brand.department_id.is_(None)
            if organization_unit_id is None
            else Brand.department_id == organization_unit_id,
            Brand.deleted_at.is_(None),
        )
    )
    if existing:
        if payload.category and not existing.category:
            existing.category = payload.category
        existing.discovery_score = max(existing.discovery_score, discovery_score)
        return existing
    if company is None and payload.company_name:
        company = get_or_create_company(
            db,
            {
                "legal_name": payload.company_name,
                "brand_name": payload.name,
                "country": payload.country,
                "domain": domain_from_url(payload.website),
            },
        )
    brand = Brand(
        company_id=company.id if company else None,
        name=payload.name,
        normalized_name=normalized_name,
        primary_website=payload.website,
        country=payload.country,
        category=payload.category,
        status="new",
        discovery_score=max(0, min(100, discovery_score)),
        organization_id=organization_id,
        department_id=organization_unit_id,
        owner_id=owner_id,
    )
    db.add(brand)
    db.flush()
    if payload.website:
        website = Website(
            brand_id=brand.id,
            domain=domain_from_url(payload.website) or "",
            url=payload.website,
            is_primary=True,
            confidence=100 if source_type == SourceType.manual_entry else 70,
            verification_status="manual" if source_type == SourceType.manual_entry else "candidate",
        )
        db.add(website)
    if payload.website or source_url:
        db.add(
            SourceEvidence(
                entity_type="brand",
                entity_id=str(brand.id),
                source_type=source_type,
                url=source_url or payload.website,
                title=source_title or payload.name,
                excerpt=source_excerpt,
                provider=provider,
                confidence=max(0, min(100, discovery_score or (70 if payload.website else 0))),
            )
        )
    audit(db, "brand.create", "brand", str(brand.id), after=payload.model_dump(mode="json"))
    emit(db, "brand.created", {"brand_id": brand.id, "provider": provider})
    return brand


def brand_detail(db: Session, brand: Brand) -> dict:
    data = to_dict(brand)
    data["websites"] = [
        to_dict(item)
        for item in db.scalars(
            select(Website).where(Website.brand_id == brand.id, Website.deleted_at.is_(None))
        ).all()
    ]
    positions = db.execute(
        select(Contact, ContactPosition)
        .join(ContactPosition, ContactPosition.contact_id == Contact.id)
        .where(
            ContactPosition.brand_id == brand.id,
            Contact.deleted_at.is_(None),
            ContactPosition.deleted_at.is_(None),
        )
    ).all()
    data["contacts"] = [
        {**to_dict(contact), "title": position.title, "department": position.department}
        for contact, position in positions
    ]
    data["source_evidence"] = [
        to_dict(item)
        for item in db.scalars(
            select(SourceEvidence).where(
                SourceEvidence.entity_type == "brand", SourceEvidence.entity_id == str(brand.id)
            )
        ).all()
    ]
    return data


def update_brand(db: Session, brand: Brand, payload: BrandUpdate) -> Brand:
    before = to_dict(brand)
    changes = payload.model_dump(exclude_unset=True)
    company_name = changes.pop("company_name", None)
    website = changes.pop("website", None)
    if company_name:
        company = get_or_create_company(
            db, {"legal_name": company_name, "country": changes.get("country")}
        )
        brand.company_id = company.id
    if website is not None:
        brand.primary_website = website or None
        if website:
            domain = domain_from_url(website) or website
            existing = db.scalar(
                select(Website).where(
                    Website.brand_id == brand.id,
                    Website.url == website,
                    Website.deleted_at.is_(None),
                )
            )
            if existing is None:
                db.add(
                    Website(
                        brand_id=brand.id,
                        domain=domain,
                        url=website,
                        is_primary=True,
                        confidence=100,
                        verification_status="manual",
                    )
                )
    for key, value in changes.items():
        if key == "name" and value:
            brand.normalized_name = slugify(value)
        setattr(brand, key, value)
    audit(db, "brand.update", "brand", str(brand.id), before=before, after=to_dict(brand))
    return brand


def approve_discovery_brand(db: Session, brand: Brand) -> SearchTask:
    if brand.status != "pending_review":
        raise ValueError("Only pending discovery candidates can be approved")
    before = {"status": brand.status, "discovery_score": brand.discovery_score}
    brand.status = "approved"
    task = create_search_task(
        db,
        SearchTaskCreate(
            name=f"Enrich {brand.name}",
            mode="exact_brand",
            brand_keywords=[brand.name],
            official_domains=[brand.primary_website] if brand.primary_website else [],
            countries=[brand.country] if brand.country else [],
            categories=[brand.category] if brand.category else [],
            brand_limit=1,
        ),
        organization_id=brand.organization_id,
        organization_unit_id=brand.department_id,
        owner_id=brand.owner_id,
    )
    audit(
        db,
        "brand.discovery_approve",
        "brand",
        str(brand.id),
        before=before,
        after={"status": brand.status, "enrichment_task_id": str(task.id)},
    )
    emit(db, "brand.discovery_approved", {"brand_id": brand.id, "task_id": task.id})
    return task


def _normalized_contact_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _normalized_linkedin_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/").casefold()


def _lock_contact_identity(db: Session, identity_key: str) -> None:
    """Serialize matching contact writes on PostgreSQL without affecting SQLite tests."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(identity_key, 0))))


def ensure_contact_position(
    db: Session,
    contact_id: UUID,
    company_id: UUID | None,
    brand_id: UUID | None,
    title: str,
    provider: str,
) -> ContactPosition:
    normalized_title = _normalized_contact_text(title)
    filters = [
        ContactPosition.contact_id == contact_id,
        ContactPosition.deleted_at.is_(None),
        func.lower(func.trim(ContactPosition.title)) == normalized_title,
    ]
    filters.append(
        ContactPosition.company_id.is_(None)
        if company_id is None
        else ContactPosition.company_id == company_id
    )
    filters.append(
        ContactPosition.brand_id.is_(None)
        if brand_id is None
        else ContactPosition.brand_id == brand_id
    )
    existing = db.scalar(select(ContactPosition).where(*filters).limit(1))
    if existing is not None:
        return existing
    position = ContactPosition(
        contact_id=contact_id,
        company_id=company_id,
        brand_id=brand_id,
        title=title.strip(),
        priority=title_priority(title),
        is_current=True,
        provider=provider,
    )
    db.add(position)
    # SessionLocal disables autoflush. Persist now so a second Vendor/task sees it.
    db.flush()
    return position


def create_contact(
    db: Session,
    payload: ContactCreate,
    provider: str = "manual",
    organization_id: UUID | None = None,
    organization_unit_id: UUID | None = None,
    owner_id: UUID | None = None,
) -> Contact:
    # Contacts created for an existing brand must inherit its tenancy even when
    # the caller is an older/manual integration that did not pass scope fields.
    if payload.brand_id is not None and (organization_id is None or organization_unit_id is None):
        brand = db.get(Brand, payload.brand_id)
        if brand is not None:
            organization_id = organization_id or brand.organization_id
            organization_unit_id = organization_unit_id or brand.department_id
            owner_id = owner_id or brand.owner_id
    full_name = f"{payload.first_name} {payload.last_name}".strip()
    normalized_name = _normalized_contact_text(full_name)
    normalized_title = _normalized_contact_text(payload.title)
    normalized_linkedin = _normalized_linkedin_url(payload.linkedin_url)
    organization_key = str(organization_id or "unscoped")
    unit_key = str(organization_unit_id or "unscoped")
    scope_key = str(payload.brand_id or payload.company_id or "unscoped")
    identity_key = (
        f"org:{organization_key}|unit:{unit_key}|linkedin:{normalized_linkedin}"
        if normalized_linkedin
        else f"org:{organization_key}|unit:{unit_key}|scope:{scope_key}|name:{normalized_name}|title:{normalized_title}"
    )
    _lock_contact_identity(db, identity_key)

    existing = None
    if normalized_linkedin:
        linkedin_query = select(Contact).where(
            func.lower(func.rtrim(Contact.linkedin_url, "/")) == normalized_linkedin,
            Contact.deleted_at.is_(None),
        )
        linkedin_query = linkedin_query.where(
            Contact.organization_id.is_(None)
            if organization_id is None
            else Contact.organization_id == organization_id
        )
        linkedin_query = linkedin_query.where(
            Contact.department_id.is_(None)
            if organization_unit_id is None
            else Contact.department_id == organization_unit_id
        )
        existing = db.scalar(linkedin_query.limit(1))
    if existing is None:
        position_filters = [
            ContactPosition.deleted_at.is_(None),
            func.lower(func.trim(ContactPosition.title)) == normalized_title,
        ]
        if payload.brand_id is not None:
            position_filters.append(ContactPosition.brand_id == payload.brand_id)
        elif payload.company_id is not None:
            position_filters.append(ContactPosition.company_id == payload.company_id)
        else:
            position_filters.extend(
                [ContactPosition.brand_id.is_(None), ContactPosition.company_id.is_(None)]
            )
        query = (
            select(Contact)
            .join(ContactPosition, ContactPosition.contact_id == Contact.id)
            .where(
                func.lower(func.trim(Contact.full_name)) == normalized_name,
                Contact.deleted_at.is_(None),
                *position_filters,
            )
        )
        query = query.where(
            Contact.organization_id.is_(None)
            if organization_id is None
            else Contact.organization_id == organization_id
        )
        query = query.where(
            Contact.department_id.is_(None)
            if organization_unit_id is None
            else Contact.department_id == organization_unit_id
        )
        existing = db.scalar(query.limit(1))
    if existing is not None:
        ensure_contact_position(
            db, existing.id, payload.company_id, payload.brand_id, payload.title, provider
        )
        return existing
    contact = Contact(
        first_name=payload.first_name,
        last_name=payload.last_name,
        full_name=full_name,
        linkedin_url=payload.linkedin_url,
        status="invalid",
        organization_id=organization_id,
        department_id=organization_unit_id,
        owner_id=owner_id,
    )
    db.add(contact)
    db.flush()
    ensure_contact_position(
        db, contact.id, payload.company_id, payload.brand_id, payload.title, provider
    )
    audit(db, "contact.create", "contact", str(contact.id), after=payload.model_dump(mode="json"))
    emit(db, "contact.discovered", {"contact_id": contact.id, "provider": provider})
    return contact


def _email_makes_contact_valid(email: EmailAddress | dict) -> bool:
    if isinstance(email, dict):
        authenticity_level = email.get("authenticity_level")
        pool = email.get("pool")
    else:
        authenticity_level = email.authenticity_level
        pool = email.pool
    return str(authenticity_level) == "verified" and str(pool) == str(EmailPool.valid)


def refresh_contact_status(db: Session, contact_id: UUID | None) -> str | None:
    """Synchronize contact validity with its non-archived email evidence."""
    if contact_id is None:
        return None
    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        return None
    emails = list(
        db.scalars(
            select(EmailAddress).where(
                EmailAddress.contact_id == contact_id,
                EmailAddress.deleted_at.is_(None),
            )
        )
    )
    if any(_email_makes_contact_valid(email) for email in emails):
        status = "valid"
    elif any(str(email.pool) == str(EmailPool.manual_review) for email in emails):
        status = "pending_review"
    elif any(str(email.pool) == str(EmailPool.raw) for email in emails):
        status = "pending_verification"
    else:
        status = "invalid"
    contact.status = status
    return status


def update_contact(db: Session, contact: Contact, payload: ContactUpdate) -> Contact:
    before = to_dict(contact)
    changes = payload.model_dump(exclude_unset=True)
    title = changes.pop("title", None)
    for key, value in changes.items():
        setattr(contact, key, value)
    if "first_name" in changes or "last_name" in changes:
        contact.full_name = " ".join(
            part for part in [contact.first_name, contact.last_name] if part
        ).strip()
    if title:
        position = db.scalar(
            select(ContactPosition).where(
                ContactPosition.contact_id == contact.id,
                ContactPosition.is_current.is_(True),
                ContactPosition.deleted_at.is_(None),
            )
        )
        if position:
            position.title = title
    audit(db, "contact.update", "contact", str(contact.id), before=before, after=to_dict(contact))
    return contact


def create_email(
    db: Session,
    payload: EmailCreate,
    provider: str = "manual",
    *,
    organization_id: UUID | None = None,
    organization_unit_id: UUID | None = None,
    owner_id: UUID | None = None,
) -> EmailAddress:
    normalized = str(payload.address).lower()
    brand_id = payload.brand_id or _brand_id_for_contact(db, payload.contact_id)
    # Resolve ownership from the related contact/brand before checking for a
    # duplicate. A single address may legitimately exist in different groups.
    related = db.get(Contact, payload.contact_id) if payload.contact_id else None
    if related is None and brand_id is not None:
        related = db.get(Brand, brand_id)
    if related is not None:
        organization_id = organization_id or related.organization_id
        organization_unit_id = organization_unit_id or related.department_id
        owner_id = owner_id or related.owner_id
    existing = db.scalar(
        select(EmailAddress).where(
            EmailAddress.normalized_address == normalized,
            EmailAddress.organization_id.is_(None)
            if organization_id is None
            else EmailAddress.organization_id == organization_id,
            EmailAddress.department_id.is_(None)
            if organization_unit_id is None
            else EmailAddress.department_id == organization_unit_id,
            EmailAddress.deleted_at.is_(None),
        )
    )
    if existing:
        if existing.brand_id is None and brand_id is not None:
            existing.brand_id = brand_id
        if existing.contact_id is None and payload.contact_id is not None:
            existing.contact_id = payload.contact_id
        refresh_contact_status(db, existing.contact_id)
        return existing
    domain = normalized.split("@", 1)[1]
    email = EmailAddress(
        contact_id=payload.contact_id,
        brand_id=brand_id,
        address=str(payload.address),
        normalized_address=normalized,
        domain=domain,
        type=payload.type,
        status=EmailStatus.raw,
        pool=EmailPool.raw,
        provider=provider,
        organization_id=organization_id,
        department_id=organization_unit_id,
        owner_id=owner_id,
    )
    db.add(email)
    db.flush()
    _record_email_discovery_evidence(db, email, provider)
    refresh_contact_status(db, email.contact_id)
    audit(db, "email.create", "email", str(email.id), after=payload.model_dump(mode="json"))
    emit(db, "email.discovered", {"email_id": email.id, "provider": provider})
    return email


def _record_email_discovery_evidence(db: Session, email: EmailAddress, provider: str) -> None:
    if provider == "website_parser":
        return
    source_type = SourceType.manual_entry
    confidence = 50
    title = "Email added manually"
    if provider == "file_import":
        source_type = SourceType.manual_import
        title = "Email imported from file"
    elif provider == "pattern_inference":
        source_type = "pattern_inference"
        confidence = 35
        title = "Email generated from a name and domain pattern"
    elif provider != "manual":
        source_type = SourceType.commercial_api
        confidence = 70
        title = f"Email discovered by {provider}"
    existing = db.scalar(
        select(SourceEvidence.id).where(
            SourceEvidence.entity_type == "email",
            SourceEvidence.entity_id == str(email.id),
            SourceEvidence.provider == provider,
            SourceEvidence.title == title,
        )
    )
    if existing is None:
        db.add(
            SourceEvidence(
                entity_type="email",
                entity_id=str(email.id),
                source_type=source_type,
                title=title,
                confidence=confidence,
                provider=provider,
            )
        )


def _brand_id_for_contact(db: Session, contact_id: UUID | None) -> UUID | None:
    if contact_id is None:
        return None
    return db.scalar(
        select(ContactPosition.brand_id)
        .where(
            ContactPosition.contact_id == contact_id,
            ContactPosition.brand_id.is_not(None),
            ContactPosition.is_current.is_(True),
            ContactPosition.deleted_at.is_(None),
        )
        .order_by(ContactPosition.created_at.desc())
        .limit(1)
    )


def update_email(db: Session, email: EmailAddress, payload: EmailUpdate) -> EmailAddress:
    before = to_dict(email)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(email, key, value)
    audit(db, "email.update", "email", str(email.id), before=before, after=to_dict(email))
    return email


def archive_entity(
    db: Session, entity: Brand | Contact | EmailAddress | Website, entity_type: str
) -> None:
    contact_id = entity.contact_id if isinstance(entity, EmailAddress) else None
    entity.deleted_at = datetime.now(UTC)
    # Production sessions disable autoflush. Persist the archive marker before
    # recomputing contact validity so the removed email is excluded.
    if contact_id is not None:
        db.flush()
    refresh_contact_status(db, contact_id)
    audit(db, f"{entity_type}.archive", entity_type, str(entity.id))


def bulk_archive_brands(db: Session, brand_ids: list[UUID]) -> dict:
    unique_ids = list(dict.fromkeys(brand_ids))
    brands = db.scalars(
        select(Brand).where(Brand.id.in_(unique_ids), Brand.deleted_at.is_(None))
    ).all()
    active_brand_ids = [brand.id for brand in brands]
    if not active_brand_ids:
        return {
            "requested": len(unique_ids),
            "archived": 0,
            "contacts_archived": 0,
            "contacts_preserved": 0,
            "emails_archived": 0,
            "positions_archived": 0,
            "websites_archived": 0,
            "skipped": len(unique_ids),
        }

    brand_positions = list(
        db.scalars(
            select(ContactPosition).where(
                ContactPosition.brand_id.in_(active_brand_ids),
                ContactPosition.deleted_at.is_(None),
            )
        )
    )
    linked_contact_ids = {position.contact_id for position in brand_positions}
    active_linked_contact_ids = set()
    if linked_contact_ids:
        active_linked_contact_ids = set(
            db.scalars(
                select(Contact.id).where(
                    Contact.id.in_(linked_contact_ids),
                    Contact.deleted_at.is_(None),
                )
            )
        )

    shared_contact_ids = set()
    if active_linked_contact_ids:
        shared_contact_ids = set(
            db.scalars(
                select(ContactPosition.contact_id)
                .join(Brand, ContactPosition.brand_id == Brand.id)
                .where(
                    ContactPosition.contact_id.in_(active_linked_contact_ids),
                    ContactPosition.brand_id.not_in(active_brand_ids),
                    ContactPosition.deleted_at.is_(None),
                    ContactPosition.is_current.is_(True),
                    Brand.deleted_at.is_(None),
                )
                .distinct()
            )
        )
    contacts_to_archive_ids = active_linked_contact_ids - shared_contact_ids
    contacts = (
        list(db.scalars(select(Contact).where(Contact.id.in_(contacts_to_archive_ids))))
        if contacts_to_archive_ids
        else []
    )

    position_by_id = {position.id: position for position in brand_positions}
    if contacts_to_archive_ids:
        for position in db.scalars(
            select(ContactPosition).where(
                ContactPosition.contact_id.in_(contacts_to_archive_ids),
                ContactPosition.deleted_at.is_(None),
            )
        ):
            position_by_id[position.id] = position

    email_filters = [EmailAddress.brand_id.in_(active_brand_ids)]
    if contacts_to_archive_ids:
        email_filters.append(EmailAddress.contact_id.in_(contacts_to_archive_ids))
    emails = list(
        db.scalars(
            select(EmailAddress).where(
                EmailAddress.deleted_at.is_(None),
                or_(*email_filters),
            )
        )
    )
    websites = list(
        db.scalars(
            select(Website).where(
                Website.brand_id.in_(active_brand_ids),
                Website.deleted_at.is_(None),
            )
        )
    )

    for email in emails:
        archive_entity(db, email, "email")
    archived_at = datetime.now(UTC)
    for position in position_by_id.values():
        position.deleted_at = archived_at
        position.is_current = False
    for contact in contacts:
        archive_entity(db, contact, "contact")
    for website in websites:
        archive_entity(db, website, "website")
    for brand in brands:
        archive_entity(db, brand, "brand")
    result = {
        "requested": len(unique_ids),
        "archived": len(brands),
        "contacts_archived": len(contacts),
        "contacts_preserved": len(shared_contact_ids),
        "emails_archived": len(emails),
        "positions_archived": len(position_by_id),
        "websites_archived": len(websites),
        "skipped": len(unique_ids) - len(brands),
    }
    audit(db, "brand.bulk_archive", "brand", after=result)
    return result


def bulk_archive_emails(db: Session, email_ids: list[UUID]) -> dict:
    unique_ids = list(dict.fromkeys(email_ids))
    emails = db.scalars(
        select(EmailAddress).where(
            EmailAddress.id.in_(unique_ids), EmailAddress.deleted_at.is_(None)
        )
    ).all()
    for email in emails:
        archive_entity(db, email, "email")
    audit(
        db,
        "email.bulk_archive",
        "email",
        after={"requested": len(unique_ids), "archived": len(emails)},
    )
    return {
        "requested": len(unique_ids),
        "archived": len(emails),
        "skipped": len(unique_ids) - len(emails),
    }


def bulk_archive_contacts(db: Session, contact_ids: list[UUID]) -> dict:
    unique_ids = list(dict.fromkeys(contact_ids))
    contacts = db.scalars(
        select(Contact).where(Contact.id.in_(unique_ids), Contact.deleted_at.is_(None))
    ).all()
    emails = db.scalars(
        select(EmailAddress).where(
            EmailAddress.contact_id.in_(unique_ids), EmailAddress.deleted_at.is_(None)
        )
    ).all()
    for email in emails:
        archive_entity(db, email, "email")
    for contact in contacts:
        archive_entity(db, contact, "contact")
    audit(
        db,
        "contact.bulk_archive",
        "contact",
        after={
            "requested": len(unique_ids),
            "contacts_archived": len(contacts),
            "emails_archived": len(emails),
        },
    )
    return {
        "requested": len(unique_ids),
        "contacts_archived": len(contacts),
        "emails_archived": len(emails),
        "skipped": len(unique_ids) - len(contacts),
    }


def _nested_flag(payload: object, *keys: str) -> bool:
    wanted = {key.lower() for key in keys}
    queue = [payload]
    while queue:
        value = queue.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in wanted:
                    if isinstance(item, bool):
                        return item
                    if str(item).strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "y",
                        "catch-all",
                        "catch_all",
                        "accept_all",
                        "disposable",
                    }:
                        return True
                if isinstance(item, (dict, list)):
                    queue.append(item)
        elif isinstance(value, list):
            queue.extend(value)
    return False


def _email_evidence(db: Session, email: EmailAddress) -> tuple[list[SourceEvidence], int, bool]:
    evidence = list(
        db.scalars(
            select(SourceEvidence).where(
                SourceEvidence.entity_type == "email",
                SourceEvidence.entity_id == str(email.id),
            )
        )
    )
    weights = {
        SourceType.official_website.value: 100,
        SourceType.commercial_api.value: 75,
        SourceType.search_engine.value: 65,
        SourceType.public_directory.value: 60,
        SourceType.manual_entry.value: 50,
        SourceType.manual_import.value: 50,
        "pattern_inference": 35,
    }
    scores = [
        max(int(item.confidence or 0), weights.get(str(item.source_type), 40)) for item in evidence
    ]
    score = min(100, (max(scores) if scores else 20) + max(0, len(evidence) - 1) * 5)
    official = any(str(item.source_type) == SourceType.official_website.value for item in evidence)
    return evidence, score, official


def _email_identity_context(
    db: Session, email: EmailAddress, *, official_evidence: bool, manual_approved: bool
) -> tuple[int, bool, dict]:
    contact = db.get(Contact, email.contact_id) if email.contact_id else None
    brand_id = email.brand_id or _brand_id_for_contact(db, email.contact_id)
    brand = db.get(Brand, brand_id) if brand_id else None
    company = db.get(Company, brand.company_id) if brand and brand.company_id else None
    brand_domain = domain_from_url(brand.primary_website) if brand else None
    company_domain = (company.domain or "").lower().removeprefix("www.") if company else None
    domain_matches = email.domain.lower().removeprefix("www.") in {brand_domain, company_domain}

    identity_score = 40 if domain_matches else 0
    local_part = email.normalized_address.split("@", 1)[0]
    normalized_local = re.sub(r"[^a-z0-9]", "", local_part.lower())
    name_match = False
    if contact:
        identity_score += 20
        first = re.sub(r"[^a-z0-9]", "", contact.first_name.lower())
        last = re.sub(r"[^a-z0-9]", "", contact.last_name.lower())
        candidates = {
            first,
            f"{first}{last}",
            f"{first[:1]}{last}" if first and last else "",
            f"{first}{last[:1]}" if first and last else "",
            last,
        }
        name_match = bool(normalized_local and normalized_local in candidates)
        if name_match:
            identity_score += 30
    elif email.type == "generic" and official_evidence:
        identity_score += 25
    if official_evidence:
        identity_score += 10
    if manual_approved:
        identity_score = max(identity_score, 85)
    identity_score = min(100, identity_score)
    return (
        identity_score,
        domain_matches,
        {
            "contact_id": str(contact.id) if contact else None,
            "brand_id": str(brand.id) if brand else None,
            "brand_domain": brand_domain or company_domain,
            "name_pattern_matches": name_match,
            "manual_review_approved": manual_approved,
        },
    )


def assess_email_authenticity(
    db: Session,
    email: EmailAddress,
    result_data: dict,
    *,
    provider_name: str,
    manual_approved: bool = False,
) -> dict:
    status_value = str(result_data.get("result") or email.status or EmailStatus.unknown)
    catch_all = _nested_flag(
        result_data, "is_catch_all", "catch_all", "accept_all"
    ) or status_value in {"catch-all", "catch_all", "accept_all"}
    disposable = (
        _nested_flag(result_data, "is_disposable", "disposable")
        or status_value == EmailStatus.disposable
    )
    deliverability = max(
        0, min(100, int(result_data.get("score") or email.deliverability_score or 0))
    )
    if catch_all:
        deliverability = min(deliverability, 55)
    if disposable or status_value in {EmailStatus.invalid, EmailStatus.do_not_contact}:
        deliverability = 0

    evidence, evidence_score, official_evidence = _email_evidence(db, email)
    identity_score, domain_matches, identity_context = _email_identity_context(
        db,
        email,
        official_evidence=official_evidence,
        manual_approved=manual_approved,
    )
    domain_score = 100 if domain_matches else 20
    confidence = round(
        deliverability * 0.40
        + identity_score * 0.25
        + domain_score * 0.15
        + evidence_score * 0.15
        + 100 * 0.05
    )
    if catch_all:
        confidence = min(confidence, 69)
    if disposable or status_value in {EmailStatus.invalid, EmailStatus.do_not_contact}:
        confidence = 0

    rules = get_system_settings(db)["email_rules"]
    verified_threshold = int(rules.get("verified_confidence", 80))
    probable_threshold = int(rules.get("probable_confidence", 65))
    invalid_statuses = {EmailStatus.invalid, EmailStatus.disposable, EmailStatus.do_not_contact}
    if status_value in invalid_statuses or disposable:
        authenticity_level = "invalid"
    elif (
        status_value == EmailStatus.valid
        and not catch_all
        and confidence >= verified_threshold
        and domain_matches
        and (identity_score >= 60 or official_evidence)
    ):
        authenticity_level = "verified"
    elif status_value == EmailStatus.valid and confidence >= probable_threshold:
        authenticity_level = "probable"
    else:
        authenticity_level = "risky"

    email.deliverability_score = deliverability
    email.identity_score = identity_score
    email.evidence_score = evidence_score
    email.confidence_score = confidence
    email.score = confidence
    email.authenticity_level = authenticity_level
    email.is_catch_all = catch_all
    email.is_disposable = disposable
    email.domain_matches_brand = domain_matches
    email.evidence_count = len(evidence)
    email.last_verified_at = utc_now()
    email.verification_summary = {
        "provider": provider_name,
        "provider_result": status_value,
        "domain_deliverable": bool(result_data.get("domain_deliverable", deliverability > 0)),
        "official_website_evidence": official_evidence,
        "identity": identity_context,
        "evidence_ids": [str(item.id) for item in evidence],
        "manual_review_approved": manual_approved,
        "third_party_review": result_data.get("third_party_review"),
        "provider_errors": result_data.get("provider_errors", []),
        "verification_server": result_data.get("verification_server", provider_name),
        "resolution": result_data.get("resolution"),
        "local_observation": result_data.get("local_observation"),
    }
    if authenticity_level == "verified":
        email.pool = EmailPool.valid
    elif authenticity_level in {"probable", "risky"}:
        email.pool = EmailPool.manual_review
    elif status_value == EmailStatus.do_not_contact:
        email.pool = EmailPool.suppressed
    else:
        email.pool = EmailPool.invalid
    return {
        "deliverability_score": deliverability,
        "identity_score": identity_score,
        "evidence_score": evidence_score,
        "confidence_score": confidence,
        "authenticity_level": authenticity_level,
        "is_catch_all": catch_all,
        "is_disposable": disposable,
        "domain_matches_brand": domain_matches,
        "evidence_count": len(evidence),
        "verification_summary": email.verification_summary,
    }


def verify_email(db: Session, email_id: UUID, task: SearchTask | None = None) -> EmailAddress:
    email = db.get(EmailAddress, email_id)
    if email is None or email.deleted_at is not None:
        raise ValueError("Email not found")
    blocked = db.scalar(
        select(Blacklist).where(
            or_(
                (Blacklist.type == "email") & (Blacklist.value == email.normalized_address),
                (Blacklist.type == "domain") & (Blacklist.value == email.domain),
            )
        )
    )
    if blocked:
        result_data = {"result": EmailStatus.do_not_contact, "score": 0, "reason": "blacklist"}
        provider_name = "blacklist"
    else:
        if task is not None:
            provider, result_data, provider_errors = execute_email_verifier_waterfall(
                db, email.address, task
            )
        else:
            provider, result_data, provider_errors = execute_email_verifier_waterfall(
                db, email.address
            )
        if provider is not None:
            provider_name = provider.provider
            if provider_errors:
                result_data["provider_errors"] = provider_errors
            provider_config = decrypt_provider_config(getattr(provider, "config", {}) or {})
            is_local_result = str(provider_config.get("adapter") or "").lower() == "aftership_local"
            needs_paid_review = str(result_data.get("result") or "") in {
                EmailStatus.risky,
                EmailStatus.unknown,
            } or bool(result_data.get("is_catch_all"))
            if is_local_result and needs_paid_review:
                paid_providers = [
                    item
                    for item in enabled_providers(db, "email_verifier", task)
                    if str(decrypt_provider_config(item.config or {}).get("adapter") or "").lower()
                    != "aftership_local"
                ]
                unavailable = not paid_providers or bool(provider_errors)
                result_data["third_party_review"] = {
                    "required": True,
                    "available": not unavailable,
                    "message": (
                        "本地验证无法确认具体邮箱，需要第三方复核，但当前第三方服务不可用"
                        if unavailable
                        else "本地验证无法确认具体邮箱，建议第三方复核"
                    ),
                }
        else:
            provider_name = "domain_deliverability"
            try:
                validation = validate_email(email.address, check_deliverability=True)
                result_data = {
                    "result": EmailStatus.unknown,
                    "score": 50,
                    "normalized": validation.normalized,
                    "domain_deliverable": True,
                    "reason": "Mailbox verification Provider is not configured or returned no conclusive result",
                }
            except EmailNotValidError as exc:
                result_data = {"result": EmailStatus.invalid, "score": 0, "reason": str(exc)}
            if provider_errors:
                result_data["provider_errors"] = provider_errors
    observed_status = str(result_data["result"])
    observation_provider_name = provider_name
    provider_config = (
        decrypt_provider_config(getattr(provider, "config", {}) or {})
        if not blocked and provider is not None
        else {}
    )
    is_local_observation = str(provider_config.get("adapter") or "").lower() == "aftership_local"
    if is_local_observation:
        authoritative = db.scalar(
            select(EmailVerification)
            .where(
                EmailVerification.email_id == email.id,
                EmailVerification.result.in_([EmailStatus.invalid, EmailStatus.do_not_contact]),
                ~EmailVerification.provider.like("aftership_local%"),
                EmailVerification.provider != "domain_deliverability",
            )
            .order_by(EmailVerification.checked_at.desc())
        )
        if authoritative is not None:
            result_data["local_observation"] = {
                "provider": observation_provider_name,
                "result": observed_status,
                "score": result_data.get("score"),
                "is_catch_all": bool(result_data.get("is_catch_all")),
                "reason": result_data.get("reason"),
            }
            result_data["resolution"] = {
                "rule": "preserve_authoritative_third_party_result",
                "message": "保留历史第三方明确结论；本地复验仅作为补充观察",
                "authoritative_provider": authoritative.provider,
                "authoritative_result": authoritative.result,
            }
            result_data["result"] = authoritative.result
            result_data["score"] = (
                0
                if authoritative.result in {EmailStatus.invalid, EmailStatus.do_not_contact}
                else authoritative.deliverability_score
            )
            result_data["is_catch_all"] = authoritative.is_catch_all
            provider_name = authoritative.provider

    result_data["verification_server"] = observation_provider_name
    status = str(result_data["result"])
    allowed = {item.value for item in EmailStatus}
    email.status = status if status in allowed else EmailStatus.unknown
    email.deliverability_score = max(0, min(100, int(result_data.get("score") or 0)))
    rules = get_system_settings(db)["email_rules"]
    if email.status == EmailStatus.valid and email.deliverability_score < int(rules["valid_score"]):
        email.status = EmailStatus.risky
    authenticity = assess_email_authenticity(db, email, result_data, provider_name=provider_name)
    db.add(
        EmailVerification(
            email_id=email.id,
            provider=observation_provider_name,
            result=observed_status,
            score=email.confidence_score,
            deliverability_score=email.deliverability_score,
            identity_score=email.identity_score,
            evidence_score=email.evidence_score,
            confidence_score=email.confidence_score,
            authenticity_level=email.authenticity_level,
            is_catch_all=email.is_catch_all,
            is_disposable=email.is_disposable,
            domain_matches_brand=email.domain_matches_brand,
            raw_result=jsonable_encoder({**result_data, "authenticity": authenticity}),
            checked_at=utc_now(),
        )
    )
    audit(
        db,
        "email.verify",
        "email",
        str(email.id),
        after={**result_data, "authenticity": authenticity},
    )
    emit(
        db,
        "email.verified",
        {
            "email_id": email.id,
            "status": email.status,
            "authenticity_level": email.authenticity_level,
            "confidence_score": email.confidence_score,
            "provider": provider_name,
        },
    )
    refresh_contact_status(db, email.contact_id)
    return email


def review_email(db: Session, email_id: UUID, decision: str, reason: str | None) -> EmailAddress:
    email = db.get(EmailAddress, email_id)
    if email is None or email.deleted_at is not None:
        raise ValueError("Email not found")
    before = {"status": email.status, "pool": email.pool}
    if decision == "approve":
        existing = db.scalar(
            select(SourceEvidence.id).where(
                SourceEvidence.entity_type == "email",
                SourceEvidence.entity_id == str(email.id),
                SourceEvidence.provider == "manual_review",
            )
        )
        if existing is None:
            db.add(
                SourceEvidence(
                    entity_type="email",
                    entity_id=str(email.id),
                    source_type=SourceType.manual_entry,
                    title="Email identity approved by a reviewer",
                    excerpt=reason,
                    confidence=95,
                    provider="manual_review",
                )
            )
            db.flush()
        assess_email_authenticity(
            db,
            email,
            {"result": email.status, "score": email.deliverability_score},
            provider_name=str(
                (email.verification_summary or {}).get("provider") or "manual_review"
            ),
            manual_approved=True,
        )
    elif decision == "suppress":
        email.status = EmailStatus.do_not_contact
        email.pool = EmailPool.suppressed
        email.authenticity_level = "invalid"
        email.confidence_score = 0
        email.score = 0
        create_blacklist(
            db, BlacklistCreate(type="email", value=email.normalized_address, reason=reason)
        )
    else:
        email.status = EmailStatus.invalid
        email.pool = EmailPool.invalid
        email.authenticity_level = "invalid"
        email.confidence_score = 0
        email.score = 0
    audit(
        db,
        "email.review",
        "email",
        str(email.id),
        before=before,
        after={"decision": decision, "reason": reason},
    )
    refresh_contact_status(db, email.contact_id)
    return email


def email_authenticity_detail(db: Session, email_id: UUID) -> dict:
    email = db.get(EmailAddress, email_id)
    if email is None or email.deleted_at is not None:
        raise ValueError("Email not found")
    evidence = list(
        db.scalars(
            select(SourceEvidence)
            .where(SourceEvidence.entity_type == "email", SourceEvidence.entity_id == str(email.id))
            .order_by(SourceEvidence.created_at.desc())
        )
    )
    verifications = list(
        db.scalars(
            select(EmailVerification)
            .where(EmailVerification.email_id == email.id)
            .order_by(EmailVerification.created_at.desc())
        )
    )
    result = to_dict(email)
    result["evidence"] = [to_dict(item) for item in evidence]
    result["verification_history"] = [to_dict(item) for item in verifications]
    return result


def dedup_check(db: Session, organization_id=None) -> dict:
    duplicate_emails = db.execute(
        select(EmailAddress.normalized_address, func.count(EmailAddress.id))
        .where(EmailAddress.deleted_at.is_(None), EmailAddress.organization_id == organization_id)
        .group_by(EmailAddress.normalized_address)
        .having(func.count(EmailAddress.id) > 1)
    ).all()
    duplicate_brands = db.execute(
        select(Brand.normalized_name, func.count(Brand.id))
        .where(Brand.deleted_at.is_(None), Brand.organization_id == organization_id)
        .group_by(Brand.normalized_name)
        .having(func.count(Brand.id) > 1)
    ).all()
    duplicate_contacts = db.execute(
        select(Contact.full_name, func.count(Contact.id))
        .where(Contact.deleted_at.is_(None), Contact.organization_id == organization_id)
        .group_by(Contact.full_name)
        .having(func.count(Contact.id) > 1)
    ).all()

    # Fuzzy matches
    from app.modules.fuzzy_dedup import find_fuzzy_brands, find_fuzzy_contacts, find_fuzzy_emails

    return {
        "exact": {
            "duplicate_emails": [
                {
                    "value": value,
                    "count": count,
                    "ids": [
                        str(item)
                        for item in db.scalars(
                            select(EmailAddress.id).where(
                                EmailAddress.normalized_address == value,
                                EmailAddress.deleted_at.is_(None),
                                EmailAddress.organization_id == organization_id,
                            )
                        ).all()
                    ],
                }
                for value, count in duplicate_emails
            ],
            "duplicate_brands": [
                {
                    "value": value,
                    "count": count,
                    "ids": [
                        str(item)
                        for item in db.scalars(
                            select(Brand.id).where(
                                Brand.normalized_name == value, Brand.deleted_at.is_(None),
                                Brand.organization_id == organization_id,
                            )
                        ).all()
                    ],
                }
                for value, count in duplicate_brands
            ],
            "duplicate_contacts": [
                {
                    "value": value,
                    "count": count,
                    "ids": [
                        str(item)
                        for item in db.scalars(
                            select(Contact.id).where(
                                Contact.full_name == value, Contact.deleted_at.is_(None),
                                Contact.organization_id == organization_id,
                            )
                        ).all()
                    ],
                }
                for value, count in duplicate_contacts
            ],
        },
        "fuzzy": {
            "brands": find_fuzzy_brands(db, organization_id=organization_id),
            "contacts": find_fuzzy_contacts(db, organization_id=organization_id),
            "emails": find_fuzzy_emails(db, organization_id=organization_id),
        },
    }


def merge_duplicates(db: Session, payload: DedupMergeRequest) -> dict:
    model = {"brand": Brand, "contact": Contact, "email": EmailAddress}[payload.entity_type]
    primary = db.get(model, payload.primary_id)
    if primary is None or getattr(primary, "deleted_at", None) is not None:
        raise ValueError("Primary record not found")
    merged: list[str] = []
    for duplicate_id in payload.duplicate_ids:
        if duplicate_id == payload.primary_id:
            continue
        duplicate = db.get(model, duplicate_id)
        if duplicate is None or getattr(duplicate, "deleted_at", None) is not None:
            continue
        if payload.entity_type == "brand":
            db.execute(
                update(Website).where(Website.brand_id == duplicate.id).values(brand_id=primary.id)
            )
            db.execute(
                update(ContactPosition)
                .where(ContactPosition.brand_id == duplicate.id)
                .values(brand_id=primary.id)
            )
        elif payload.entity_type == "contact":
            db.execute(
                update(ContactPosition)
                .where(ContactPosition.contact_id == duplicate.id)
                .values(contact_id=primary.id)
            )
            db.execute(
                update(EmailAddress)
                .where(EmailAddress.contact_id == duplicate.id)
                .values(contact_id=primary.id)
            )
        else:
            db.execute(
                update(EmailVerification)
                .where(EmailVerification.email_id == duplicate.id)
                .values(email_id=primary.id)
            )
        duplicate.deleted_at = datetime.now(UTC)
        merged.append(str(duplicate.id))
    audit(
        db,
        "dedup.merge",
        payload.entity_type,
        str(payload.primary_id),
        after={"merged_ids": merged},
    )
    emit(
        db,
        "duplicate.merged",
        {
            "entity_type": payload.entity_type,
            "primary_id": payload.primary_id,
            "merged_ids": merged,
        },
    )
    return {"status": "merged", "primary_id": str(payload.primary_id), "merged_ids": merged}


def import_rows(
    db: Session,
    entity_type: str,
    rows: list[dict[str, str]],
    field_mapping: dict[str, str] | None = None,
    *,
    authorization=None,
) -> dict:
    if field_mapping:
        rows = [
            {target: row.get(source, "") for target, source in field_mapping.items()}
            | {key: value for key, value in row.items() if key not in field_mapping.values()}
            for row in rows
        ]
    created = 0
    skipped = 0
    errors: list[dict] = []
    for index, row in enumerate(rows, start=2):
        try:
            with db.begin_nested():
                if entity_type == "brands":
                    create_brand(
                        db,
                        BrandCreate(
                            name=row.get("name") or row.get("brand_name") or "",
                            company_name=row.get("company_name") or None,
                            website=row.get("website") or None,
                            country=row.get("country") or None,
                            category=row.get("category") or None,
                        ),
                        source_type=SourceType.manual_import,
                        provider="file_import",
                        organization_id=authorization.organization_id if authorization else None,
                        organization_unit_id=(
                            authorization.organization_unit_id if authorization else None
                        ),
                        owner_id=authorization.user_id if authorization else None,
                    )
                elif entity_type == "contacts":
                    brand_id = UUID(row["brand_id"]) if row.get("brand_id") else None
                    if brand_id is not None and authorization is not None:
                        from app.authz.policy import load_scoped_entity

                        load_scoped_entity(
                            db, Brand, brand_id, authorization, resource="brands"
                        )
                    create_contact(
                        db,
                        ContactCreate(
                            brand_id=brand_id,
                            company_id=UUID(row["company_id"]) if row.get("company_id") else None,
                            first_name=row.get("first_name") or "",
                            last_name=row.get("last_name") or "",
                            title=row.get("title") or "",
                            linkedin_url=row.get("linkedin_url") or None,
                        ),
                        provider="file_import",
                        organization_id=authorization.organization_id if authorization else None,
                        organization_unit_id=(
                            authorization.organization_unit_id if authorization else None
                        ),
                        owner_id=authorization.user_id if authorization else None,
                    )
                elif entity_type == "emails":
                    contact_id = UUID(row["contact_id"]) if row.get("contact_id") else None
                    if contact_id is not None and authorization is not None:
                        from app.authz.policy import load_scoped_entity

                        load_scoped_entity(
                            db, Contact, contact_id, authorization, resource="contacts"
                        )
                    create_email(
                        db,
                        EmailCreate(
                            contact_id=contact_id,
                            address=row.get("address") or row.get("email") or "",
                            type=row.get("type") or "personal",
                        ),
                        provider="file_import",
                        organization_id=authorization.organization_id if authorization else None,
                        organization_unit_id=(
                            authorization.organization_unit_id if authorization else None
                        ),
                        owner_id=authorization.user_id if authorization else None,
                    )
                else:
                    raise ValueError("entity_type must be brands, contacts, or emails")
            created += 1
        except Exception as exc:
            errors.append({"row": index, "error": str(exc)})
            skipped += 1
    audit(db, "import.complete", entity_type, after={"created": created, "skipped": skipped})
    emit(
        db, "import.completed", {"entity_type": entity_type, "created": created, "skipped": skipped}
    )
    return {
        "status": "completed",
        "entity_type": entity_type,
        "created": created,
        "skipped": skipped,
        "errors": errors[:100],
    }


def export_csv(
    db: Session,
    entity_type: str,
    filters: dict[str, str | int | float | bool] | None = None,
    *,
    authorization=None,
) -> bytes:
    model = {
        "brands": Brand,
        "contacts": Contact,
        "emails": EmailAddress,
        "tasks": SearchTask,
        "audit_logs": AuditLog,
    }[entity_type]
    statement = select(model).order_by(model.created_at.desc())
    if hasattr(model, "deleted_at"):
        statement = statement.where(model.deleted_at.is_(None))
    if authorization is not None and entity_type in {"brands", "contacts", "emails", "tasks"}:
        from app.authz.scope import apply_scope

        statement = apply_scope(statement, model, db, authorization, entity_type)
    elif authorization is not None and entity_type == "audit_logs" and "admin:*" not in authorization.permissions:
        statement = statement.where(AuditLog.organization_id == str(authorization.organization_id))
    for key, value in (filters or {}).items():
        column = getattr(model, key, None)
        if column is not None:
            statement = statement.where(column == value)
    items = [to_dict(item) for item in db.scalars(statement).all()]
    buffer = io.StringIO()
    if items:
        writer = csv.DictWriter(buffer, fieldnames=list(items[0].keys()))
        writer.writeheader()
        for item in items:
            writer.writerow({key: _csv_value(value) for key, value in item.items()})
    audit(db, "export.complete", entity_type, after={"rows": len(items)})
    return buffer.getvalue().encode("utf-8-sig")


def export_selected_emails_csv(db: Session, email_ids: list[UUID]) -> tuple[bytes, int]:
    unique_ids = list(dict.fromkeys(email_ids))
    statement = (
        select(EmailAddress)
        .where(EmailAddress.id.in_(unique_ids), EmailAddress.deleted_at.is_(None))
        .order_by(EmailAddress.created_at.desc())
    )
    items = [to_dict(item) for item in db.scalars(statement).all()]
    buffer = io.StringIO()
    if items:
        writer = csv.DictWriter(buffer, fieldnames=list(items[0].keys()))
        writer.writeheader()
        for item in items:
            writer.writerow({key: _csv_value(value) for key, value in item.items()})
    audit(
        db,
        "email.export_selected",
        "email",
        after={"requested": len(unique_ids), "rows": len(items)},
    )
    return buffer.getvalue().encode("utf-8-sig"), len(items)


def export_selected_contacts_csv(db: Session, contact_ids: list[UUID]) -> tuple[bytes, int]:
    unique_ids = list(dict.fromkeys(contact_ids))
    statement = (
        select(Contact, ContactPosition.title, Brand.name)
        .outerjoin(
            ContactPosition,
            (ContactPosition.contact_id == Contact.id)
            & ContactPosition.is_current.is_(True)
            & ContactPosition.deleted_at.is_(None),
        )
        .outerjoin(Brand, ContactPosition.brand_id == Brand.id)
        .where(Contact.id.in_(unique_ids), Contact.deleted_at.is_(None))
        .order_by(Contact.created_at.desc())
    )
    email_rows = db.execute(
        select(EmailAddress.contact_id, EmailAddress.address)
        .where(EmailAddress.contact_id.in_(unique_ids), EmailAddress.deleted_at.is_(None))
        .order_by(EmailAddress.address)
    ).all()
    emails_by_contact: dict[UUID, list[str]] = {}
    for contact_id, address in email_rows:
        emails_by_contact.setdefault(contact_id, []).append(address)
    items = []
    for contact, title, brand_name in db.execute(statement).all():
        item = to_dict(contact)
        item.update(
            {
                "title": title,
                "brand_name": brand_name,
                "email_addresses": "; ".join(emails_by_contact.get(contact.id, [])),
            }
        )
        items.append(item)
    buffer = io.StringIO()
    if items:
        writer = csv.DictWriter(buffer, fieldnames=list(items[0].keys()))
        writer.writeheader()
        for item in items:
            writer.writerow({key: _csv_value(value) for key, value in item.items()})
    audit(
        db,
        "contact.export_selected",
        "contact",
        after={"requested": len(unique_ids), "rows": len(items)},
    )
    return buffer.getvalue().encode("utf-8-sig"), len(items)


def dashboard(db: Session, *, authorization=None) -> dict:
    if authorization is None:
        counts = current_counts(db)
    else:
        from app.authz.scope import apply_scope

        def scoped_count(model, resource: str, *filters) -> int:
            statement = select(model.id).where(*filters)
            statement = apply_scope(statement, model, db, authorization, resource)
            return db.scalar(select(func.count()).select_from(statement.subquery())) or 0

        counts = {
            "tasks": scoped_count(SearchTask, "tasks"),
            "brands": scoped_count(Brand, "brands", Brand.deleted_at.is_(None)),
            "contacts": scoped_count(Contact, "contacts", Contact.deleted_at.is_(None)),
            "emails": scoped_count(
                EmailAddress, "emails", EmailAddress.deleted_at.is_(None)
            ),
        }
    pending_candidates = (
        db.scalar(
            select(func.count())
            .select_from(DiscoveryCandidate)
            .where(DiscoveryCandidate.status == "pending")
        )
        or 0
    )
    valid_contacts = (
        db.scalar(
            select(func.count())
            .select_from(Contact)
            .where(Contact.deleted_at.is_(None), Contact.status == "valid")
        )
        or 0
    )
    valid_emails = (
        db.scalar(
            select(func.count())
            .select_from(EmailAddress)
            .where(EmailAddress.deleted_at.is_(None), EmailAddress.pool == EmailPool.valid)
        )
        or 0
    )
    review_emails = (
        db.scalar(
            select(func.count())
            .select_from(EmailAddress)
            .where(EmailAddress.deleted_at.is_(None), EmailAddress.pool == EmailPool.manual_review)
        )
        or 0
    )
    pool_rows = db.execute(
        select(EmailAddress.pool, func.count(EmailAddress.id))
        .where(EmailAddress.deleted_at.is_(None))
        .group_by(EmailAddress.pool)
    ).all()
    pool_counts = {pool: count for pool, count in pool_rows}
    if authorization is not None and "admin:*" not in authorization.permissions:
        valid_contacts = scoped_count(
            Contact,
            "contacts",
            Contact.deleted_at.is_(None),
            Contact.status == "valid",
        )
        valid_emails = scoped_count(
            EmailAddress,
            "emails",
            EmailAddress.deleted_at.is_(None),
            EmailAddress.pool == EmailPool.valid,
        )
        review_emails = scoped_count(
            EmailAddress,
            "emails",
            EmailAddress.deleted_at.is_(None),
            EmailAddress.pool == EmailPool.manual_review,
        )
        pool_counts = {}
        for pool in EmailPool:
            pool_counts[pool.value] = scoped_count(
                EmailAddress,
                "emails",
                EmailAddress.deleted_at.is_(None),
                EmailAddress.pool == pool,
            )
        accessible_tasks = apply_scope(
            select(SearchTask.id), SearchTask, db, authorization, "tasks"
        )
        pending_candidates = (
            db.scalar(
                select(func.count(func.distinct(DiscoveryCandidateHit.candidate_id)))
                .join(
                    DiscoveryCandidate,
                    DiscoveryCandidate.id == DiscoveryCandidateHit.candidate_id,
                )
                .where(
                    DiscoveryCandidateHit.task_id.in_(accessible_tasks),
                    DiscoveryCandidate.status == "pending",
                )
            )
            or 0
        )
    providers = []
    for provider in db.scalars(select(ProviderConfig).order_by(ProviderConfig.priority)).all():
        used = (
            db.scalar(
                select(func.sum(ApiUsage.calls)).where(ApiUsage.provider == provider.provider)
            )
            or 0
        )
        providers.append(
            {
                "id": str(provider.id),
                "name": provider.provider,
                "type": provider.type,
                "enabled": provider.enabled,
                "used": used,
                "quota": provider.quota,
            }
        )
    events = []
    if authorization is None or "admin:*" in authorization.permissions:
        events = [
            to_dict(item)
            for item in db.scalars(
                select(DomainEvent).order_by(DomainEvent.created_at.desc()).limit(8)
            ).all()
        ]
    return {
        "metrics": {
            **counts,
            "discovered_contacts": counts["contacts"],
            "valid_contacts": valid_contacts,
            "valid_emails": valid_emails,
            "review_emails": review_emails,
            "pending_candidates": pending_candidates,
        },
        "email_pools": {pool.value: pool_counts.get(pool.value, 0) for pool in EmailPool},
        "providers": providers,
        "events": events,
    }


ASSIGNABLE_RESOURCE_MODELS = {
    "tasks": SearchTask,
    "brands": Brand,
    "contacts": Contact,
    "emails": EmailAddress,
}


def assign_business_data(
    db: Session,
    *,
    resource: str,
    entities: list,
    target_unit: OrganizationUnit,
    target_owner: User | None,
    actor: User,
    reason: str,
) -> dict:
    """Atomically move already-authorized business records to another unit."""
    if resource not in ASSIGNABLE_RESOURCE_MODELS:
        raise ValueError("Unsupported assignment resource")
    if target_unit.status != "active":
        raise ValueError("Target organization unit is disabled")
    if target_unit.organization_id != actor.organization_id:
        raise ValueError("Cross-organization assignment is not allowed")
    if target_owner is not None and (
        target_owner.deleted_at is not None
        or target_owner.status != "active"
        or target_owner.organization_id != target_unit.organization_id
        or target_owner.organization_unit_id != target_unit.id
    ):
        raise ValueError("Target owner must be an active member of the target unit")

    changes = []
    for entity in entities:
        before = {
            "department_id": str(entity.department_id) if entity.department_id else None,
            "owner_id": str(entity.owner_id) if entity.owner_id else None,
        }
        entity.department_id = target_unit.id
        entity.owner_id = target_owner.id if target_owner else None
        entity.updated_by = actor.id
        after = {
            "department_id": str(target_unit.id),
            "owner_id": str(target_owner.id) if target_owner else None,
            "assigned_by": str(actor.id),
            "reason": reason,
        }
        audit(
            db,
            f"{resource.rstrip('s')}.assign",
            resource.rstrip("s"),
            str(entity.id),
            before=before,
            after=after,
        )
        emit(
            db,
            "data.assignment.changed",
            {
                "entity_id": str(entity.id),
                "resource": resource,
                **after,
            },
        )
        changes.append({"id": str(entity.id), **after})
    db.flush()
    return {"resource": resource, "assigned": len(changes), "items": changes}


def share_business_data(
    db: Session, *, resource: str, entities: list, target_unit: OrganizationUnit,
    actor: User, reason: str,
) -> dict:
    """Grant read access without altering organization, unit, or owner fields."""
    if target_unit.status != "active" or target_unit.organization_id != actor.organization_id:
        raise ValueError("Target organization unit is not available")
    created = 0
    for entity in entities:
        if entity.organization_id != target_unit.organization_id:
            raise ValueError("Cross-organization sharing is not allowed")
        existing = db.scalar(select(DataShareGrant).where(
            DataShareGrant.resource == resource,
            DataShareGrant.entity_id == entity.id,
            DataShareGrant.target_unit_id == target_unit.id,
            DataShareGrant.revoked_at.is_(None),
        ))
        if existing is None:
            db.add(DataShareGrant(
                resource=resource, entity_id=entity.id,
                organization_id=entity.organization_id,
                source_unit_id=entity.department_id,
                target_unit_id=target_unit.id, permission="read", reason=reason,
                created_by=actor.id,
            ))
            created += 1
        audit(db, f"{resource.rstrip('s')}.share", resource.rstrip('s'), str(entity.id),
              after={"target_unit_id": str(target_unit.id), "reason": reason})
    db.flush()
    return {"resource": resource, "shared": created, "target_unit_id": str(target_unit.id)}


def create_provider_config(db: Session, payload: ProviderConfigCreate) -> ProviderConfig:
    values = payload.model_dump(mode="json")
    _validate_provider_config(values["type"], values["config"])
    values["config"] = encrypt_provider_config(values["config"])
    provider = ProviderConfig(**values)
    db.add(provider)
    db.flush()
    audit(
        db,
        "provider_config.create",
        "provider_config",
        str(provider.id),
        after=_masked_config(provider.config),
    )
    return provider


def vendor_credential_public(credential: VendorCredential) -> dict:
    configured = bool(str(credential.encrypted_api_key or "").strip())
    return {
        "id": str(credential.id),
        "vendor": credential.vendor,
        "display_name": (
            adapter_for(credential.vendor).display_name
            if adapter_for(credential.vendor)
            else credential.vendor.title()
        ),
        "enabled": credential.enabled,
        "api_key": "********" if configured else "",
        "api_key_configured": configured,
        "last_tested_at": credential.last_tested_at,
        "last_test_ok": credential.last_test_ok,
        "last_test_error": credential.last_test_error,
        "updated_at": credential.updated_at,
    }


def list_vendor_credentials(db: Session) -> list[dict]:
    credentials = db.scalars(
        select(VendorCredential)
        .where(VendorCredential.vendor.in_(["apollo", "hunter"]))
        .order_by(VendorCredential.vendor.asc())
    ).all()
    return [vendor_credential_public(item) for item in credentials]


def update_vendor_credential(
    db: Session, credential: VendorCredential, payload: VendorCredentialUpdate
) -> VendorCredential:
    changes = payload.model_dump(exclude_unset=True)
    api_key = changes.pop("api_key", None)
    if api_key is not None and api_key.strip() not in {"", "********"}:
        credential.encrypted_api_key = encrypt_secret(api_key.strip())
        credential.last_test_ok = None
        credential.last_test_error = None
    for key, value in changes.items():
        setattr(credential, key, value)
    audit(
        db,
        "vendor_credential.update",
        "vendor_credential",
        str(credential.id),
        after={
            "vendor": credential.vendor,
            "enabled": credential.enabled,
            "api_key_configured": bool(credential.encrypted_api_key),
        },
    )
    return credential


def test_vendor_credential(db: Session, credential: VendorCredential) -> dict:
    adapter = adapter_for(credential.vendor)
    if adapter is None:
        raise ValueError(f"Unsupported Vendor: {credential.vendor}")
    if not credential.encrypted_api_key:
        result = ProviderResult(
            False,
            credential.vendor,
            error_code="missing_api_key",
            error_message="API Key is not configured",
        )
    else:
        result = adapter.check_availability(decrypt_secret(credential.encrypted_api_key))
    credential.last_tested_at = utc_now()
    credential.last_test_ok = result.ok
    credential.last_test_error = (
        None
        if result.ok
        else (result.error_message or result.error_code or "Connection test failed")
    )
    audit(
        db,
        "vendor_credential.test",
        "vendor_credential",
        str(credential.id),
        after={"vendor": credential.vendor, "ok": result.ok, "error_code": result.error_code},
    )
    return {
        "ok": result.ok,
        "vendor": credential.vendor,
        "error_code": result.error_code,
        "error_message": credential.last_test_error,
    }


def get_vendor_strategy(db: Session) -> dict:
    strategy = db.scalar(select(VendorStrategy).where(VendorStrategy.name == "default"))
    if strategy is None:
        strategy = VendorStrategy(
            name="default",
            primary_vendor="apollo",
            fallback_vendors=["prospeo", "hunter"],
            verification_vendor="zerobounce",
            adapter_version=ADAPTER_VERSION,
            local_verification_mode="disabled",
            local_verification_rollout=0,
            local_verification_sample=10,
        )
        db.add(strategy)
        db.flush()
    return to_dict(strategy)


def update_vendor_strategy(db: Session, payload: VendorStrategyUpdate) -> dict:
    strategy = db.scalar(select(VendorStrategy).where(VendorStrategy.name == "default"))
    if strategy is None:
        strategy = VendorStrategy(name="default", primary_vendor=payload.primary_vendor)
        db.add(strategy)
    strategy.primary_vendor = payload.primary_vendor
    strategy.fallback_vendors = list(payload.fallback_vendors)
    strategy.verification_vendor = payload.verification_vendor
    strategy.adapter_version = ADAPTER_VERSION
    strategy.local_verification_mode = payload.local_verification_mode
    strategy.local_verification_rollout = payload.local_verification_rollout
    strategy.local_verification_sample = payload.local_verification_sample
    db.flush()
    audit(
        db, "vendor_strategy.update", "vendor_strategy", str(strategy.id), after=to_dict(strategy)
    )
    return to_dict(strategy)


def _vendor_credential(db: Session, vendor: str) -> VendorCredential | None:
    return db.scalar(
        select(VendorCredential).where(
            VendorCredential.vendor == vendor,
            VendorCredential.enabled.is_(True),
        )
    )


def ensure_task_vendor_plan(db: Session, task: SearchTask) -> TaskVendorPlan:
    plan = db.scalar(select(TaskVendorPlan).where(TaskVendorPlan.task_id == task.id))
    if plan is not None:
        return plan

    # New search tasks always use an explicit, task-frozen Vendor selection.
    filters = task.filters if isinstance(task.filters, dict) else {}
    selected = filters.get("selected_vendors")
    selected_vendors_list = (
        list(dict.fromkeys(str(v).strip().lower() for v in selected))
        if isinstance(selected, list)
        else []
    )
    if selected_vendors_list and not set(selected_vendors_list) <= {"apollo", "hunter"}:
        raise ValueError(
            "This task has an invalid Vendor selection. Select Apollo, Hunter, or both."
        )
    if not selected_vendors_list:
        # Read-only compatibility plan for historical tasks. The task executor
        # rejects this mode; it remains only so old records and audit screens load.
        strategy = get_vendor_strategy(db)
        plan = TaskVendorPlan(
            task_id=task.id,
            primary_vendor=str(strategy["primary_vendor"]),
            fallback_vendors=list(strategy.get("fallback_vendors") or []),
            verification_vendor=strategy.get("verification_vendor"),
            adapter_version=str(strategy.get("adapter_version") or ADAPTER_VERSION),
            local_verification_mode=str(strategy.get("local_verification_mode") or "disabled"),
            local_verification_rollout=int(strategy.get("local_verification_rollout") or 0),
            local_verification_sample=int(strategy.get("local_verification_sample") or 10),
            execution_mode="legacy_waterfall",
            selected_vendors=[],
            pipeline_source="legacy_read_only",
            vendor_routes={},
        )
        db.add(plan)
        db.flush()
        return plan
    execution_mode = "apollo_hunter"
    if selected_vendors_list == ["apollo"]:
        execution_mode = "apollo_only"
    elif selected_vendors_list == ["hunter"]:
        execution_mode = "hunter_only"
    pipeline_source = "user_selection"
    vendor_routes: dict = {}

    for vendor in selected_vendors_list:
        adapter = adapter_for(vendor)
        vc = _vendor_credential(db, vendor)
        vendor_routes[vendor] = {
                "vendor": vendor,
                "adapter_version": adapter.adapter_version if adapter else ADAPTER_VERSION,
                "credential_id": str(vc.id) if vc else None,
                "stages": {
                    "company_search": {
                        "provider": f"{vendor}-company-search",
                        "enabled": True,
                    },
                    "contact_search": {
                        "provider": f"{vendor}-contact-search",
                        "enabled": True,
                    },
                },
                "email_method": (
                    "from_contact_search" if vendor == "apollo"
                    else "email_finder_domain_search"
                ),
        }
        if vendor == "apollo":
            vendor_routes[vendor]["stages"]["contact_enrichment"] = {
                    "provider": f"{vendor}-contact-search",
                    "enabled": True,
            }
        elif vendor == "hunter":
            vendor_routes[vendor]["stages"]["email_finder"] = {
                    "provider": f"{vendor}-email-finder",
                    "enabled": True,
            }
            vendor_routes[vendor]["stages"]["email_verifier"] = {
                    "provider": f"{vendor}-email-verifier",
                    "enabled": True,
            }

    plan = TaskVendorPlan(
        task_id=task.id,
        # Retained columns are populated for additive-schema compatibility only.
        primary_vendor=selected_vendors_list[0],
        fallback_vendors=[],
        verification_vendor="hunter" if "hunter" in selected_vendors_list else None,
        adapter_version=ADAPTER_VERSION,
        local_verification_mode="disabled",
        local_verification_rollout=0,
        local_verification_sample=10,
        execution_mode=execution_mode,
        selected_vendors=selected_vendors_list,
        pipeline_source=pipeline_source,
        vendor_routes=vendor_routes,
    )
    db.add(plan)
    db.flush()
    audit(db, "task_vendor_plan.create", "search_task", str(task.id), after=to_dict(plan))
    return plan


def update_provider_config(
    db: Session, provider: ProviderConfig, payload: ProviderConfigUpdate
) -> ProviderConfig:
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if key == "config" and value is not None:
            value = _merge_provider_config(provider.config or {}, value)
        setattr(provider, key, value)
    _validate_provider_config(provider.type, decrypt_provider_config(provider.config or {}))
    audit(
        db,
        "provider_config.update",
        "provider_config",
        str(provider.id),
        after=_masked_config(provider.config),
    )
    return provider


def _validate_provider_config(provider_type: str, config: dict) -> None:
    adapter = str(config.get("adapter") or "").strip().lower()
    vendor_adapters = {"apollo", "hunter", "zerobounce", *CONFIGURABLE_CATALOG_ADAPTERS}
    if adapter in vendor_adapters:
        endpoint_url = str(config.get("endpoint_url") or "").strip()
        parsed = urlparse(endpoint_url)
        if not endpoint_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Vendor Provider must have a valid endpoint URL")
        quota_endpoint_url = str(config.get("quota_endpoint_url") or "").strip()
        parsed = urlparse(quota_endpoint_url)
        if not quota_endpoint_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "Vendor Provider must have a valid quota endpoint URL for automatic failover"
            )
        if not str(config.get("quota_remaining_path") or "").strip():
            raise ValueError("Vendor Provider must configure the remaining-quota response path")
    if adapter in CONFIGURABLE_CATALOG_ADAPTERS:
        supported_types = CATALOG_SUPPORTED_TYPES[adapter]
        if provider_type not in supported_types:
            raise ValueError(f"{adapter} does not support Provider type: {provider_type}")
        if (
            not str(config.get("api_key_header") or "").strip()
            and not str(config.get("api_key_query_param") or "").strip()
        ):
            raise ValueError("Catalog Provider must configure an API key header or query parameter")
        if (
            not str(config.get("quota_api_key_header") or "").strip()
            and not str(config.get("quota_api_key_query_param") or "").strip()
        ):
            raise ValueError("Catalog Provider must configure quota API key authentication")
        request_method = str(config.get("request_method") or "").strip().upper()
        if request_method not in {"GET", "POST"}:
            raise ValueError("Catalog Provider request method must be GET or POST")
        for key, label in {
            "request_headers": "request headers",
            "request_query": "request query",
            "request_body": "request body",
        }.items():
            value = config.get(key, {})
            if not isinstance(value, dict):
                raise ValueError(f"Catalog Provider {label} must be a JSON object")
        if provider_type == "email_verifier":
            if not str(config.get("result_path") or "").strip():
                raise ValueError("Catalog email verifier must configure a result response path")
            if not isinstance(config.get("result_map", {}), dict):
                raise ValueError("Catalog email verifier result map must be a JSON object")
        else:
            if not str(config.get("response_items_path") or "").strip():
                raise ValueError("Catalog Provider must configure a response items path")
            if not isinstance(config.get("response_field_map"), dict):
                raise ValueError("Catalog Provider response field map must be a JSON object")
    auxiliary_endpoints = {
        "domain_finder_endpoint_url": "Hunter Domain Finder",
        "email_count_endpoint_url": "Hunter Email Count",
        "bulk_enrichment_endpoint_url": "Apollo bulk enrichment",
    }
    for key, capability in auxiliary_endpoints.items():
        endpoint_url = str(config.get(key) or "").strip()
        if not endpoint_url:
            continue
        parsed = urlparse(endpoint_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{capability} endpoint URL must be a valid http(s) URL")
    if "bulk_enrichment_batch_size" in config:
        try:
            batch_size = int(config["bulk_enrichment_batch_size"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Apollo bulk enrichment batch size must be between 1 and 10") from exc
        if not 1 <= batch_size <= 10:
            raise ValueError("Apollo bulk enrichment batch size must be between 1 and 10")
    if provider_type != "company_search":
        return
    semantics = str(config.get("country_semantics") or "unknown").strip().casefold()
    allowed = {"unknown", "headquarters", "registered", "origin", "operating"}
    if semantics not in allowed:
        raise ValueError(
            "company_search Provider 的 country_semantics 必须是 unknown、headquarters、"
            "registered、origin 或 operating"
        )


def _merge_provider_config(existing: dict, changes: dict) -> dict:
    previous = decrypt_provider_config(existing)
    merged = previous | changes
    for key, value in changes.items():
        if (
            _is_sensitive_config_key(key)
            and isinstance(value, str)
            and value in {"", "********"}
            and previous.get(key)
        ):
            merged[key] = previous[key]
    # These values were formerly written by the service. Availability is now
    # determined directly from the configured vendor quota endpoint instead.
    for key in {
        "quota_soft_threshold",
        "circuit_open_until",
        "circuit_last_error",
        "quota_remaining",
        "quota_used",
        "quota_reset_at",
    }:
        merged.pop(key, None)
    return encrypt_provider_config(merged)


def list_provider_configs(db: Session, page: int, page_size: int) -> dict:
    result = list_page(db, ProviderConfig, page, page_size)
    for item in result["items"]:
        item["config"] = _masked_config(item.get("config") or {})
    return result


def provider_public(provider: ProviderConfig) -> dict:
    item = to_dict(provider)
    item["config"] = _masked_config(item.get("config") or {})
    return item


def test_provider(db: Session, provider: ProviderConfig) -> dict:
    config = decrypt_provider_config(provider.config or {})
    connection_result = test_catalog_provider_connection(provider, config)
    if connection_result is not None:
        result = connection_result
    else:
        test_payload = provider_test_payload(provider, config)
        quota_check = provider_quota_available(provider, config, test_payload)
        result = (
            quota_check
            if quota_check is not None and not quota_check.ok
            else execute_provider(provider, test_payload)
        )
    error_message = _provider_test_error_message(provider, config, result)
    if result.ok:
        record_usage(db, provider, result.cost)
    audit(
        db,
        "provider_config.test",
        "provider_config",
        str(provider.id),
        after={"ok": result.ok, "error": error_message},
    )
    return {
        "ok": result.ok,
        "provider": result.provider,
        "error_code": result.error_code,
        "error_message": error_message,
    }


def _provider_test_error_message(
    provider: ProviderConfig, config: dict, result: ProviderResult
) -> str | None:
    """Turn Apollo test failures into configuration actions the operator can take."""
    if result.ok or str(config.get("adapter") or "").lower() != "apollo":
        return result.error_message

    error_code = str(result.error_code or "")
    detail = str(result.error_message or "").strip()
    if error_code == "missing_api_key":
        return "Apollo 连接测试失败：未配置 API Key。People API Search 需要 Apollo Master API Key。"
    if error_code == "http_401":
        return "Apollo 认证失败（401）：请确认系统配置中的 API Key 有效，且未被撤销。"
    if error_code == "http_403":
        return "Apollo 拒绝访问（403）：People API Search 需要 Master API Key，并且当前套餐必须具备 API 访问权限。"
    if error_code == "http_429":
        return "Apollo 请求过于频繁（429）：请等待 Apollo 限流恢复后重试连接测试。"
    if error_code == "request_failed":
        return (
            "Apollo 连接在收到 HTTP 响应前中断，暂时无法验证该 Key 是否为 Master API Key。"
            "请检查服务器网络出口、代理/防火墙，以及 Apollo 账户的 API 访问状态。"
            + (f" 原始错误：{detail}" if detail else "")
        )
    return detail or "Apollo 连接测试失败：请检查 Master API Key、套餐 API 权限和请求配置。"


def provider_test_payload(provider: ProviderConfig, config: dict) -> dict:
    configured = config.get("test_payload")
    if isinstance(configured, dict):
        return configured
    if provider.type == "company_search":
        if str(config.get("adapter") or "").lower() == "hunter":
            domain_finder_company = str(config.get("test_domain_finder_company") or "").strip()
            if (
                domain_finder_company
                and str(config.get("domain_finder_endpoint_url") or "").strip()
            ):
                return {"operation": "domain_finder", "company_name": domain_finder_company}
            return {
                "mode": "brand_discovery",
                "brand_keywords": [str(config.get("test_query") or "handbag manufacturers")],
                "categories": ["Handbags"],
                "countries": ["Italy"],
                "brand_limit": 1,
            }
        return {
            "brand_keywords": [str(config.get("test_query") or "BuyerReach Demo")],
            "brand_limit": 1,
        }
    if provider.type == "contact_search":
        if (
            str(config.get("adapter") or "").lower() == "apollo"
            and config.get("test_bulk_enrichment")
            and str(config.get("bulk_enrichment_endpoint_url") or "").strip()
        ):
            return {
                "operation": "bulk_enrich",
                "company": {
                    "brand_name": "BuyerReach Demo",
                    "domain": str(config.get("test_domain") or "example.com"),
                },
                "contacts": [{"first_name": "Jane", "last_name": "Doe", "title": "Head of Buying"}],
            }
        return {
            "company": {
                "brand_name": "BuyerReach Demo",
                "domain": str(config.get("test_domain") or "example.com"),
            },
            "titles": ["Head of Buying"],
            "limit": 1,
        }
    if provider.type == "email_finder":
        return {
            "contact": {"first_name": "Jane", "last_name": "Doe"},
            "domain": str(config.get("test_domain") or "example.com"),
        }
    if provider.type == "brand_email_search":
        return {
            "domain_search": True,
            "domain": str(config.get("test_domain") or "example.com"),
            "limit": int(config.get("limit") or 5),
        }
    if provider.type == "email_verifier":
        return {"email": str(config.get("test_email") or "test@example.com")}
    return {"event": "provider.test", "payload": {"test": True}}


def enabled_providers(
    db: Session, provider_type: str, task: SearchTask | None = None
) -> list[ProviderConfig]:
    """Build transient providers from encrypted Vendor credentials and code adapters.

    Legacy ProviderConfig remains as a migration fallback only when the new credential
    table has no rows, which keeps pre-migration tests and rollback paths operational.
    """
    credentials = list(db.scalars(select(VendorCredential)).all())
    if not credentials:
        if task is not None and isinstance(task.configuration_snapshot, dict):
            frozen = task.configuration_snapshot.get("providers", [])
            snapshot_providers = [
                ProviderConfig(
                    provider=str(item.get("provider") or "snapshot-provider"),
                    type=str(item.get("type") or ""),
                    priority=int(item.get("priority") or 100),
                    enabled=True,
                    config=item.get("config") if isinstance(item.get("config"), dict) else {},
                )
                for item in frozen
                if isinstance(item, dict) and item.get("type") == provider_type
            ]
            if snapshot_providers:
                return sorted(snapshot_providers, key=lambda item: item.priority)
        return list(
            db.scalars(
                select(ProviderConfig)
                .where(ProviderConfig.type == provider_type, ProviderConfig.enabled.is_(True))
                .order_by(ProviderConfig.priority.asc(), ProviderConfig.created_at.asc())
            )
        )

    frozen_credential_ids: set[str] | None = None
    if (
        task is not None
        and isinstance(task.configuration_snapshot, dict)
        and "credential_refs" in task.configuration_snapshot
    ):
        refs = task.configuration_snapshot.get("credential_refs", [])
        frozen_credential_ids = {
            str(item.get("credential_id"))
            for item in refs
            if isinstance(item, dict) and item.get("credential_id")
        }
    credential_by_vendor = {
        item.vendor: item
        for item in credentials
        if item.encrypted_api_key
        and (
            str(item.id) in frozen_credential_ids
            if frozen_credential_ids is not None
            else item.enabled
        )
    }
    if task is not None:
        plan = ensure_task_vendor_plan(db, task)
        search_order = [plan.primary_vendor, *(plan.fallback_vendors or [])]
        verification_order = [plan.verification_vendor] if plan.verification_vendor else []
        local_mode = str(plan.local_verification_mode or "disabled")
    else:
        strategy = get_vendor_strategy(db)
        search_order = [strategy["primary_vendor"], *(strategy.get("fallback_vendors") or [])]
        verification_order = (
            [strategy.get("verification_vendor")] if strategy.get("verification_vendor") else []
        )
        local_mode = str(strategy.get("local_verification_mode") or "disabled")
    order = verification_order if provider_type == "email_verifier" else search_order

    # ── Pipeline mode: restrict to selected vendors only ──────────────────
    if (
        task is not None
        and plan.execution_mode
        not in {"legacy_waterfall", "waterfall", ""}
    ):
        selected = list(plan.selected_vendors or [])
        if selected:
            # The frozen user selection is the complete routing source for new
            # tasks; legacy primary/fallback/verification fields must not hide
            # a selected Vendor or reintroduce an unselected one.
            order = selected
    if provider_type == "email_verifier":
        if not (
            task is not None
            and plan.execution_mode not in {"legacy_waterfall", "waterfall", ""}
        ):
            order.extend(vendor for vendor in VERIFICATION_VENDORS if vendor not in order)
        if local_mode != "disabled":
            order = [
                "aftership_local",
                *(vendor for vendor in order if vendor != "aftership_local"),
            ]
        else:
            order = [vendor for vendor in order if vendor != "aftership_local"]
    else:
        if (
            task is not None
            and plan.execution_mode
            not in {"legacy_waterfall", "waterfall", ""}
            and plan.selected_vendors
        ):
            # Pipeline mode: DO NOT extend with all vendors
            pass
        else:
            order.extend(vendor for vendor in SEARCH_VENDORS if vendor not in order)

    providers: list[ProviderConfig] = []
    for priority, vendor in enumerate(order, start=1):
        credential = credential_by_vendor.get(str(vendor or ""))
        adapter = adapter_for(str(vendor or ""))
        if credential is None or adapter is None:
            continue
        provider = adapter.provider(
            provider_type, decrypt_secret(credential.encrypted_api_key), priority=priority * 10
        )
        if provider is not None:
            providers.append(provider)
    return providers


def _provider_source_type(provider: ProviderConfig) -> SourceType:
    config = decrypt_provider_config(provider.config or {})
    try:
        return SourceType(str(config.get("source_type") or SourceType.commercial_api))
    except ValueError:
        return SourceType.commercial_api


def enabled_provider(db: Session, provider_type: str) -> ProviderConfig | None:
    providers = enabled_providers(db, provider_type)
    return providers[0] if providers else None


def provider_quota_available(
    provider: ProviderConfig,
    config: dict,
    payload: dict | None = None,
) -> ProviderResult | None:
    """Vendor quota is authoritative; local call counts are metrics only."""
    if (
        str(config.get("adapter") or "").lower() == "hunter"
        and provider.type == "company_search"
        and str((payload or {}).get("mode") or "") == "brand_discovery"
        and str((payload or {}).get("operation") or "") != "domain_finder"
    ):
        # Hunter Discover is a free company-search call. A depleted search-credit
        # balance must not prevent the request; endpoint access and rate limits are
        # still enforced by the actual Discover response (for example 403/429).
        return None
    return check_vendor_provider_quota(provider, config)


def _checkpoint_scope(payload: dict) -> str:
    if str(payload.get("mode") or "") == "brand_discovery":
        payload = {
            "mode": "brand_discovery",
            "countries": sorted(
                str(item).strip().casefold()
                for item in payload.get("countries", [])
                if str(item).strip()
            ),
            "categories": sorted(
                str(item).strip().casefold()
                for item in payload.get("categories", [])
                if str(item).strip()
            ),
            "category_match_mode": str(payload.get("category_match_mode") or "any").casefold(),
            "require_website": bool(payload.get("require_website", True)),
        }
    serialized = json.dumps(
        jsonable_encoder(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _discovery_page_offset(
    db: Session,
    task: SearchTask,
    checkpoint: TaskStageCheckpoint,
    page_size: int,
) -> int:
    previous_pages = (
        db.scalar(
            select(func.count(TaskStageCheckpoint.id))
            .join(SearchTask, SearchTask.id == TaskStageCheckpoint.task_id)
            .where(
                TaskStageCheckpoint.stage == checkpoint.stage,
                TaskStageCheckpoint.scope_key == checkpoint.scope_key,
                TaskStageCheckpoint.vendor == checkpoint.vendor,
                TaskStageCheckpoint.task_id != task.id,
                SearchTask.organization_id == task.organization_id,
            )
        )
        or 0
    )
    return int(previous_pages) * page_size


def _discovery_page_number(
    db: Session,
    task: SearchTask,
    checkpoint: TaskStageCheckpoint,
) -> int:
    return (_discovery_page_offset(db, task, checkpoint, 1)) + 1


def _exclude_previous_discovery_results(
    db: Session,
    task: SearchTask,
    checkpoint: TaskStageCheckpoint,
    items: list[dict],
) -> list[dict]:
    previous_task_ids = (
        select(TaskStageCheckpoint.task_id)
        .join(SearchTask, SearchTask.id == TaskStageCheckpoint.task_id)
        .where(
            TaskStageCheckpoint.stage == checkpoint.stage,
            TaskStageCheckpoint.scope_key == checkpoint.scope_key,
            TaskStageCheckpoint.vendor == checkpoint.vendor,
            TaskStageCheckpoint.task_id != task.id,
            SearchTask.organization_id == task.organization_id,
        )
    )
    previous = db.execute(
        select(DiscoveryCandidate.normalized_domain, DiscoveryCandidate.normalized_name)
        .join(DiscoveryCandidateHit, DiscoveryCandidateHit.candidate_id == DiscoveryCandidate.id)
        .where(DiscoveryCandidateHit.task_id.in_(previous_task_ids))
    ).all()
    domains = {str(domain) for domain, _ in previous if domain}
    names = {str(name) for _, name in previous if name}
    unseen: list[dict] = []
    for item in items:
        domain = domain_from_url(item.get("domain") or item.get("website") or item.get("url"))
        name = slugify(
            str(item.get("brand_name") or item.get("name") or item.get("legal_name") or "")
        )
        if domain:
            if domain in domains:
                continue
        elif name and name in names:
            continue
        unseen.append(item)
    return unseen


def _stage_checkpoint(
    db: Session,
    task: SearchTask | None,
    stage: str,
    scope_key: str,
    provider: ProviderConfig,
) -> TaskStageCheckpoint | None:
    if task is None:
        return None
    checkpoint = db.scalar(
        select(TaskStageCheckpoint).where(
            TaskStageCheckpoint.task_id == task.id,
            TaskStageCheckpoint.stage == stage,
            TaskStageCheckpoint.scope_key == scope_key,
            TaskStageCheckpoint.vendor
            == str((provider.config or {}).get("adapter") or provider.provider).lower(),
        )
    )
    if checkpoint is None:
        checkpoint = TaskStageCheckpoint(
            task_id=task.id,
            stage=stage,
            scope_key=scope_key,
            vendor=str((provider.config or {}).get("adapter") or provider.provider).lower(),
        )
        db.add(checkpoint)
        db.flush()
    return checkpoint


def _start_checkpoint(checkpoint: TaskStageCheckpoint | None) -> None:
    if checkpoint is None:
        return
    checkpoint.status = "running"
    checkpoint.attempts = int(checkpoint.attempts or 0) + 1
    checkpoint.error_code = None
    checkpoint.error_message = None
    checkpoint.started_at = utc_now()
    checkpoint.completed_at = None


def _fail_checkpoint(
    checkpoint: TaskStageCheckpoint | None, error_code: str | None, message: str
) -> None:
    if checkpoint is None:
        return
    checkpoint.status = "failed"
    checkpoint.error_code = error_code
    checkpoint.error_message = message[:2000]
    checkpoint.completed_at = utc_now()


def _complete_checkpoint(
    checkpoint: TaskStageCheckpoint | None, items_path: str, items: list[dict]
) -> None:
    if checkpoint is None:
        return
    checkpoint.status = "completed"
    checkpoint.error_code = None
    checkpoint.error_message = None
    checkpoint.normalized_output = {items_path: jsonable_encoder(items)}
    checkpoint.completed_at = utc_now()


def execute_provider_waterfall(
    db: Session,
    provider_type: str,
    payload: dict,
    items_path: str,
    *,
    task: SearchTask | None = None,
    entity_type: str | None = None,
    item_filter: Callable[[list[dict]], list[dict]] | None = None,
    allowed_vendors: set[str] | None = None,
) -> tuple[ProviderConfig | None, list[dict], list[str]]:
    """Run providers until one returns mapped items that pass business validation."""
    providers = enabled_providers(db, provider_type, task)
    if allowed_vendors is not None:
        providers = [
            provider
            for provider in providers
            if str(decrypt_provider_config(provider.config or {}).get("adapter") or "").casefold()
            in allowed_vendors
        ]
    if not providers:
        return None, [], [f"No enabled {provider_type} Provider is configured"]

    errors: list[str] = []
    scope_key = _checkpoint_scope(payload)
    for provider in providers:
        config = decrypt_provider_config(provider.config or {})
        supported_modes = config.get("supported_modes")
        mode = str(payload.get("mode") or "").strip()
        if isinstance(supported_modes, list) and mode and mode not in supported_modes:
            continue
        checkpoint = _stage_checkpoint(db, task, provider_type, scope_key, provider)
        if checkpoint is not None and checkpoint.status == "completed":
            cached = (
                checkpoint.normalized_output.get(items_path)
                if isinstance(checkpoint.normalized_output, dict)
                else None
            )
            if isinstance(cached, list):
                return provider, cached, errors
        _start_checkpoint(checkpoint)
        quota_check = provider_quota_available(provider, config, payload)
        if quota_check is not None and not quota_check.ok:
            message = f"{provider.provider}: {quota_check.error_message or 'quota check failed'}"
            errors.append(message)
            _fail_checkpoint(checkpoint, quota_check.error_code, message)
            _record_provider_fallback(db, task, entity_type or provider_type, provider, message)
            continue
        provider_payload = dict(payload)
        if (
            task is not None
            and checkpoint is not None
            and provider_type == "company_search"
            and str(payload.get("mode") or "") == "brand_discovery"
            and str(config.get("adapter") or "").casefold() in {"hunter", "apollo"}
        ):
            adapter = str(config.get("adapter") or "").casefold()
            page_size = min(max(int(config.get("discovery_page_size") or 100), 1), 100)
            provider_payload["discovery_limit"] = page_size
            if adapter == "hunter":
                # Hunter only permits offsets beyond the first 100 records on
                # eligible paid plans. Keep the safe default on page one unless
                # the operator explicitly confirms premium pagination support.
                provider_payload["discovery_offset"] = (
                    _discovery_page_offset(db, task, checkpoint, page_size)
                    if config.get("discovery_pagination_enabled") is True
                    else 0
                )
            else:
                provider_payload["discovery_page"] = _discovery_page_number(db, task, checkpoint)
        payload_variants = _provider_payload_variants(provider_type, provider_payload)
        max_combinations = min(max(int(config.get("max_search_combinations") or 100), 1), 500)
        if len(payload_variants) > max_combinations:
            message = (
                f"{provider.provider}: {len(payload_variants)} country/category combinations exceed "
                f"the configured limit of {max_combinations}"
            )
            errors.append(message)
            _fail_checkpoint(checkpoint, "too_many_combinations", message)
            _record_provider_fallback(db, task, entity_type or provider_type, provider, message)
            continue
        batches: list[list[dict]] = []
        raw_item_count = 0
        had_successful_response = False
        last_error_code: str | None = None
        for variant in payload_variants:
            combination = _provider_combination_label(variant)
            result = execute_provider(provider, variant)
            record_usage(db, provider, result.cost)
            if not result.ok:
                message = (
                    f"{provider.provider} ({combination}): {result.error_message or 'request failed'}"
                    if len(payload_variants) > 1
                    else f"{provider.provider}: {result.error_message or 'request failed'}"
                )
                errors.append(message)
                last_error_code = result.error_code
                _record_provider_fallback(db, task, entity_type or provider_type, provider, message)
                if result.error_code in {"http_429", "http_403"}:
                    break
                continue
            had_successful_response = True
            batch = extract_items(provider, result.data, items_path)
            raw_item_count += len(batch)
            if batch:
                batches.append(batch)
        items = _dedupe_provider_items(_round_robin_batches(batches))
        if not had_successful_response:
            _fail_checkpoint(
                checkpoint,
                last_error_code or "request_failed",
                errors[-1] if errors else "Provider request failed",
            )
            continue
        if items and item_filter is not None:
            items = item_filter(items)
        if (
            items
            and task is not None
            and provider_type == "company_search"
            and str(payload.get("mode") or "") == "brand_discovery"
            and str(config.get("adapter") or "").casefold() == "hunter"
            and not _new_discovery_provider_items(db, items)
        ):
            message = (
                f"{provider.provider}: returned companies, but none are new; "
                "continuing to the next company-search Provider"
            )
            _fail_checkpoint(checkpoint, "no_new_candidates", message)
            _record_provider_fallback(db, task, entity_type or provider_type, provider, message)
            continue
        business_item_count = len(items)
        if (
            items
            and task is not None
            and checkpoint is not None
            and provider_type == "company_search"
            and str(payload.get("mode") or "") == "brand_discovery"
            and (
                str(config.get("adapter") or "").casefold() == "apollo"
                or (
                    str(config.get("adapter") or "").casefold() == "hunter"
                    and config.get("discovery_pagination_enabled") is True
                )
            )
        ):
            items = _exclude_previous_discovery_results(db, task, checkpoint, items)
        if items:
            _complete_checkpoint(checkpoint, items_path, items)
            return provider, items, errors
        exhausted = business_item_count > 0 and not items
        message = (
            f"{provider.provider}: no new companies remain on this search page; the previous results were excluded"
            if exhausted
            else f"{provider.provider}: {raw_item_count} mapped {items_path} failed business filters"
            if raw_item_count and item_filter is not None
            else f"{provider.provider}: no mapped {items_path} returned"
        )
        errors.append(message)
        _fail_checkpoint(
            checkpoint,
            last_error_code or ("search_exhausted" if exhausted else "no_results"),
            message,
        )
        _record_provider_fallback(db, task, entity_type or provider_type, provider, message)
    return None, [], errors


def _new_discovery_provider_items(db: Session, items: list[dict]) -> list[dict]:
    """Return Provider companies that can create a new discovery candidate."""
    new_items: list[dict] = []
    for item in items:
        name = str(item.get("brand_name") or item.get("name") or "").strip()
        if not name:
            continue
        domain = domain_from_url(item.get("domain") or item.get("website") or item.get("url"))
        normalized_name = slugify(name)
        country = str(item.get("country") or "").strip() or None
        dedupe_key = (
            f"domain:{domain}"
            if domain
            else f"name:{normalized_name}|country:{str(country or '').casefold()}"
        )
        if domain and db.scalar(
            select(Blacklist.id).where(
                Blacklist.type == "domain",
                func.lower(Blacklist.value) == domain,
            )
        ):
            continue
        if _candidate_matches_customer(db, normalized_name, domain, country):
            continue
        if db.scalar(
            select(DiscoveryCandidate.id).where(DiscoveryCandidate.dedupe_key == dedupe_key)
        ):
            continue
        new_items.append(item)
    return new_items


def _provider_payload_variants(provider_type: str, payload: dict) -> list[dict]:
    if provider_type != "company_search" or str(payload.get("mode") or "") != "brand_discovery":
        return [payload]
    countries = [str(value).strip() for value in payload.get("countries", []) if str(value).strip()]
    categories = [
        str(value).strip() for value in payload.get("categories", []) if str(value).strip()
    ]
    if not countries or not categories:
        return [payload]
    # Compound targets such as "fast fashion + luggage" must be sent to the
    # Provider as one query. Splitting them silently changes AND into OR.
    category_groups = (
        [categories]
        if str(payload.get("category_match_mode") or "any") == "all"
        else [[category] for category in categories]
    )
    return [
        {
            **{key: value for key, value in payload.items() if key != "company_types"},
            "countries": [country],
            "categories": category_group,
        }
        for country in countries
        for category_group in category_groups
    ]


def _provider_combination_label(payload: dict) -> str:
    countries = (
        "/".join(str(value) for value in payload.get("countries", []) if str(value).strip())
        or "不限国家"
    )
    categories = (
        "/".join(str(value) for value in payload.get("categories", []) if str(value).strip())
        or "不限品类"
    )
    return f"{countries} × {categories}"


def _round_robin_batches(batches: list[list[dict]]) -> list[dict]:
    if not batches:
        return []
    return [
        batch[index]
        for index in range(max(len(batch) for batch in batches))
        for batch in batches
        if index < len(batch)
    ]


def _dedupe_provider_items(items: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    positions: dict[str, int] = {}
    for item in items:
        domain = domain_from_url(item.get("domain") or item.get("website") or item.get("url"))
        name = (
            str(item.get("brand_name") or item.get("name") or item.get("legal_name") or "")
            .strip()
            .casefold()
        )
        key = f"domain:{domain}" if domain else f"name:{name}"
        if key == "name:" or key not in positions:
            positions[key] = len(deduped)
            deduped.append(item)
            continue
        existing = deduped[positions[key]]
        actual_categories = [
            value for value in (existing.get("category"), item.get("category")) if value
        ]
        if actual_categories:
            existing["category"] = ", ".join(dict.fromkeys(actual_categories))
    return deduped


def execute_email_verifier_waterfall(
    db: Session, address: str, task: SearchTask | None = None
) -> tuple[ProviderConfig | None, dict, list[str]]:
    """Run verifier providers until one gives a conclusive result."""
    errors: list[str] = []
    conclusive_statuses = {
        EmailStatus.valid,
        EmailStatus.risky,
        EmailStatus.invalid,
        EmailStatus.disposable,
        EmailStatus.do_not_contact,
    }
    scope_key = _checkpoint_scope({"email": address})
    if task is not None:
        policy = ensure_task_vendor_plan(db, task)
        local_mode = str(policy.local_verification_mode or "disabled")
        rollout = int(policy.local_verification_rollout or 0)
        sample = int(policy.local_verification_sample or 10)
    else:
        policy_data = get_vendor_strategy(db)
        local_mode = str(policy_data.get("local_verification_mode") or "disabled")
        rollout = int(policy_data.get("local_verification_rollout") or 0)
        sample = int(policy_data.get("local_verification_sample") or 10)
    local_selected = (
        int(hashlib.sha256(address.lower().encode()).hexdigest()[:8], 16) % 100 < rollout
    )
    sample_selected = (
        int(hashlib.sha256(f"sample:{address.lower()}".encode()).hexdigest()[:8], 16) % 100 < sample
    )
    shadow_result: dict | None = None
    shadow_provider: ProviderConfig | None = None
    for provider in enabled_providers(db, "email_verifier", task):
        config = decrypt_provider_config(provider.config or {})
        is_local = str(config.get("adapter") or "").lower() == "aftership_local"
        if is_local and (local_mode == "disabled" or not local_selected):
            continue
        checkpoint = _stage_checkpoint(db, task, "email_verifier", scope_key, provider)
        if checkpoint is not None and checkpoint.status == "completed":
            cached = (
                checkpoint.normalized_output.get("verification")
                if isinstance(checkpoint.normalized_output, dict)
                else None
            )
            if isinstance(cached, dict):
                if is_local and (
                    local_mode == "shadow"
                    or cached.get("result") == EmailStatus.risky
                    or sample_selected
                ):
                    shadow_result = cached
                    shadow_provider = provider
                    continue
                return provider, cached, errors
        _start_checkpoint(checkpoint)
        quota_check = provider_quota_available(provider, config)
        if quota_check is not None and not quota_check.ok:
            message = f"{provider.provider}: {quota_check.error_message or 'quota check failed'}"
            errors.append(message)
            _fail_checkpoint(checkpoint, quota_check.error_code, message)
            continue
        result = execute_provider(provider, {"email": address})
        record_usage(db, provider, result.cost)
        if not result.ok:
            message = f"{provider.provider}: {result.error_message or 'request failed'}"
            errors.append(message)
            _fail_checkpoint(checkpoint, result.error_code, message)
            continue
        result_data = {
            "result": _nested(result.data, str(config.get("result_path") or "result"))
            or EmailStatus.unknown,
            "score": int(_nested(result.data, str(config.get("score_path") or "score")) or 0),
            "raw": result.raw,
        }
        for field in (
            "is_catch_all",
            "is_disposable",
            "domain_deliverable",
            "mailbox_exists",
            "smtp_check",
        ):
            if field in result.data:
                result_data[field] = result.data[field]
        if is_local:
            result_data["local_mode"] = local_mode
            result_data["sampled"] = sample_selected
            if checkpoint is not None:
                checkpoint.status = "completed"
                checkpoint.error_code = None
                checkpoint.error_message = None
                checkpoint.normalized_output = {"verification": jsonable_encoder(result_data)}
                checkpoint.completed_at = utc_now()
            if (
                local_mode == "shadow"
                or str(result_data["result"]) == EmailStatus.risky
                or (str(result_data["result"]) == EmailStatus.valid and sample_selected)
            ):
                shadow_result = result_data
                shadow_provider = provider
                continue
        if str(result_data["result"]) in conclusive_statuses:
            if checkpoint is not None:
                checkpoint.status = "completed"
                checkpoint.error_code = None
                checkpoint.error_message = None
                checkpoint.normalized_output = {"verification": jsonable_encoder(result_data)}
                checkpoint.completed_at = utc_now()
            if shadow_result is not None:
                result_data["local_comparison"] = shadow_result
            return provider, result_data, errors
        message = f"{provider.provider}: returned an inconclusive result"
        errors.append(message)
        _fail_checkpoint(checkpoint, "inconclusive_result", message)
    if shadow_result is not None and shadow_provider is not None and local_mode == "active":
        return shadow_provider, shadow_result, errors
    return None, {}, errors


def _record_provider_fallback(
    db: Session,
    task: SearchTask | None,
    entity_type: str,
    provider: ProviderConfig,
    error: str,
) -> None:
    if task is None:
        return
    db.add(
        TaskItem(
            task_id=task.id,
            entity_type=entity_type,
            stage=f"{entity_type}_provider_fallback",
            status=TaskStatus.partial,
            attempts=1,
            provider=provider.provider,
            error_message=error,
        )
    )


def record_usage(db: Session, provider: ProviderConfig, cost: float = 0) -> None:
    today = datetime.now(UTC).date().isoformat()
    usage = db.scalar(
        select(ApiUsage).where(ApiUsage.provider == provider.provider, ApiUsage.date == today)
    )
    if usage is None:
        usage = ApiUsage(provider=provider.provider, date=today, calls=0, cost=0)
        db.add(usage)
    usage.calls += 1
    usage.cost += cost


def create_blacklist(db: Session, payload: BlacklistCreate) -> Blacklist:
    value = payload.value.strip().lower()
    existing = db.scalar(select(Blacklist).where(Blacklist.value == value))
    if existing:
        return existing
    item = Blacklist(type=payload.type, value=value, reason=payload.reason)
    db.add(item)
    db.flush()
    audit(db, "blacklist.create", "blacklist", str(item.id), after=payload.model_dump(mode="json"))
    return item


def get_system_settings(db: Session) -> dict:
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "system"))
    stored = setting.value if setting else {}
    ai_settings = _stored_ai_settings(stored)
    if ai_settings.get("api_key"):
        ai_settings["api_key"] = "********"
    return {
        **DEFAULT_SYSTEM_SETTINGS,
        **stored,
        "title_dictionary": {
            **DEFAULT_SYSTEM_SETTINGS["title_dictionary"],
            **stored.get("title_dictionary", {}),
        },
        "email_rules": {**DEFAULT_SYSTEM_SETTINGS["email_rules"], **stored.get("email_rules", {})},
        "task_rules": {**DEFAULT_SYSTEM_SETTINGS["task_rules"], **stored.get("task_rules", {})},
        "ai": {**DEFAULT_SYSTEM_SETTINGS["ai"], **ai_settings},
    }


def get_task_defaults(db: Session) -> dict:
    """Expose only safe task-creation defaults to task users."""
    settings = get_system_settings(db)
    return {
        "target_titles": list(settings["title_dictionary"].get("p1") or []),
        "contacts_limit_per_brand": settings["task_rules"].get("default_contact_limit", 5),
    }


def _stored_ai_settings(stored: dict) -> dict:
    return decrypt_provider_config({"ai": stored.get("ai", {})}).get("ai", {})


def get_ai_settings(db: Session, task: SearchTask | None = None) -> dict:
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "system"))
    stored = setting.value if setting else {}
    current = {**DEFAULT_SYSTEM_SETTINGS["ai"], **_stored_ai_settings(stored)}
    if task is None or not isinstance(task.configuration_snapshot, dict):
        return current
    settings_snapshot = task.configuration_snapshot.get("settings", {})
    system_snapshot = (
        settings_snapshot.get("system", {}) if isinstance(settings_snapshot, dict) else {}
    )
    ai_snapshot = system_snapshot.get("ai", {}) if isinstance(system_snapshot, dict) else {}
    if not isinstance(ai_snapshot, dict):
        return current
    # Credentials are resolved at execution through their governed reference;
    # all behavioral settings stay frozen to task creation time.
    return {**current, **ai_snapshot, "api_key": current.get("api_key", "")}


def update_system_settings(db: Session, payload: SystemSettingsUpdate) -> dict:
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "system"))
    if setting is None:
        setting = SystemSetting(key="system", value={})
        db.add(setting)
    before = get_system_settings(db)
    previous_ai = _stored_ai_settings(setting.value or {})
    ai_settings = payload.ai.model_dump()
    if not ai_settings["api_key"] or ai_settings["api_key"].startswith("********"):
        ai_settings["api_key"] = previous_ai.get("api_key", "")
    setting.value = {
        "title_dictionary": payload.title_dictionary,
        "email_rules": payload.email_rules,
        "task_rules": payload.task_rules,
        "ai": encrypt_provider_config({"ai": ai_settings})["ai"],
    }
    db.flush()
    after = get_system_settings(db)
    audit(
        db, "system_settings.update", "system_setting", str(setting.id), before=before, after=after
    )
    return after


def plan_ai_task(db: Session, payload: AITaskPlanRequest) -> dict:
    from app.modules.ai_coordinator import generate_task_plan

    providers: list[dict] = []
    for vendor in ("apollo", "hunter", "prospeo"):
        credential = db.scalar(select(VendorCredential).where(VendorCredential.vendor == vendor))
        if credential is None or not credential.enabled or not credential.encrypted_api_key:
            continue
        adapter = adapter_for(vendor)
        if adapter is None:
            continue
        providers.append(
            {"name": adapter.display_name, "type": ", ".join(sorted(adapter.supported_types))}
        )
    result = generate_task_plan(payload.prompt, get_ai_settings(db), providers)
    task = result["task"]
    # The compatibility adapter is deliberately vocabulary-free. A validated AI
    # intent may replace it; unavailable AI remains explicit and reviewable.
    intent = (
        SearchIntent.model_validate(result["search_intent"])
        if result.get("search_intent")
        else intent_from_legacy(
            payload.prompt,
            task.get("categories", []),
            task.get("countries", []),
            task.get("company_types", []),
            task.get("category_match_mode", "any"),
        )
    )
    result["search_intent"] = intent.model_dump(mode="json")
    result["requires_confirmation"] = any(
        bool(item.get("requires_confirmation")) for item in intent.ambiguities
    )
    audit(
        db,
        "ai.task_plan_generated",
        "ai",
        "task_plan",
        after={
            "source": result["source"],
            "task": result["task"],
            "ai_attempted": result.get("ai_attempted", False),
            "fallback_reason": result.get("fallback_reason"),
        },
    )
    return result


def list_roles(db: Session, page: int, page_size: int, *, actor: User) -> dict:
    from app.core.security import can_assign_role, get_user_permissions

    statement = select(Role)
    if "admin:*" not in get_user_permissions(db, actor):
        statement = statement.where(Role.organization_id == actor.organization_id)
    roles = list(db.scalars(statement.order_by(Role.created_at.desc())).all())
    roles = [role for role in roles if can_assign_role(db, actor, role.id)]
    total = len(roles)
    items = [to_dict(role) for role in roles[(page - 1) * page_size:page * page_size]]
    return page_result(total, page, page_size, items)


def create_role(db: Session, payload: RoleCreate, *, organization_id=None) -> Role:
    normalized_name = payload.name.strip().lower()
    existing = db.scalar(
        select(Role).where(Role.name == normalized_name, Role.organization_id == organization_id)
    )
    if existing:
        raise ValueError("Role already exists")
    role = Role(
        name=normalized_name,
        permissions=payload.permissions,
        data_scopes=payload.data_scopes,
        organization_id=organization_id,
    )
    db.add(role)
    db.flush()
    audit(
        db,
        "role.create",
        "role",
        str(role.id),
        after={"name": role.name, "permissions": role.permissions},
    )
    return role


def update_role(db: Session, role: Role, payload: RoleUpdate) -> Role:
    before = {"permissions": role.permissions, "data_scopes": role.data_scopes, "status": role.status}
    role.permissions = payload.permissions
    role.data_scopes = payload.data_scopes
    if payload.status is not None:
        role.status = payload.status
    role.permission_version = 1
    audit(
        db,
        "role.update",
        "role",
        str(role.id),
        before=before,
        after={
            "permissions": role.permissions,
            "data_scopes": role.data_scopes,
            "status": role.status,
        },
    )
    return role


def list_users(db: Session, page: int, page_size: int, *, actor: User) -> dict:
    from app.core.security import can_manage_user

    statement = (
        select(User, Role.name)
        .outerjoin(Role, User.role_id == Role.id)
        .where(User.deleted_at.is_(None))
    )
    rows = db.execute(statement.order_by(User.created_at.desc())).all()
    rows = [(item, role_name) for item, role_name in rows if can_manage_user(db, actor, item)]
    total = len(rows)
    rows = rows[(page - 1) * page_size:page * page_size]
    items = []
    for user, role_name in rows:
        item = to_dict(user)
        item["role_name"] = role_name
        item.pop("password_hash", None)
        items.append(item)
    return page_result(total, page, page_size, items)


def create_user(
    db: Session,
    payload: UserCreate,
    *,
    organization_id: UUID | None = None,
) -> User:
    from app.core.security import hash_password

    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError("User email already exists")
    user = User(
        email=email,
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        role_id=payload.role_id,
        department_id=payload.department_id,
        organization_unit_id=payload.organization_unit_id,
        organization_id=organization_id,
        status=payload.status,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        "user.create",
        "user",
        str(user.id),
        after={"email": user.email, "role_id": user.role_id, "status": user.status},
    )
    return user


def update_user(db: Session, user: User, payload: UserUpdate) -> User:
    from app.core.security import hash_password

    before = {
        "name": user.name,
        "role_id": user.role_id,
        "department_id": user.department_id,
        "status": user.status,
    }
    changes = payload.model_dump(exclude_unset=True)
    password = changes.pop("password", None)
    for key, value in changes.items():
        setattr(user, key, value)
    if password:
        user.password_hash = hash_password(password)
    audit(
        db,
        "user.update",
        "user",
        str(user.id),
        before=before,
        after={key: getattr(user, key) for key in before},
    )
    return user


def create_tag(db: Session, payload: TagCreate) -> Tag:
    name = payload.name.strip()
    existing = db.scalar(select(Tag).where(Tag.name == name, Tag.module == payload.module))
    if existing:
        raise ValueError("Tag already exists")
    tag = Tag(name=name, module=payload.module)
    db.add(tag)
    db.flush()
    audit(db, "tag.create", "tag", str(tag.id), after=to_dict(tag))
    return tag


def list_tags(db: Session, module: str | None, page: int, page_size: int) -> dict:
    usage_count = (
        select(func.count(EntityTag.id))
        .where(EntityTag.tag_id == Tag.id)
        .correlate(Tag)
        .scalar_subquery()
    )
    statement = select(Tag, usage_count.label("usage_count"))
    count_statement = select(func.count()).select_from(Tag)
    if module:
        statement = statement.where(Tag.module == module)
        count_statement = count_statement.where(Tag.module == module)
    total = db.scalar(count_statement) or 0
    rows = db.execute(
        statement.order_by(Tag.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = []
    for tag, count in rows:
        item = to_dict(tag)
        item["usage_count"] = count or 0
        items.append(item)
    return page_result(total, page, page_size, items)


def update_tag(db: Session, tag: Tag, payload: TagUpdate) -> Tag:
    name = payload.name.strip()
    duplicate = db.scalar(
        select(Tag).where(Tag.module == tag.module, Tag.name == name, Tag.id != tag.id)
    )
    if duplicate:
        raise ValueError("Tag already exists")
    before = to_dict(tag)
    tag.name = name
    db.flush()
    audit(db, "tag.update", "tag", str(tag.id), before=before, after=to_dict(tag))
    return tag


def delete_tag(db: Session, tag: Tag) -> None:
    before = to_dict(tag)
    db.execute(sa_delete(EntityTag).where(EntityTag.tag_id == tag.id))
    db.delete(tag)
    audit(db, "tag.delete", "tag", str(tag.id), before=before)


def create_custom_field(db: Session, payload: CustomFieldCreate) -> CustomField:
    name = payload.name.strip()
    existing = db.scalar(
        select(CustomField).where(CustomField.name == name, CustomField.module == payload.module)
    )
    if existing:
        raise ValueError("Custom field already exists")
    field = CustomField(**payload.model_dump(exclude={"name"}), name=name)
    db.add(field)
    db.flush()
    audit(db, "custom_field.create", "custom_field", str(field.id), after=to_dict(field))
    return field


def list_custom_fields(db: Session, module: str | None, page: int, page_size: int) -> dict:
    usage_count = (
        select(func.count(CustomValue.id))
        .where(CustomValue.field_id == CustomField.id)
        .correlate(CustomField)
        .scalar_subquery()
    )
    statement = select(CustomField, usage_count.label("usage_count"))
    count_statement = select(func.count()).select_from(CustomField)
    if module:
        statement = statement.where(CustomField.module == module)
        count_statement = count_statement.where(CustomField.module == module)
    total = db.scalar(count_statement) or 0
    rows = db.execute(
        statement.order_by(CustomField.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for field, count in rows:
        item = to_dict(field)
        item["usage_count"] = count or 0
        items.append(item)
    return page_result(total, page, page_size, items)


def update_custom_field(
    db: Session,
    field: CustomField,
    payload: CustomFieldUpdate,
) -> CustomField:
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        duplicate = db.scalar(
            select(CustomField).where(
                CustomField.module == field.module,
                CustomField.name == changes["name"],
                CustomField.id != field.id,
            )
        )
        if duplicate:
            raise ValueError("Custom field already exists")
    if changes.get("type") and changes["type"] != field.type:
        value_count = (
            db.scalar(
                select(func.count())
                .select_from(CustomValue)
                .where(CustomValue.field_id == field.id)
            )
            or 0
        )
        if value_count:
            raise ValueError("Custom field type cannot be changed while values exist")
    before = to_dict(field)
    for key, value in changes.items():
        setattr(field, key, value)
    db.flush()
    audit(
        db,
        "custom_field.update",
        "custom_field",
        str(field.id),
        before=before,
        after=to_dict(field),
    )
    return field


def delete_custom_field(db: Session, field: CustomField) -> None:
    before = to_dict(field)
    db.execute(sa_delete(CustomValue).where(CustomValue.field_id == field.id))
    db.delete(field)
    audit(db, "custom_field.delete", "custom_field", str(field.id), before=before)


ENTITY_METADATA_MODELS = {
    "brands": Brand,
    "contacts": Contact,
    "emails": EmailAddress,
}


def metadata_entity(db: Session, entity_type: str, entity_id: UUID):
    model = ENTITY_METADATA_MODELS.get(entity_type)
    if model is None:
        raise ValueError("Unsupported entity type")
    entity = db.get(model, entity_id)
    if entity is None or getattr(entity, "deleted_at", None) is not None:
        raise LookupError("Entity not found")
    return entity


def list_entity_tags(db: Session, entity_type: str, entity_id: UUID) -> dict:
    metadata_entity(db, entity_type, entity_id)
    assignments = db.execute(
        select(EntityTag, Tag)
        .join(Tag, EntityTag.tag_id == Tag.id)
        .where(
            EntityTag.entity_type == entity_type,
            EntityTag.entity_id == str(entity_id),
        )
        .order_by(Tag.name)
    ).all()
    items = []
    for assignment, tag in assignments:
        item = to_dict(tag)
        item["assignment_id"] = str(assignment.id)
        item["assigned_at"] = assignment.created_at.isoformat()
        items.append(item)
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "total": len(items),
        "items": items,
    }


def assign_entity_tag(
    db: Session,
    entity_type: str,
    entity_id: UUID,
    tag: Tag,
) -> EntityTag:
    metadata_entity(db, entity_type, entity_id)
    if tag.module != entity_type:
        raise ValueError("Tag module does not match entity type")
    assignment = db.scalar(
        select(EntityTag).where(
            EntityTag.entity_type == entity_type,
            EntityTag.entity_id == str(entity_id),
            EntityTag.tag_id == tag.id,
        )
    )
    if assignment:
        return assignment
    assignment = EntityTag(
        entity_type=entity_type,
        entity_id=str(entity_id),
        tag_id=tag.id,
    )
    db.add(assignment)
    db.flush()
    audit(
        db,
        "entity_tag.assign",
        entity_type,
        str(entity_id),
        after={"tag_id": str(tag.id), "tag_name": tag.name},
    )
    return assignment


def remove_entity_tag(
    db: Session,
    entity_type: str,
    entity_id: UUID,
    tag: Tag,
) -> None:
    metadata_entity(db, entity_type, entity_id)
    assignment = db.scalar(
        select(EntityTag).where(
            EntityTag.entity_type == entity_type,
            EntityTag.entity_id == str(entity_id),
            EntityTag.tag_id == tag.id,
        )
    )
    if assignment is None:
        raise LookupError("Tag assignment not found")
    db.delete(assignment)
    audit(
        db,
        "entity_tag.remove",
        entity_type,
        str(entity_id),
        before={"tag_id": str(tag.id), "tag_name": tag.name},
    )


def normalize_custom_value(field: CustomField, value):
    if value is None:
        if field.is_required:
            raise ValueError("Custom field is required")
        return None

    if field.type in {"text", "single_select"}:
        if not isinstance(value, str):
            raise ValueError(f"{field.type} value must be a string")
        normalized = value.strip()
    elif field.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number value must be numeric")
        normalized = value
    elif field.type == "date":
        if isinstance(value, datetime):
            normalized = value.date().isoformat()
        elif isinstance(value, date):
            normalized = value.isoformat()
        elif isinstance(value, str):
            try:
                normalized = date.fromisoformat(value.strip()).isoformat()
            except ValueError as exc:
                raise ValueError("date value must use YYYY-MM-DD") from exc
        else:
            raise ValueError("date value must use YYYY-MM-DD")
    elif field.type == "multi_select":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("multi_select value must be a string array")
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
    elif field.type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("boolean value must be true or false")
        normalized = value
    elif field.type == "url":
        if not isinstance(value, str):
            raise ValueError("url value must be a string")
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url value must be a valid HTTP or HTTPS URL")
    elif field.type == "email":
        if not isinstance(value, str):
            raise ValueError("email value must be a string")
        try:
            normalized = validate_email(value.strip(), check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise ValueError("email value must be a valid email address") from exc
    elif field.type == "phone":
        if not isinstance(value, str):
            raise ValueError("phone value must be a string")
        normalized = value.strip()
        if (
            not re.fullmatch(r"[+0-9() .-]{6,30}", normalized)
            or len(re.sub(r"\D", "", normalized)) < 6
        ):
            raise ValueError("phone value must be a valid phone number")
    else:
        raise ValueError("Unsupported custom field type")

    if field.is_required and (
        normalized is None or normalized == "" or (isinstance(normalized, list) and not normalized)
    ):
        raise ValueError("Custom field is required")
    return normalized


def custom_value_item(field: CustomField, value: CustomValue | None) -> dict:
    item = to_dict(field)
    item["custom_value_id"] = str(value.id) if value else None
    item["has_value"] = value is not None
    item["value"] = value.value if value else None
    item["value_updated_at"] = value.updated_at.isoformat() if value else None
    return item


def list_entity_custom_values(db: Session, entity_type: str, entity_id: UUID) -> dict:
    metadata_entity(db, entity_type, entity_id)
    fields = list(
        db.scalars(
            select(CustomField)
            .where(CustomField.module == entity_type)
            .order_by(CustomField.created_at, CustomField.name)
        )
    )
    values = list(
        db.scalars(
            select(CustomValue).where(
                CustomValue.entity_type == entity_type,
                CustomValue.entity_id == str(entity_id),
            )
        )
    )
    values_by_field = {value.field_id: value for value in values}
    items = [custom_value_item(field, values_by_field.get(field.id)) for field in fields]
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "total": len(items),
        "items": items,
    }


def upsert_custom_value(
    db: Session,
    entity_type: str,
    entity_id: UUID,
    field: CustomField,
    raw_value,
) -> CustomValue:
    metadata_entity(db, entity_type, entity_id)
    if field.module != entity_type:
        raise ValueError("Custom field module does not match entity type")
    normalized = normalize_custom_value(field, raw_value)
    value = db.scalar(
        select(CustomValue).where(
            CustomValue.field_id == field.id,
            CustomValue.entity_type == entity_type,
            CustomValue.entity_id == str(entity_id),
        )
    )
    before = {"value": value.value} if value else None
    if value is None:
        value = CustomValue(
            field_id=field.id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            value=normalized,
        )
        db.add(value)
    else:
        value.value = normalized
    db.flush()
    audit(
        db,
        "custom_value.upsert",
        entity_type,
        str(entity_id),
        before=before,
        after={"field_id": str(field.id), "field_name": field.name, "value": normalized},
    )
    return value


def delete_custom_value(
    db: Session,
    entity_type: str,
    entity_id: UUID,
    field: CustomField,
) -> None:
    metadata_entity(db, entity_type, entity_id)
    if field.module != entity_type:
        raise ValueError("Custom field module does not match entity type")
    if field.is_required:
        raise ValueError("Required custom field value cannot be deleted")
    value = db.scalar(
        select(CustomValue).where(
            CustomValue.field_id == field.id,
            CustomValue.entity_type == entity_type,
            CustomValue.entity_id == str(entity_id),
        )
    )
    if value is None:
        raise LookupError("Custom field value not found")
    before = {"field_id": str(field.id), "field_name": field.name, "value": value.value}
    db.delete(value)
    audit(db, "custom_value.delete", entity_type, str(entity_id), before=before)


def _record_task_result(
    db: Session,
    task: SearchTask,
    entity_type: str,
    entity_id: UUID | str,
    stage: str,
    provider: str | None = None,
) -> None:
    """Associate a concrete task result with the task that produced it."""
    db.add(
        TaskItem(
            task_id=task.id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            stage=stage,
            status=TaskStatus.completed,
            attempts=1,
            provider=provider,
        )
    )


def _task_result_counts(db: Session, task_id: UUID | str) -> dict[str, int]:
    """Count distinct result entities recorded for one search task."""
    db.flush()
    counts: dict[str, int] = {}
    for progress_key, entity_type in (
        ("brands", "brand"),
        ("websites", "website"),
        ("contacts", "contact"),
        ("emails", "email"),
    ):
        counts[progress_key] = int(
            db.scalar(
                select(func.count(func.distinct(TaskItem.entity_id))).where(
                    TaskItem.task_id == task_id,
                    TaskItem.entity_type == entity_type,
                    TaskItem.entity_id.is_not(None),
                    TaskItem.status == TaskStatus.completed,
                )
            )
            or 0
        )
    return counts


def _task_progress(db: Session, task: SearchTask) -> dict:
    """Preserve mode-specific statistics while refreshing task result totals."""
    return {**(task.progress or {}), **_task_result_counts(db, task.id)}


def current_counts(db: Session) -> dict:
    return {
        "brands": db.scalar(
            select(func.count())
            .select_from(Brand)
            .where(
                Brand.deleted_at.is_(None),
                Brand.status.notin_({"pending_review", "migrated_candidate"}),
            )
        )
        or 0,
        "websites": db.scalar(
            select(func.count()).select_from(Website).where(Website.deleted_at.is_(None))
        )
        or 0,
        "contacts": db.scalar(
            select(func.count()).select_from(Contact).where(Contact.deleted_at.is_(None))
        )
        or 0,
        "emails": db.scalar(
            select(func.count()).select_from(EmailAddress).where(EmailAddress.deleted_at.is_(None))
        )
        or 0,
    }


def audit(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=_redact_sensitive(before) if before is not None else None,
            after=_redact_sensitive(after) if after is not None else None,
        )
    )


def emit(db: Session, event_name: str, payload: dict) -> None:
    encoded = _redact_sensitive(payload)
    aggregate_id = str(
        encoded.get("task_id")
        or encoded.get("candidate_id")
        or encoded.get("entity_id")
        or "system"
    )
    aggregate_type = (
        "search_task"
        if encoded.get("task_id")
        else "discovery_candidate"
        if encoded.get("candidate_id")
        else "system"
    )
    add_event(
        db,
        event_name,
        aggregate_type,
        aggregate_id,
        encoded,
        candidate_id=str(encoded["candidate_id"]) if encoded.get("candidate_id") else None,
    )


def _redact_sensitive(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict = {}
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(
                fragment in normalized
                for fragment in (
                    "api_key",
                    "authorization",
                    "password",
                    "secret",
                    "token",
                    "credential",
                )
            ):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return jsonable_encoder(value)


def to_dict(item: object) -> dict:
    data = {}
    for column in item.__table__.columns:
        value = getattr(item, column.name)
        data[column.name] = jsonable_encoder(value) if value is not None else None
    return data


def page_result(total: int, page: int, page_size: int, items: list[dict]) -> dict:
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    value = url if url.startswith(("http://", "https://")) else f"https://{url}"
    parsed = urlparse(value)
    domain = parsed.hostname or value.replace("https://", "").replace("http://", "").split("/")[0]
    return domain.removeprefix("www.").lower()


def _is_sensitive_config_key(key: object) -> bool:
    return is_sensitive_config_key(key)


def _masked_config(config: dict) -> dict:
    masked = {}
    for key, value in config.items():
        if _is_sensitive_config_key(key):
            masked[key] = "********" if value else ""
        else:
            masked[key] = value
    return masked


def _nested(value: object, path: str) -> object:
    current = value
    for part in path.split(".") if path else []:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _csv_value(value: object) -> object:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else value


def _notify(db: Session, event_name: str, payload: dict, *, strict: bool = False) -> None:
    """Dispatch a domain event to all enabled notification providers.

    Best-effort: failures are logged but never raised.  This runs synchronously
    inside the request/response cycle but timeouts are very short (5 s) so
    misbehaving webhooks will not block the API.
    """
    from app.core.config import settings
    from app.providers.feishu import FeishuNotificationProvider

    # Built-in Feishu webhook (from env var, for convenience)
    if settings.feishu_webhook_url:
        try:
            provider = FeishuNotificationProvider(settings.feishu_webhook_url)
            provider.send(event_name, [], payload)
        except Exception:
            if strict:
                raise

    for provider in db.scalars(
        select(ProviderConfig).where(
            ProviderConfig.type == "notification", ProviderConfig.enabled.is_(True)
        )
    ).all():
        try:
            result = execute_provider(provider, {"event": event_name, "payload": payload})
            record_usage(db, provider, result.cost)
            if strict and not result.ok:
                raise RuntimeError(result.error_message or "Notification provider failed")
        except Exception:
            if strict:
                raise
