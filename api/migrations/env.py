from alembic import context

from app.config import load_settings
from app.persistence.database import Database
from app.persistence.storage_models import Base


def run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.config.attributes.get("connection") is not None:
    run_migrations(context.config.attributes["connection"])
else:
    settings = load_settings()
    database = Database(settings.database_url.get_secret_value() if settings.database_url else None)
    try:
        if context.is_offline_mode():
            context.configure(url=database.url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
            with context.begin_transaction():
                context.run_migrations()
        else:
            with database.engine.connect() as connection:
                run_migrations(connection)
    finally:
        database.close()
