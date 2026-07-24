"""Durable, permission-gated outreach orchestration.

No vendor is called here: provider adapters are enabled only by a governed
SendingAccount.  This keeps an upgrade safe while still exercising every
database state transition and idempotency boundary.
"""

from datetime import timedelta
import re
import json
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.models import (
    AuditLog,
    Blacklist,
    EmailAddress,
    EmailTemplate,
    OutreachCampaign,
    OutreachEvent,
    OutreachMessage,
    OutreachRecipient,
    OutreachStep,
    SendingAccount,
)
from app.shared.models import utc_now

STOP_EVENTS = {"reply", "unsubscribe", "bounce", "complaint"}
ALLOWED_VARIABLES = {
    "first_name",
    "last_name",
    "full_name",
    "company_name",
    "brand_name",
    "job_title",
    "email",
}


def _variables(text: str) -> set[str]:
    return set(re.findall(r"{{\s*([a-z_][a-z0-9_]*)\s*}}", text.casefold()))


def _render(template: EmailTemplate, email: EmailAddress) -> tuple[dict, list[str]]:
    contact = None
    values = {"email": email.address, **(template.variable_defaults or {})}
    if email.contact_id:
        from app.modules.models import Contact

        contact = (
            email._sa_instance_state.session.get(Contact, email.contact_id)
            if email._sa_instance_state.session
            else None
        )
    if contact:
        values.update(
            {
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "full_name": contact.full_name,
            }
        )
    missing: list[str] = []

    def substitute(value: str) -> str:
        def replacement(match):
            key = match.group(1).strip().casefold()
            result = values.get(key)
            if result is None or str(result).strip() == "":
                missing.append(key)
                return match.group(0)
            return str(result)

        return re.sub(r"{{\s*([a-z_][a-z0-9_]*)\s*}}", replacement, value)

    return {
        "subject": substitute(template.subject),
        "body_text": substitute(template.body_text),
        "body_html": substitute(template.body_html),
    }, sorted(set(missing))


def _audit(
    db: Session, actor_id: UUID, action: str, entity_type: str, entity_id: UUID, after: dict
) -> None:
    db.add(
        AuditLog(
            actor_id=str(actor_id),
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            after=after,
        )
    )


def create_template(db: Session, payload, actor) -> EmailTemplate:
    variables = _variables(payload.subject + "\n" + payload.body_text + "\n" + payload.body_html)
    invalid = variables - ALLOWED_VARIABLES
    if invalid:
        raise ValueError(f"Unsupported template variables: {', '.join(sorted(invalid))}")
    item = EmailTemplate(
        **payload.model_dump(),
        organization_id=actor.organization_id,
        department_id=actor.organization_unit_id,
        owner_id=actor.id,
        created_by=actor.id,
    )
    db.add(item)
    db.flush()
    _audit(db, actor.id, "outreach.template.create", "email_template", item.id, {"version": 1})
    return item


def preview_template(db: Session, template: EmailTemplate, email_id: UUID) -> dict:
    email = db.get(EmailAddress, email_id)
    if not email or email.organization_id != template.organization_id:
        raise ValueError("Recipient email unavailable")
    rendered, missing = _render(template, email)
    return {
        "template_id": str(template.id),
        "email_id": str(email.id),
        "rendered": rendered,
        "missing_variables": missing,
        "send_allowed": not missing or template.missing_variable_policy == "fallback",
    }


