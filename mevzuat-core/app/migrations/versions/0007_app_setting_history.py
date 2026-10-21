"""Ayar değişikliği geçmişi: app_setting_history (Yönetim → YZ ayarları)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

_INDEXED = ('key', 'changed_at')


def upgrade() -> None:
    op.create_table('app_setting_history',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('old_value', sa.String(length=200), nullable=True),
    sa.Column('new_value', sa.String(length=200), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('changed_by', sa.String(length=100), nullable=True),
    sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('app_setting_history', schema=None) as batch_op:
        for col in _INDEXED:
            batch_op.create_index(batch_op.f(f'ix_app_setting_history_{col}'), [col], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('app_setting_history', schema=None) as batch_op:
        for col in reversed(_INDEXED):
            batch_op.drop_index(batch_op.f(f'ix_app_setting_history_{col}'))
    op.drop_table('app_setting_history')
