"""Persist stratified evaluation sets and link prompt experiments to them.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-23 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "evaluation_set",
        sa.Column("id", _BIG, autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "evaluation_label",
        sa.Column("id", _BIG, autoincrement=True, nullable=False),
        sa.Column("set_id", _BIG, nullable=False),
        sa.Column("regulation_id", _BIG, nullable=False),
        sa.Column("title_snapshot", sa.Text(), nullable=False),
        sa.Column("stratum", sa.String(length=24), nullable=False),
        sa.Column("expected_relevant", sa.Boolean(), nullable=True),
        sa.Column("label_source", sa.String(length=16), nullable=False),
        sa.Column("excel_row", sa.Integer(), nullable=True),
        sa.Column("match_method", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("labelled_by", sa.String(length=200), nullable=False),
        sa.Column("labelled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["set_id"], ["evaluation_set.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["regulation_id"], ["regulation.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("set_id", "regulation_id", name="uq_evaluation_label_regulation"),
    )
    op.create_index("ix_evaluation_label_set_id", "evaluation_label", ["set_id"], unique=False)
    op.create_index("ix_evaluation_label_regulation_id", "evaluation_label", ["regulation_id"], unique=False)

    with op.batch_alter_table("prompt_experiment", schema=None) as batch_op:
        batch_op.add_column(sa.Column("evaluation_set_id", _BIG, nullable=True))
        batch_op.create_foreign_key("fk_prompt_experiment_evaluation_set", "evaluation_set",
                                    ["evaluation_set_id"], ["id"])
        batch_op.create_index("ix_prompt_experiment_evaluation_set_id", ["evaluation_set_id"], unique=False)
    with op.batch_alter_table("prompt_experiment_item", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stratum", sa.String(length=24), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("prompt_experiment_item", schema=None) as batch_op:
        batch_op.drop_column("stratum")
    with op.batch_alter_table("prompt_experiment", schema=None) as batch_op:
        batch_op.drop_index("ix_prompt_experiment_evaluation_set_id")
        batch_op.drop_constraint("fk_prompt_experiment_evaluation_set", type_="foreignkey")
        batch_op.drop_column("evaluation_set_id")
    op.drop_index("ix_evaluation_label_regulation_id", table_name="evaluation_label")
    op.drop_index("ix_evaluation_label_set_id", table_name="evaluation_label")
    op.drop_table("evaluation_label")
    op.drop_table("evaluation_set")