def generate_ai_draft(db: Session, payload, actor) -> dict:
    """Generate a review-only draft from owned customer records and stored evidence."""
    from app.modules.models import Brand, SourceEvidence
    from app.modules.services import get_ai_settings

    email = db.get(EmailAddress, payload.email_id) if payload.email_id else None
    if email and email.organization_id != actor.organization_id:
        raise ValueError("Recipient email unavailable")
    brand = db.get(Brand, email.brand_id) if email and email.brand_id else None
    evidence = []
    if brand and payload.use_website_evidence:
        evidence = [
            x.excerpt
            for x in db.scalars(
                select(SourceEvidence)
                .where(
                    SourceEvidence.entity_type == "brand", SourceEvidence.entity_id == str(brand.id)
                )
                .order_by(SourceEvidence.observed_at.desc())
                .limit(5)
            ).all()
            if x.excerpt
        ]
    context = {
        "brand": brand.name if brand else None,
        "website": brand.primary_website if brand else None,
        "goal": payload.goal,
        "tone": payload.tone,
        "language": payload.language,
        "evidence": evidence,
    }
    settings = get_ai_settings(db)
    if not settings.get("enabled") or not settings.get("api_key"):
        return {
            "source": "local_fallback",
            "warning": "AI 未配置，已生成待编辑草稿",
            "evidence": evidence,
            "subject": f"关于 {brand.name if brand else '合作'} 的合作咨询",
            "body_text": f"您好，\n\n我关注到贵司{brand.name if brand else ''}。{payload.goal}\n\n期待交流。",
            "context": context,
        }
    body = {
        "model": settings["model_name"],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Write a truthful B2B outreach email draft using ONLY supplied evidence. Return JSON {subject,body_text,warnings}. Never invent customer facts; if evidence is insufficient, say so in warnings.",
            },
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    }
    request = Request(
        f"{settings['base_url'].rstrip('/')}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(
        request, timeout=min(int(settings.get("request_timeout_seconds") or 30), 45)
    ) as response:  # nosec B310: admin configured endpoint
        result = json.loads(
            json.loads(response.read().decode())["choices"][0]["message"]["content"]
        )
    return {
        "source": "ai",
        "evidence": evidence,
        "subject": str(result.get("subject") or "")[:500],
        "body_text": str(result.get("body_text") or "")[:100000],
        "warnings": result.get("warnings") or [],
        "context": context,
    }


def create_campaign(db: Session, payload, actor) -> OutreachCampaign:
    account = (
        db.get(SendingAccount, payload.sending_account_id) if payload.sending_account_id else None
    )
    if account and (account.organization_id != actor.organization_id or account.status != "active"):
        raise ValueError("Sending account is unavailable")
    campaign = OutreachCampaign(
        name=payload.name,
        status="draft",
        sending_account_id=payload.sending_account_id,
        organization_id=actor.organization_id,
        department_id=actor.organization_unit_id,
        owner_id=actor.id,
        configuration_snapshot={
            "schema_version": "outreach-1",
            "created_at": utc_now().isoformat(),
        },
        created_by=actor.id,
    )
    db.add(campaign)
    db.flush()
    seen_orders: set[int] = set()
    for raw in payload.steps:
        order, template_id, delay = (
            int(raw.get("sequence_order", 0)),
            raw.get("template_id"),
            int(raw.get("delay_minutes", 0)),
        )
        if order < 1 or order in seen_orders or delay < 0:
            raise ValueError("Invalid or duplicate sequence step")
        template = db.get(EmailTemplate, template_id)
        if (
            not template
            or template.organization_id != actor.organization_id
            or template.status == "archived"
        ):
            raise ValueError("Template unavailable")
        seen_orders.add(order)
        db.add(
            OutreachStep(
                campaign_id=campaign.id,
                sequence_order=order,
                delay_minutes=delay,
                template_id=template.id,
                template_version=template.version,
                created_by=actor.id,
            )
        )
    emails = db.scalars(
        select(EmailAddress).where(
            EmailAddress.id.in_(payload.email_ids),
            EmailAddress.organization_id == actor.organization_id,
            EmailAddress.deleted_at.is_(None),
        )
    ).all()
    if len(emails) != len(set(payload.email_ids)):
        raise ValueError("One or more recipients are unavailable")
    for email in emails:
        db.add(
            OutreachRecipient(
                campaign_id=campaign.id,
                email_id=email.id,
                contact_id=email.contact_id,
                organization_id=email.organization_id,
                department_id=email.department_id,
                owner_id=email.owner_id,
                status="queued",
                created_by=actor.id,
            )
        )
    _audit(
        db,
        actor.id,
        "outreach.campaign.create",
        "outreach_campaign",
        campaign.id,
        {"recipients": len(emails), "steps": len(seen_orders)},
    )
    return campaign


def approve_campaign(db: Session, campaign: OutreachCampaign, actor) -> None:
    if campaign.status != "draft":
        raise ValueError("Only draft campaigns can be approved")
    campaign.status, campaign.approved_at, campaign.approved_by = "approved", utc_now(), actor.id
    _audit(db, actor.id, "outreach.campaign.approve", "outreach_campaign", campaign.id, {})


