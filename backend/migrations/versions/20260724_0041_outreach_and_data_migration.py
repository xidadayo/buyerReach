"""Add auditable outreach and staged data-migration structures.

Revision ID: 20260724_0041
Revises: 20260724_0040
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision = "20260724_0041"
down_revision: str | None = "20260724_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("updated_by", sa.Uuid()),
    ]


def upgrade() -> None:
    op.create_table(
        "sending_account",
        *_id_columns(),
        sa.Column("organization_id", sa.Uuid(), index=True),
        sa.Column("department_id", sa.Uuid(), index=True),
        sa.Column("owner_id", sa.Uuid(), index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False, server_default="disabled"),
        sa.Column("credential_id", sa.Uuid(), sa.ForeignKey("vendor_credential.id")),
        sa.Column("from_email", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(160)),
        sa.Column("status", sa.String(30), nullable=False, server_default="disabled"),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config", sa.JSON(), nullable=False),
    )
    op.create_table(
        "email_template",
        *_id_columns(),
        sa.Column("organization_id", sa.Uuid(), index=True),
        sa.Column("department_id", sa.Uuid(), index=True),
        sa.Column("owner_id", sa.Uuid(), index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_by", sa.Uuid()),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
    )
    op.create_table(
        "outreach_campaign",
        *_id_columns(),
        sa.Column("organization_id", sa.Uuid(), index=True),
        sa.Column("department_id", sa.Uuid(), index=True),
        sa.Column("owner_id", sa.Uuid(), index=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("sending_account_id", sa.Uuid(), sa.ForeignKey("sending_account.id")),
        sa.Column("configuration_snapshot", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("user.id")),
    )
    op.create_table(
        "outreach_step",
        *_id_columns(),
        sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("outreach_campaign.id"), nullable=False),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        sa.Column("delay_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("email_template.id"), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.UniqueConstraint("campaign_id", "sequence_order", name="uq_outreach_step_order"),
    )
    op.create_table(
        "outreach_recipient",
        *_id_columns(),
        sa.Column("organization_id", sa.Uuid(), index=True),
        sa.Column("department_id", sa.Uuid(), index=True),
        sa.Column("owner_id", sa.Uuid(), index=True),
        sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("outreach_campaign.id"), nullable=False),
        sa.Column("email_id", sa.Uuid(), sa.ForeignKey("email_address.id"), nullable=False),
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contact.id")),
        sa.Column("status", sa.String(40), nullable=False, server_default="queued"),
        sa.Column("next_send_at", sa.DateTime(timezone=True)),
        sa.Column("stop_reason", sa.String(80)),
        sa.UniqueConstraint("campaign_id", "email_id", name="uq_outreach_campaign_email"),
    )
    op.create_table(
        "outreach_message",
        *_id_columns(),
        sa.Column(
            "recipient_id", sa.Uuid(), sa.ForeignKey("outreach_recipient.id"), nullable=False
        ),
        sa.Column("step_id", sa.Uuid(), sa.ForeignKey("outreach_step.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="queued"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("subject_snapshot", sa.String(500), nullable=False),
        sa.Column("body_text_snapshot", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.UniqueConstraint("recipient_id", "step_id", name="uq_outreach_recipient_step"),
    )
    op.create_table(
        "outreach_event",
        *_id_columns(),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("outreach_message.id")),
        sa.Column("email_id", sa.Uuid(), sa.ForeignKey("email_address.id"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("provider_event_id", sa.String(255), unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "data_import_batch",
        *_id_columns(),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(255)),
        sa.Column("file_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="uploaded"),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("organization_unit_id", sa.Uuid(), sa.ForeignKey("organization_unit.id")),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("rollback_until", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "data_import_row",
        *_id_columns(),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("data_import_batch.id"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("normalized_data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("match_entity_id", sa.Uuid()),
        sa.Column("conflict", sa.JSON(), nullable=False),
        sa.Column("applied_entity_id", sa.Uuid()),
        sa.UniqueConstraint("batch_id", "row_number", name="uq_data_import_row_number"),
    )
    for table, cols in [
        ("outreach_campaign", ["status"]),
        ("outreach_recipient", ["status", "next_send_at"]),
        ("outreach_message", ["status", "scheduled_at"]),
        ("outreach_event", ["email_id", "event_type"]),
        ("data_import_batch", ["status", "organization_id"]),
        ("data_import_row", ["batch_id", "status"]),
    ]:
        op.create_index(f"ix_{table}_{'_'.join(cols)}", table, cols)


def downgrade() -> None:
    for table in [
        "data_import_row",
        "data_import_batch",
        "outreach_event",
        "outreach_message",
        "outreach_recipient",
        "outreach_step",
        "outreach_campaign",
        "email_template",
        "sending_account",
    ]:
        op.drop_table(table)
