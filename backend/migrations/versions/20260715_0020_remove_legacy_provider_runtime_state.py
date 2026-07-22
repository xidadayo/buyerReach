"""remove legacy local quota and circuit state

Revision ID: 20260715_0020
Revises: 20260715_0019
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260715_0020"
down_revision: str | None = "20260715_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE provider_config
        SET config = (
            config::jsonb
            - 'quota_soft_threshold'
            - 'circuit_open_until'
            - 'circuit_last_error'
            - 'quota_remaining'
            - 'quota_used'
            - 'quota_reset_at'
            - 'quota_checked_at'
        )::json
        WHERE config::jsonb ?| ARRAY[
            'quota_soft_threshold',
            'circuit_open_until',
            'circuit_last_error',
            'quota_remaining',
            'quota_used',
            'quota_reset_at',
            'quota_checked_at'
        ]
        """
    )


def downgrade() -> None:
    # Runtime snapshots were derived data and cannot be restored reliably.
    pass
