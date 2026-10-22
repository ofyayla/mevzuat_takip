"""Persist isolated prompt A/B experiments.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22 15:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_experiment",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("system_prompt_a", sa.Text(), nullable=False),
        sa.Column("system_prompt_b", sa.Text(), nullable=False),
        sa.Column("settings_snapshot", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_experiment_status", "prompt_experiment", ["status"], unique=False)
    op.create_table(
        "prompt_experiment_item",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("experiment_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("regulation_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("title_snapshot", sa.Text(), nullable=False),
        sa.Column("expected_relevant", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("result_a", sa.JSON(), nullable=True),
        sa.Column("result_b", sa.JSON(), nullable=True),
        sa.Column("error_a", sa.Text(), nullable=True),
        sa.Column("error_b", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["experiment_id"], ["prompt_experiment.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["regulation_id"], ["regulation.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "regulation_id", name="uq_prompt_experiment_regulation"),
    )
    op.create_index("ix_prompt_experiment_item_experiment_id", "prompt_experiment_item", ["experiment_id"],
                    unique=False)
    op.create_index("ix_prompt_experiment_item_regulation_id", "prompt_experiment_item", ["regulation_id"],
                    unique=False)


def downgrade() -> None:
    op.drop_index("ix_prompt_experiment_item_regulation_id", table_name="prompt_experiment_item")
    op.drop_index("ix_prompt_experiment_item_experiment_id", table_name="prompt_experiment_item")
    op.drop_table("prompt_experiment_item")
    op.drop_index("ix_prompt_experiment_status", table_name="prompt_experiment")
    op.drop_table("prompt_experiment")
