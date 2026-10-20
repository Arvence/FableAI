from pathlib import Path

from sqlalchemy import Connection, create_engine, event
from sqlalchemy.engine import URL

from app.storage_models import Base


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.engine = create_engine(URL.create("sqlite+pysqlite", database=str(self.path)), connect_args={"check_same_thread": False, "timeout": 5}, hide_parameters=True)
        event.listen(self.engine, "connect", self._connect)
        event.listen(self.engine, "begin", self._begin)

    @staticmethod
    def _connect(connection: object, record: object) -> None:
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @staticmethod
    def _begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE" if connection.get_execution_options().get("write_transaction") else "BEGIN")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()
