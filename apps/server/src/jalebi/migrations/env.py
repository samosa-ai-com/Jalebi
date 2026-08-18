from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from jalebi.config import load_config
from jalebi.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_PLACEHOLDER_URL = "driver://user:pass@localhost/dbname"


def _db_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url or url == _PLACEHOLDER_URL:
        return load_config().db_url
    return url


config.set_main_option("sqlalchemy.url", _db_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL without a live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Jalebi's global SQLAlchemy "connect" listener (db.py) forces
    # PRAGMA foreign_keys=ON on every SQLite engine. Alembic's batch
    # mode must drop/recreate `tasks` when widening ck_tasks_status, which
    # SQLite forbids while foreign keys are enforced. Register an
    # engine-instance listener that turns foreign keys OFF *after* the
    # global one runs, so alembic's batch DDL works. This is the standard
    # alembic-on-SQLite pattern and only affects the migration connection.
    @event.listens_for(connectable, "connect")
    def _disable_fk_for_migrations(dbapi_connection, _connection_record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
