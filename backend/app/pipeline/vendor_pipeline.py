"""Per-vendor independent pipeline execution.

Each vendor's pipeline runs from company search through contact/email discovery.
Pipelines are independent — one vendor's failure does not affect another's results.
All calls go through existing `execute_provider()`, `enabled_providers()` etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.models import (
    Brand,
    Company,
    Contact,
    ContactPosition,
    EmailAddress,
    ProviderConfig,
    SearchTask,
    TaskItem,
    TaskVendorPlan,
)
from app.modules.services import (
    _candidate_matches_customer,
    _record_provider_fallback,
    _record_task_result,
    enabled_providers,
    execute_provider_waterfall,
    ensure_task_vendor_plan,
    record_usage,
    to_dict,
)
from app.providers.base import ProviderResult
from app.providers.local import slugify
from app.shared.enums import EmailPool, EmailStatus, TaskStatus
from app.shared.models import utc_now


@dataclass
class PipelineStageResult:
    """Result of one stage within a vendor's pipeline."""
    stage: str
    vendor: str
    ok: bool
    items: list[dict[str, Any]] = field(default_factory=list)
    raw_count: int = 0
    error_code: str | None = None
    error_message: str | None = None
    cost: float = 0.0
    vendor_request_id: str | None = None


@dataclass
class VendorPipelineResult:
    """Complete result of one vendor's full pipeline execution."""
    vendor: str
    ok: bool
    companies: list[dict[str, Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    emails: list[dict[str, Any]] = field(default_factory=list)
    stages: list[PipelineStageResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_cost: float = 0.0
    provider_config: ProviderConfig | None = None


def execute_vendor_pipeline(
    db: Session,
    task: SearchTask,
    vendor: str,
) -> VendorPipelineResult:
    """Execute one vendor's complete pipeline: company → contact → email.

    Returns a structured result; errors are collected, not raised.
    Single-stage failures do not abort the pipeline.
    """
    result = VendorPipelineResult(vendor=vendor, ok=False)
    filters = task.filters if isinstance(task.filters, dict) else {}

    # ── Resolve the vendor's company_search provider ──────────────────────
    company_providers = [
        p for p in enabled_providers(db, "company_search", task)
        if _vendor_matches(p, vendor)
    ]
    if not company_providers:
        result.errors.append(f"{vendor}: no enabled company_search provider")
        return result

    company_provider = company_providers[0]
    result.provider_config = company_provider

    # ── Stage 1: Company Search ──────────────────────────────────────────
    company_stage = _execute_company_search(db, task, company_provider, vendor, filters)
    result.stages.append(company_stage)
    result.total_cost += company_stage.cost
    if not company_stage.ok:
        result.errors.append(f"{vendor} company_search: {company_stage.error_message}")
        return result  # No companies → pipeline cannot continue

    companies = company_stage.items
    if not companies:
        result.ok = True  # Successfully searched, no results found
        return result

    result.companies = companies

    # ── Stage 2: Contact Search ──────────────────────────────────────────
    target_titles = list(filters.get("target_titles") or [])
    if vendor == "apollo":
        contact_stage = _execute_apollo_contact_search(
            db, task, companies, target_titles, vendor
        )
    else:
        contact_stage = _execute_hunter_contact_search(
            db, task, companies, target_titles, vendor
        )
    result.stages.append(contact_stage)
    result.total_cost += contact_stage.cost
    if contact_stage.ok:
        result.contacts = contact_stage.items
    elif contact_stage.error_code not in {"no_results", "no_mapped_contacts"}:
        result.errors.append(f"{vendor} contact_search: {contact_stage.error_message}")

    # ── Stage 3: Email Discovery ─────────────────────────────────────────
    if result.contacts:
        email_stage = _execute_email_discovery(
            db, task, companies, result.contacts, vendor
        )
        result.stages.append(email_stage)
        result.total_cost += email_stage.cost
        if email_stage.ok:
            result.emails = email_stage.items
        if not email_stage.ok and email_stage.error_code != "no_email_returned":
            result.errors.append(f"{vendor} email: {email_stage.error_message}")

    result.ok = True
    return result


# ── Private stage implementations ────────────────────────────────────────────


def _vendor_matches(provider: ProviderConfig, vendor: str) -> bool:
    """Check if a provider belongs to *vendor* (e.g. 'apollo-contact-search' matches 'apollo')."""
    return (
        provider.provider == vendor
        or provider.provider.startswith(f"{vendor}-")
        or str(provider.provider).partition("-")[0] == vendor
    )


def _execute_company_search(
    db: Session,
    task: SearchTask,
    provider: ProviderConfig,
    vendor: str,
    filters: dict,
) -> PipelineStageResult:
    try:
        prov, items, errors = execute_provider_waterfall(
            db,
            "company_search",
            {
                "operation": "company_search",
                "brand_keywords": filters.get("brand_keywords", []),
                "official_domains": filters.get("official_domains", []),
                "countries": filters.get("countries", []),
                "categories": filters.get("categories", []),
                "mode": task.mode,
            },
            "companies",
            task=task,
            entity_type="company",
        )
        if prov is None:
            msg = "; ".join(errors) if errors else f"No enabled {vendor} company_search provider"
            return PipelineStageResult(
                stage="company_search", vendor=vendor, ok=False,
                error_code="no_provider", error_message=msg,
            )
        raw_count = len(items)
        return PipelineStageResult(
            stage="company_search", vendor=vendor, ok=True,
            items=items, raw_count=raw_count,
        )
    except Exception as exc:
        return PipelineStageResult(
            stage="company_search", vendor=vendor, ok=False,
            error_code="exception", error_message=str(exc)[:2000],
        )


def _execute_apollo_contact_search(
    db: Session,
    task: SearchTask,
    companies: list[dict],
    target_titles: list[str],
    vendor: str,
) -> PipelineStageResult:
    contacts: list[dict] = []
    for company in companies:
        domain = company.get("domain") or ""
        try:
            prov, items, errors = execute_provider_waterfall(
                db,
                "contact_search",
                {
                    "operation": "contact_search",
                    "company": company,
                    "domain": domain,
                    "titles": target_titles,
                    "limit": int(task.filters.get("contacts_limit_per_brand") or 5),
                },
                "contacts",
                task=task,
                entity_type="contact",
            )
            if prov is None or not items:
                continue
            # Apollo people search returns contacts; run bulk enrichment
            enriched = _apollo_bulk_enrich(db, task, prov, company, items)
            contacts.extend(enriched)
        except Exception:
            continue

    if not contacts:
        return PipelineStageResult(
            stage="contact_search", vendor=vendor, ok=False,
            error_code="no_mapped_contacts", error_message="no mapped contacts returned",
        )
    return PipelineStageResult(
        stage="contact_search", vendor=vendor, ok=True,
        items=contacts, raw_count=len(contacts),
    )


def _apollo_bulk_enrich(
    db: Session,
    task: SearchTask,
    provider: ProviderConfig,
    company: dict,
    contacts: list[dict],
) -> list[dict]:
    """Run Apollo bulk_match to enrich contacts with emails/phones."""
    from app.core.crypto import decrypt_provider_config
    from app.modules.services import _enrich_contacts_with_apollo

    config = decrypt_provider_config(provider.config or {})
    if (
        str(config.get("adapter") or "").lower() != "apollo"
        or not str(config.get("bulk_enrichment_endpoint_url") or "").strip()
    ):
        return contacts
    try:
        enriched = _enrich_contacts_with_apollo(db, task, provider, company, contacts)
        return enriched
    except Exception:
        return contacts


def _execute_hunter_contact_search(
    db: Session,
    task: SearchTask,
    companies: list[dict],
    target_titles: list[str],
    vendor: str,
) -> PipelineStageResult:
    contacts: list[dict] = []
    for company in companies:
        domain = company.get("domain") or ""
        try:
            prov, items, errors = execute_provider_waterfall(
                db,
                "contact_search",
                {
                    "company": company,
                    "domain": domain,
                    "titles": target_titles,
                    "limit": int(task.filters.get("contacts_limit_per_brand") or 5),
                },
                "contacts",
                task=task,
                entity_type="contact",
            )
            if prov is None or not items:
                continue
            contacts.extend(items)
        except Exception:
            continue

    if not contacts:
        return PipelineStageResult(
            stage="contact_search", vendor=vendor, ok=False,
            error_code="no_results", error_message="Hunter Domain Search returned no contacts",
        )
    return PipelineStageResult(
        stage="contact_search", vendor=vendor, ok=True,
        items=contacts, raw_count=len(contacts),
    )


def _execute_email_discovery(
    db: Session,
    task: SearchTask,
    companies: list[dict],
    contacts: list[dict],
    vendor: str,
) -> PipelineStageResult:
    """Discover emails. Apollo contacts already carry emails from search/enrichment.
    Hunter contacts need email_finder calls."""
    emails: list[dict] = []

    if vendor == "apollo":
        # Apollo contacts already have emails from people search or bulk_match
        for contact in contacts:
            contact_emails = contact.get("emails", [])
            email_addr = contact.get("email")
            if email_addr:
                contact_emails.append(email_addr)
            for addr in set(contact_emails):
                if addr:
                    emails.append({"address": str(addr), "contact": contact, "vendor": vendor})
        if not emails:
            return PipelineStageResult(
                stage="email_discovery", vendor=vendor, ok=False,
                error_code="no_email_returned",
                error_message="Apollo returned no email addresses for contacts",
            )
    else:
        # Hunter: call email_finder for each contact, then domain search for brands without contacts
        for company in companies:
            domain = company.get("domain") or ""
            for contact in contacts:
                try:
                    prov, items, errors = execute_provider_waterfall(
                        db,
                        "email_finder",
                        {
                            "contact": contact,
                            "domain": domain,
                            "first_name": contact.get("first_name"),
                            "last_name": contact.get("last_name"),
                        },
                        "emails",
                        task=task,
                        entity_type="email",
                    )
                    if prov is not None and items:
                        for item in items:
                            addr = item.get("address") or item.get("email")
                            if addr:
                                emails.append(
                                    {"address": str(addr), "contact": contact, "vendor": vendor}
                                )
                except Exception:
                    continue
        if not emails:
            return PipelineStageResult(
                stage="email_discovery", vendor=vendor, ok=False,
                error_code="no_email_returned",
                error_message="Hunter Email Finder returned no emails",
            )

    return PipelineStageResult(
        stage="email_discovery", vendor=vendor, ok=True,
        items=emails, raw_count=len(emails),
    )


# ── Top-level pipeline mode orchestrator ─────────────────────────────────────


def execute_pipeline_mode(
    db: Session,
    task: SearchTask,
    plan: TaskVendorPlan,
) -> tuple[list[dict], list[str], str | None]:
    """Execute selected vendors' pipelines sequentially.

    Returns (all_companies, all_errors, primary_provider_name).
    Each vendor's results are persisted independently via the existing ingestion
    functions. Cross-vendor dedup is handled by DiscoveryCandidate.dedupe_key.
    """
    selected = list(plan.selected_vendors or [])
    if not selected:
        return [], ["No vendors selected for pipeline execution"], None

    all_companies: list[dict] = []
    all_errors: list[str] = []
    primary_provider_name: str | None = None

    for vendor in selected:
        if task.status in {TaskStatus.cancelled, TaskStatus.paused}:
            break

        pipeline_result = execute_vendor_pipeline(db, task, vendor)

        if pipeline_result.companies:
            all_companies.extend(pipeline_result.companies)
            if primary_provider_name is None:
                primary_provider_name = vendor

        all_errors.extend(pipeline_result.errors)

        # ── Persist contacts and emails from this pipeline ────────────────
        if pipeline_result.contacts or pipeline_result.emails:
            _persist_pipeline_contacts_and_emails(db, task, pipeline_result)

    return all_companies, all_errors, primary_provider_name


def _persist_pipeline_contacts_and_emails(
    db: Session,
    task: SearchTask,
    result: VendorPipelineResult,
) -> None:
    """Create Contact, ContactPosition, EmailAddress from a pipeline result."""
    from app.modules.services import create_brand, create_contact, create_email

    for company in result.companies[:]:
        brand_name = str(company.get("brand_name") or company.get("name") or "").strip()
        if not brand_name:
            continue

        # Create or find Company
        from app.modules.services import get_or_create_company
        comp = get_or_create_company(db, company)

        # Create Brand
        from app.modules.schemas import BrandCreate
        brand = create_brand(
            db,
            BrandCreate(
                name=brand_name,
                website=company.get("website") or company.get("domain"),
                country=company.get("country"),
                category=company.get("category"),
            ),
            company=comp,
            provider=result.vendor,
        )

        # Create contacts for this company
        for contact_payload in result.contacts:
            contact = create_contact(
                db,
                ContactCreate(
                    brand_id=brand.id,
                    company_id=comp.id,
                    first_name=str(contact_payload.get("first_name") or ""),
                    last_name=str(contact_payload.get("last_name") or ""),
                    title=str(contact_payload.get("title") or ""),
                    linkedin_url=contact_payload.get("linkedin_url"),
                ),
                provider=result.vendor,
            )
            # Create emails for this contact
            for email_payload in result.emails:
                if email_payload.get("contact") == contact_payload:
                    create_email(
                        db,
                        EmailCreate(
                            contact_id=contact.id,
                            address=email_payload["address"],
                        ),
                        provider=result.vendor,
                    )
