"""Task-frozen Apollo/Hunter execution for company-to-email workflows.

Vendor protocol remains in the versioned adapters.  This module only applies
the task plan, keeps company/contact scope intact, and persists normalized
results through the existing domain services.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.brand_discovery import filter_discovery_companies, filter_exact_brand_companies
from app.modules.models import (
    PipelineStageRun,
    ProviderConfig,
    SearchTask,
    SourceEvidence,
    TaskVendorPlan,
)
from app.modules.schemas import BrandCreate, ContactCreate, EmailCreate
from app.modules.services import (
    _apply_provider_verification,
    _ensure_email_verified,
    _record_task_result,
    _title_matches_targets,
    create_brand,
    create_contact,
    create_email,
    enabled_providers,
    execute_provider_waterfall,
    get_or_create_company,
)
from app.shared.enums import SourceType, TaskStatus
from app.shared.models import utc_now


@dataclass
class PipelineStageResult:
    stage: str
    vendor: str
    ok: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class CompanyPipelineResult:
    company: dict[str, Any]
    contacts: list[dict[str, Any]] = field(default_factory=list)
    emails: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VendorPipelineResult:
    vendor: str
    ok: bool
    companies: list[CompanyPipelineResult] = field(default_factory=list)
    stages: list[PipelineStageResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    provider_config: ProviderConfig | None = None
    stage_run: PipelineStageRun | None = None


def _provider_vendor(provider: ProviderConfig) -> str:
    config = provider.config if isinstance(provider.config, dict) else {}
    return str(config.get("adapter") or provider.provider.partition("-")[0]).casefold()


def _provider_for(db: Session, task: SearchTask, provider_type: str, vendor: str) -> ProviderConfig | None:
    return next(
        (
            provider
            for provider in enabled_providers(db, provider_type, task)
            if _provider_vendor(provider) == vendor
        ),
        None,
    )


def _company_filter(task: SearchTask, filters: dict[str, Any] | None = None):
    effective_filters = filters or task.filters
    if task.mode == "exact_brand" or effective_filters.get("mode") == "exact_brand":
        return lambda items: filter_exact_brand_companies(items, effective_filters)
    return lambda items: filter_discovery_companies(items, effective_filters)


def execute_vendor_pipeline(
    db: Session,
    task: SearchTask,
    vendor: str,
    *,
    filters: dict[str, Any] | None = None,
) -> VendorPipelineResult:
    """Run one selected Vendor without falling through to another Vendor."""
    effective_filters = filters or task.filters
    result = VendorPipelineResult(vendor=vendor, ok=False)
    company_provider = _provider_for(db, task, "company_search", vendor)
    if company_provider is None:
        result.errors.append(f"{vendor}: no frozen company_search provider is available")
        return result
    result.provider_config = company_provider

    provider, companies, errors = execute_provider_waterfall(
        db,
        "company_search",
        effective_filters,
        "companies",
        task=task,
        entity_type="company",
        item_filter=_company_filter(task, effective_filters),
        allowed_vendors={vendor},
    )
    company_stage = PipelineStageResult(
        stage="company_search",
        vendor=vendor,
        ok=provider is not None,
        items=companies,
        error_code=None if provider is not None else "company_search_failed",
        error_message="; ".join(errors) or None,
    )
    result.stages.append(company_stage)
    if provider is None:
        result.errors.extend(errors or [f"{vendor}: company search failed"])
        return result

    limit = max(int(effective_filters.get("brand_limit") or 100), 0)
    for company in companies[:limit]:
        if task.status in {TaskStatus.cancelled, TaskStatus.paused}:
            break
        scoped = CompanyPipelineResult(company=company)
        scoped.contacts = _search_contacts(
            db, task, vendor, company, result, filters=effective_filters
        )
        scoped.emails = _discover_emails(db, task, vendor, company, scoped.contacts, result)
        result.companies.append(scoped)

    result.ok = True
    return result


def _search_contacts(
    db: Session,
    task: SearchTask,
    vendor: str,
    company: dict[str, Any],
    pipeline: VendorPipelineResult,
    *,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    effective_filters = filters or task.filters
    provider, contacts, errors = execute_provider_waterfall(
        db,
        "contact_search",
        {
            "operation": "contact_search",
            "company": company,
            "domain": company.get("domain") or "",
            "titles": list(effective_filters.get("target_titles") or []),
            "limit": int(effective_filters.get("contacts_limit_per_brand") or 5),
        },
        "contacts",
        task=task,
        entity_type="contact",
        allowed_vendors={vendor},
    )
    if provider is None:
        pipeline.stages.append(
            PipelineStageResult(
                stage="contact_search",
                vendor=vendor,
                ok=False,
                error_code="no_results" if not errors else "contact_search_failed",
                error_message="; ".join(errors) or "No contacts returned",
            )
        )
        return []

    if vendor == "apollo" and contacts:
        from app.modules.services import _enrich_contacts_with_apollo

        contacts = _enrich_contacts_with_apollo(db, task, provider, company, contacts)
    contacts = _filter_contacts_for_titles(
        contacts, list(effective_filters.get("target_titles") or [])
    )
    pipeline.stages.append(
        PipelineStageResult(stage="contact_search", vendor=vendor, ok=True, items=contacts)
    )
    return contacts[: int(effective_filters.get("contacts_limit_per_brand") or 5)]


def _filter_contacts_for_titles(
    contacts: list[dict[str, Any]], target_titles: list[str]
) -> list[dict[str, Any]]:
    return [
        contact
        for contact in contacts
        if _title_matches_targets(str(contact.get("title") or ""), target_titles)
    ]


def _contact_key(contact: dict[str, Any]) -> str:
    provider_id = str(contact.get("provider_person_id") or "").strip()
    if provider_id:
        return f"provider:{provider_id}"
    linkedin = str(contact.get("linkedin_url") or "").strip().casefold()
    if linkedin:
        return f"linkedin:{linkedin}"
    return "name:" + "|".join(
        str(contact.get(key) or "").strip().casefold()
        for key in ("first_name", "last_name", "title")
    )


def _discover_emails(
    db: Session,
    task: SearchTask,
    vendor: str,
    company: dict[str, Any],
    contacts: list[dict[str, Any]],
    pipeline: VendorPipelineResult,
) -> list[dict[str, Any]]:
    emails: list[dict[str, Any]] = []
    for contact in contacts:
        details_by_address = {
            str(item.get("address") or "").strip().casefold(): item
            for item in contact.get("email_details") or []
            if isinstance(item, dict) and str(item.get("address") or "").strip()
        }
        addresses = list(contact.get("emails") or [])
        if contact.get("email"):
            addresses.append(contact["email"])
        for address in dict.fromkeys(str(value).strip() for value in addresses if str(value).strip()):
            evidence = details_by_address.get(address.casefold(), {})
            emails.append(
                {
                    "address": address,
                    "contact_key": _contact_key(contact),
                    **{
                        key: evidence[key]
                        for key in (
                            "verification_status",
                            "verification_source",
                            "verification_provider",
                        )
                        if evidence.get(key)
                    },
                }
            )

        if vendor != "hunter" or addresses:
            continue
        provider, items, _ = execute_provider_waterfall(
            db,
            "email_finder",
            {
                "contact": contact,
                "domain": company.get("domain") or "",
                "first_name": contact.get("first_name"),
                "last_name": contact.get("last_name"),
            },
            "emails",
            task=task,
            entity_type="email",
            allowed_vendors={vendor},
        )
        if provider is not None:
            for item in items:
                address = str(item.get("address") or item.get("email") or "").strip()
                if address:
                    emails.append({"address": address, "contact_key": _contact_key(contact)})

    deduped = list({(item["address"].casefold(), item["contact_key"]): item for item in emails}.values())
    pipeline.stages.append(
        PipelineStageResult(
            stage="email_discovery",
            vendor=vendor,
            ok=True,
            items=deduped,
            error_code=None if deduped else "no_email_returned",
            error_message=None if deduped else f"{vendor} returned no email addresses",
        )
    )
    return deduped


def execute_pipeline_mode(
    db: Session,
    task: SearchTask,
    plan: TaskVendorPlan,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Execute and durably commit each selected Vendor independently."""
    all_companies: list[dict[str, Any]] = []
    all_errors: list[str] = []
    primary: str | None = None

    for vendor in list(plan.selected_vendors or []):
        if task.status in {TaskStatus.cancelled, TaskStatus.paused}:
            break
        from app.pipeline.runner import begin_stage, complete_stage, fail_stage

        stage_run = begin_stage(
            db,
            task.id,
            "provider_search",
            {"vendor": vendor, "mode": task.mode, "filters": task.filters},
        )
        result = execute_vendor_pipeline(db, task, vendor)
        result.stage_run = stage_run
        all_errors.extend(result.errors)
        if result.ok:
            complete_stage(
                stage_run,
                {
                    "vendor": vendor,
                    "accepted_company_count": len(result.companies),
                    "stage_count": len(result.stages),
                },
            )
        else:
            fail_stage(
                stage_run,
                RuntimeError("; ".join(result.errors) or f"{vendor} pipeline failed"),
                retryable=True,
            )
        if result.companies:
            primary = primary or vendor
            all_companies.extend(item.company for item in result.companies)
            _persist_vendor_result(db, task, result)
            # A later Vendor failure must not roll back this Vendor's accepted results.
            db.commit()
            db.refresh(task)

    return _dedupe_companies(all_companies), all_errors, primary


