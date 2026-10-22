"""Yönetim sistem sağlığı grafikleri için ölçüm geçmişi.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22 08:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_sample",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_queue_count", sa.Integer(), nullable=False),
        sa.Column("pending_ingest", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_health_sample_captured_at", "health_sample", ["captured_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_health_sample_captured_at", table_name="health_sample")
    op.drop_table("health_sample")
