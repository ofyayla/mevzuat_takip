"""Arşiv ekranı (ilgisiz / ilk tarama kayıtları) için sıralı sayfalama dizini (yalnız PostgreSQL)

Arşiv listesi ``processing_status`` süzgeciyle ``publish_date DESC NULLS LAST, id DESC`` sırasında sayfalanır. Bu dizin
sıralamayı dizinden okutur (on binlerce kayıtta her sayfa için sıralama yapılmaz). SQLite dizinde ``NULLS LAST``
desteklemez (ve zaten NULL'ları küçük sayıp DESC'te sona koyar), bu yüzden orada dizin oluşturulmaz; model de bu dizini
tanımlamaz — ``migrations/env.py`` ``include_object`` onu otomatik karşılaştırmadan çıkarır.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.create_index('ix_regulation_status_publish', 'regulation',
                        ['processing_status', sa.text('publish_date DESC NULLS LAST'), sa.text('id DESC')])


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_index('ix_regulation_status_publish', table_name='regulation')
