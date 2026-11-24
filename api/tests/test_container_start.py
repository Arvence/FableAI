import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.engine import make_url


SPEC = importlib.util.spec_from_file_location("container_start", Path(__file__).resolve().parents[2] / "scripts/container_start.py")
container_start = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(container_start)


@pytest.mark.parametrize("explicit", [False, True])
def test_container_start_encodes_credentials_and_migrates_before_serving(monkeypatch: pytest.MonkeyPatch, explicit: bool) -> None:
    monkeypatch.setenv("POSTGRES_USER", "local:user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:/word%$'\"")
    monkeypatch.setenv("POSTGRES_DB", "fictional archive")
    monkeypatch.setenv("FABLEAI_DATABASE_URL", "postgresql+psycopg://custom:custom@127.0.0.1/custom" if explicit else "")
    events = []
    migrate = Mock(side_effect=lambda *args, **kwargs: (events.append("migrate"), SimpleNamespace(returncode=0))[1])
    execute = Mock(side_effect=lambda *args: events.append("serve"))
    monkeypatch.setattr(container_start.subprocess, "run", migrate)
    monkeypatch.setattr(container_start.os, "execve", execute)
    assert container_start.main() == 0
    assert events == ["migrate", "serve"]
    environment = migrate.call_args.kwargs["env"]
    assert execute.call_args.args[2] == environment
    url = make_url(environment["FABLEAI_DATABASE_URL"])
    if explicit:
        assert url.host == "127.0.0.1" and url.database == "custom"
    else:
        assert url.username == "local:user"
        assert url.password == "p@ss:/word%$'\""
        assert url.database == "fictional archive" and url.host == "postgres"
    assert migrate.call_args.args[0][-2:] == ["upgrade", "head"]
    assert execute.call_args.args[1][-4:] == ["--host", "0.0.0.0", "--port", "8000"]


def test_container_start_does_not_serve_after_a_migration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABLEAI_DATABASE_URL", "postgresql+psycopg://localhost/test")
    monkeypatch.setattr(container_start.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=7)))
    serve = Mock()
    monkeypatch.setattr(container_start.os, "execve", serve)
    assert container_start.main() == 7
    serve.assert_not_called()
