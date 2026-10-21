"""Persistent system health samples.

Revision ID: 0008
Revises: 0007
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
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True,
                  nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ai_queue", sa.Integer(), nullable=False),
        sa.Column("pending_ingest", sa.Integer(), nullable=True),
        sa.Column("crawl_mode", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_health_sample_captured_at", "health_sample", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_health_sample_captured_at", table_name="health_sample")
    op.drop_table("health_sample")
