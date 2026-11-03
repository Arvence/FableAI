import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, event, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def migration_config(connection: Connection | None = None) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


class Database:
    def __init__(self, location: str | URL | Path | None) -> None:
        self.path = location.resolve() if isinstance(location, Path) else None
        if self.path is not None:
            self.url = URL.create("sqlite+pysqlite", database=str(self.path))
            self.engine = create_engine(self.url, connect_args={"check_same_thread": False, "timeout": 5}, hide_parameters=True)
            event.listen(self.engine, "connect", self._connect_sqlite)
            event.listen(self.engine, "begin", self._begin_sqlite)
            return
        if not location:
            raise RuntimeError("Set FABLEAI_DATABASE_URL in api/.env to a PostgreSQL connection URL, then run npm run db:migrate. Existing SQLite data is not transferred automatically.")
        try:
            url = make_url(location)
            if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"} or not url.database:
                raise ValueError
        except (ArgumentError, TypeError, ValueError):
            raise ValueError("FABLEAI_DATABASE_URL must be a PostgreSQL URL with a database name.") from None
        self.url = url.set(drivername="postgresql+psycopg")
        self.engine = create_engine(self.url, pool_pre_ping=True, connect_args={"connect_timeout": 5}, hide_parameters=True)

    @staticmethod
    def _connect_sqlite(connection: object, record: object) -> None:
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @staticmethod
    def _begin_sqlite(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE" if connection.get_execution_options().get("write_transaction") else "BEGIN")

    def initialize(self) -> None:
        if self.path is not None:
            if self.path.exists() and self.path.stat().st_size:
                with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as existing:
                    tables = {row[0] for row in existing.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and "alembic_version" not in tables:
                    raise RuntimeError("Legacy SQLite data was left unchanged. Automatic transfer is not supported; configure PostgreSQL and run npm run db:migrate.")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.engine.begin() as connection:
            config = migration_config(connection)
            heads = set(MigrationContext.configure(connection).get_current_heads())
            expected = set(ScriptDirectory.from_config(config).get_heads())
            if heads == expected:
                return
            if self.path is not None and not inspect(connection).get_table_names():
                command.upgrade(config, "head")
                return
            raise RuntimeError("Database migrations are required. Run npm run db:migrate before starting the API.")

    def close(self) -> None:
        self.engine.dispose()
