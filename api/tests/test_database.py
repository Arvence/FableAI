import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.persistence.database import Database
from app.persistence.storage_models import Base


def test_database_requires_an_explicit_postgresql_connection() -> None:
    with pytest.raises(RuntimeError, match="FABLEAI_DATABASE_URL"):
        Database(None)


@pytest.mark.parametrize("url", ["private-password", "sqlite:///private-password.db", "mysql://user:private-password@localhost/db", "postgresql://localhost"])
def test_invalid_service_database_urls_do_not_disclose_credentials(url: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL URL") as error:
        Database(url)
    assert "private-password" not in str(error.value)


def test_postgres_urls_use_psycopg_and_preserve_encoded_credentials() -> None:
    database = Database("postgresql://test:private%40pass%25word@localhost/fableai")
    try:
        assert database.url.drivername == "postgresql+psycopg"
        assert database.url.password == "private@pass%word"
        assert database.engine.hide_parameters
    finally:
        database.close()


def test_temporary_sqlite_databases_use_the_same_versioned_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Schema initialization must use migrations, not create_all().")

    monkeypatch.setattr(Base.metadata, "create_all", forbidden)
    database = Database(tmp_path / "isolated.sqlite3")
    try:
        database.initialize()
        database.initialize()
        with database.engine.connect() as connection:
            context = MigrationContext.configure(connection)
            assert context.get_current_revision() == "0002_projects"
            assert compare_metadata(context, Base.metadata) == []
        assert set(inspect(database.engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    finally:
        database.close()


def test_legacy_sqlite_is_rejected_without_changing_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE preserved (content TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('keep this history')")
    before = path.read_bytes()
    database = Database(path)
    try:
        with pytest.raises(RuntimeError, match="Legacy SQLite"):
            database.initialize()
        assert path.read_bytes() == before
        assert not Path(str(path) + "-wal").exists()
    finally:
        database.close()