def _dedupe_companies(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for company in companies:
        domain = str(company.get("domain") or company.get("website") or "").strip().casefold()
        name = str(company.get("brand_name") or company.get("name") or "").strip().casefold()
        country = str(company.get("country") or "").strip().casefold()
        key = f"domain:{domain}" if domain else f"name:{name}|country:{country}"
        deduped.setdefault(key, company)
    return list(deduped.values())


def _persist_vendor_result(
    db: Session, task: SearchTask, result: VendorPipelineResult
) -> dict[str, set[str]]:
    persisted = {"brand_ids": set(), "contact_ids": set(), "email_ids": set()}
    for scoped in result.companies:
        company_payload = scoped.company
        brand_name = str(company_payload.get("brand_name") or company_payload.get("name") or "").strip()
        if not brand_name:
            continue
        company = get_or_create_company(db, company_payload)
        brand = create_brand(
            db,
            BrandCreate(
                name=brand_name,
                company_name=company.legal_name,
                website=company_payload.get("website") or company_payload.get("domain"),
                country=company_payload.get("country"),
                category=company_payload.get("category"),
            ),
            company=company,
            source_type=SourceType.commercial_api,
            provider=result.vendor,
            source_url=company_payload.get("source_url"),
            source_title=company_payload.get("source_title"),
            source_excerpt=company_payload.get("source_excerpt"),
            discovery_score=int(company_payload.get("relevance_score") or 0),
        )
        _record_task_result(db, task, "brand", brand.id, "brand_discovered", result.vendor)
        _record_source(
            db, task, "brand", brand.id, result.vendor, company_payload, result.stage_run
        )
        persisted["brand_ids"].add(str(brand.id))

        contact_by_key: dict[str, Any] = {}
        for payload in scoped.contacts:
            first_name = str(payload.get("first_name") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not first_name or not title:
                continue
            contact = create_contact(
                db,
                ContactCreate(
                    brand_id=brand.id,
                    company_id=company.id,
                    first_name=first_name,
                    last_name=str(payload.get("last_name") or ""),
                    title=title,
                    linkedin_url=payload.get("linkedin_url"),
                ),
                provider=result.vendor,
                organization_id=task.organization_id,
            )
            contact_by_key[_contact_key(payload)] = contact
            _record_task_result(db, task, "contact", contact.id, "contact_discovered", result.vendor)
            _record_source(
                db, task, "contact", contact.id, result.vendor, payload, result.stage_run
            )
            persisted["contact_ids"].add(str(contact.id))

        for payload in scoped.emails:
            contact = contact_by_key.get(str(payload.get("contact_key") or ""))
            if contact is None:
                continue
            try:
                email = create_email(
                    db,
                    EmailCreate(contact_id=contact.id, brand_id=brand.id, address=payload["address"]),
                    provider=result.vendor,
                )
            except (ValueError, IndexError):
                continue
            _record_task_result(db, task, "email", email.id, "email_discovered", result.vendor)
            _record_source(
                db, task, "email", email.id, result.vendor, payload, result.stage_run
            )
            _apply_provider_verification(db, email, payload, result.vendor)
            _ensure_email_verified(db, email, task=task)
            persisted["email_ids"].add(str(email.id))
    return persisted


def _record_source(
    db: Session,
    task: SearchTask,
    entity_type: str,
    entity_id,
    vendor: str,
    payload: dict[str, Any],
    stage_run: PipelineStageRun | None,
) -> None:
    safe_payload = {
        key: value
        for key, value in payload.items()
        if key.casefold() not in {"api_key", "authorization", "token", "password", "secret"}
    }
    canonical = json.dumps(safe_payload, sort_keys=True, default=str, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    exists = db.scalar(
        select(SourceEvidence.id).where(
            SourceEvidence.entity_type == entity_type,
            SourceEvidence.entity_id == str(entity_id),
            SourceEvidence.provider == vendor,
            SourceEvidence.content_hash == content_hash,
        )
    )
    if exists is not None:
        return
    plan = db.scalar(select(TaskVendorPlan).where(TaskVendorPlan.task_id == task.id))
    route = (
        (plan.vendor_routes or {}).get(vendor, {})
        if plan is not None and isinstance(plan.vendor_routes, dict)
        else {}
    )
    db.add(
        SourceEvidence(
            entity_type=entity_type,
            entity_id=str(entity_id),
            source_type=SourceType.commercial_api,
            provider=vendor,
            title=f"{vendor} {entity_type} result",
            content_hash=content_hash,
            task_id=task.id,
            stage_run_id=stage_run.id if stage_run is not None else None,
            provider_record_id=str(payload.get("provider_person_id") or payload.get("id") or "") or None,
            vendor_request_id=(
                str(payload.get("vendor_request_id") or "")
                or (stage_run.vendor_request_id if stage_run is not None else None)
            ),
            adapter_version=str(route.get("adapter_version") or "") or None,
            input_hash=stage_run.input_hash if stage_run is not None else content_hash,
            observed_at=utc_now(),
            normalized_evidence=safe_payload,
        )
    )
