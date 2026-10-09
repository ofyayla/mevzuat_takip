"""DMZ crawler ingest anahtarı: fetch_run.ext_id, raw_document.ext_id (Mongo _id)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ('fetch_run', 'raw_document'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('ext_id', sa.String(length=32), nullable=True))
            batch_op.create_unique_constraint(f'uq_{table}_ext_id', ['ext_id'])


def downgrade() -> None:
    for table in ('raw_document', 'fetch_run'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(f'uq_{table}_ext_id', type_='unique')
            batch_op.drop_column('ext_id')
