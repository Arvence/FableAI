import secrets
import socket
import subprocess
import time
from uuid import uuid4
from pathlib import Path

import httpx
import httpx2
import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError

from app import config
from app.persistence.database import Database, migration_config


@pytest.fixture(autouse=True)
def isolate_local_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "FABLEAI_DATABASE_PATH", "FABLEAI_DATABASE_URL", "FABLEAI_LOCAL_DEMO"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def block_network_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_request(*args: object, **kwargs: object) -> None:
        pytest.fail("Tests must use mocked DNS and an injected client or in-memory HTTP transport.")

    monkeypatch.setattr(socket, "getaddrinfo", reject_request)
    for module in (httpx, httpx2):
        monkeypatch.setattr(module.HTTPTransport, "handle_request", reject_request)
        monkeypatch.setattr(module.AsyncHTTPTransport, "handle_async_request", reject_request)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--postgres", action="store_true", help="Run integration tests against a disposable Docker PostgreSQL 16 database.")


@pytest.fixture(scope="session")
def postgres_server(request: pytest.FixtureRequest):
    if not request.config.getoption("--postgres"):
        pytest.skip("Use --postgres to run the isolated PostgreSQL integration tests.")
    name = "fableai-postgres-test-" + uuid4().hex
    password = secrets.token_hex(24)
    result = subprocess.run(["docker", "run", "--detach", "--rm", "--name", name, "--label", "fableai.test=postgres", "-e", "POSTGRES_PASSWORD=" + password, "-p", "127.0.0.1::5432", "postgres:16-alpine"], capture_output=True, text=True, check=True)
    container_id = result.stdout.strip()
    assert len(container_id) == 64 and all(character in "0123456789abcdef" for character in container_id)
    engine = None
    try:
        binding = subprocess.run(["docker", "port", container_id, "5432/tcp"], capture_output=True, text=True, check=True).stdout.strip()
        port = int(binding.rsplit(":", 1)[1])
        url = URL.create("postgresql+psycopg", username="postgres", password=password, host="127.0.0.1", port=port, database="postgres", query={"hostaddr": "127.0.0.1"})
        engine = create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True, hide_parameters=True, connect_args={"connect_timeout": 2})
        deadline = time.monotonic() + 30
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                break
            except OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        subprocess.run(["docker", "rm", "--force", "--volumes", container_id], capture_output=True, check=False)


@pytest.fixture
def postgres_database(postgres_server):
    name = "fableai_test_" + uuid4().hex
    with postgres_server.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    database = Database(postgres_server.url.set(database=name))
    try:
        yield database
    finally:
        database.close()
        with postgres_server.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_postgres(postgres_database):
    with postgres_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
    postgres_database.initialize()
    return postgres_database