def schedule_campaign(db: Session, campaign: OutreachCampaign, actor) -> int:
    if campaign.status != "approved":
        raise ValueError("Campaign requires approval before scheduling")
    steps = list(
        db.scalars(
            select(OutreachStep)
            .where(OutreachStep.campaign_id == campaign.id)
            .order_by(OutreachStep.sequence_order)
        ).all()
    )
    if not steps:
        raise ValueError("Campaign has no steps")
    first = steps[0]
    template = db.get(EmailTemplate, first.template_id)
    now = utc_now()
    created = 0
    recipients = db.scalars(
        select(OutreachRecipient).where(
            OutreachRecipient.campaign_id == campaign.id, OutreachRecipient.status == "queued"
        )
    ).all()
    for recipient in recipients:
        message = OutreachMessage(
            recipient_id=recipient.id,
            step_id=first.id,
            idempotency_key=f"outreach:{recipient.id}:{first.id}",
            status="queued",
            scheduled_at=now + timedelta(minutes=first.delay_minutes),
            subject_snapshot=template.subject,
            body_text_snapshot=template.body_text,
            created_by=actor.id,
        )
        db.add(message)
        recipient.next_send_at = message.scheduled_at
        created += 1
    campaign.status = "scheduled"
    _audit(
        db,
        actor.id,
        "outreach.campaign.schedule",
        "outreach_campaign",
        campaign.id,
        {"messages": created},
    )
    return created


def _suppression_reason(db: Session, email: EmailAddress) -> str | None:
    if db.scalar(
        select(Blacklist.id).where(
            Blacklist.type == "email", Blacklist.value == email.normalized_address
        )
    ):
        return "blacklisted"
    if db.scalar(
        select(OutreachEvent.id).where(
            OutreachEvent.email_id == email.id, OutreachEvent.event_type.in_(STOP_EVENTS)
        )
    ):
        return "prior_stop_event"
    return None


def dispatch_due_messages(db: Session, limit: int = 100) -> dict:
    """Atomically progresses due messages; external delivery stays disabled unless adapter exists."""
    now = utc_now()
    sent = blocked = 0
    due = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.status == "queued", OutreachMessage.scheduled_at <= now)
        .order_by(OutreachMessage.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for message in due:
        recipient = db.get(OutreachRecipient, message.recipient_id)
        campaign = db.get(OutreachCampaign, recipient.campaign_id)
        email = db.get(EmailAddress, recipient.email_id)
        reason = _suppression_reason(db, email)
        if reason or campaign.status != "scheduled":
            message.status = "cancelled"
            recipient.status = "stopped"
            recipient.stop_reason = reason or "campaign_not_active"
            blocked += 1
            continue
        account = (
            db.get(SendingAccount, campaign.sending_account_id)
            if campaign.sending_account_id
            else None
        )
        # Safe default: disabled/test accounts never call an external service.
        if not account or account.status != "active" or account.provider == "disabled":
            message.status = "blocked"
            message.error_code = "SENDING_DISABLED"
            message.error_message = "No active sending provider"
            blocked += 1
            continue
        # Adapter boundary: a provider implementation must durably return a provider id.
        message.status = "blocked"
        message.error_code = "ADAPTER_NOT_CONFIGURED"
        message.error_message = "Provider adapter is not configured"
        blocked += 1
    return {"processed": len(due), "sent": sent, "blocked": blocked}


def ingest_event(db: Session, payload, actor) -> OutreachEvent:
    if payload.provider_event_id:
        existing = db.scalar(
            select(OutreachEvent).where(
                OutreachEvent.provider_event_id == payload.provider_event_id
            )
        )
        if existing:
            return existing
    email = db.get(EmailAddress, payload.email_id)
    if not email or email.organization_id != actor.organization_id:
        raise ValueError("Email unavailable")
    event = OutreachEvent(
        message_id=payload.message_id,
        email_id=email.id,
        event_type=payload.event_type,
        provider_event_id=payload.provider_event_id,
        occurred_at=payload.occurred_at or utc_now(),
        payload=payload.payload,
        created_by=actor.id,
    )
    db.add(event)
    if payload.event_type in STOP_EVENTS:
        for recipient in db.scalars(
            select(OutreachRecipient).where(
                OutreachRecipient.email_id == email.id,
                OutreachRecipient.status.in_(["queued", "sending"]),
            )
        ).all():
            recipient.status, recipient.stop_reason, recipient.next_send_at = (
                "stopped",
                payload.event_type,
                None,
            )
            for message in db.scalars(
                select(OutreachMessage).where(
                    OutreachMessage.recipient_id == recipient.id, OutreachMessage.status == "queued"
                )
            ).all():
                message.status = "cancelled"
    _audit(db, actor.id, f"outreach.event.{payload.event_type}", "email_address", email.id, {})
    return event
