"""Alembic ortamı: şema app.db modellerinden, bağlantı ayarlardan (DATABASE_URL)."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.db import Base
from app.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata
# Yalnızca migrasyonda tanımlı, PostgreSQL'e özgü dizinler (modelde karşılığı yok): autogenerate "silinmeli" demesin
MIGRATION_ONLY_INDEXES = {"ix_regulation_status_publish"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "index" and reflected and name in MIGRATION_ONLY_INDEXES)


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True,
                      include_object=include_object, render_as_batch=_url().startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        # SQLite ALTER TABLE kısıtlı: batch modu tablo yeniden oluşturarak değiştirir
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                          include_object=include_object, render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
