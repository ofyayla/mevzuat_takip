"""Arka plan işleri: job_run (yönetim sayfası "Taramalar ve işler")

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

_INDEXED = ('kind', 'status', 'source_code', 'created_at')


def upgrade() -> None:
    op.create_table('job_run',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('via', sa.String(length=16), nullable=True),
    sa.Column('source_code', sa.String(length=32), nullable=True),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('requested_by', sa.String(length=200), nullable=True),
    sa.Column('worker', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        for col in _INDEXED:
            batch_op.create_index(batch_op.f(f'ix_job_run_{col}'), [col], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        for col in reversed(_INDEXED):
            batch_op.drop_index(batch_op.f(f'ix_job_run_{col}'))
    op.drop_table('job_run')
