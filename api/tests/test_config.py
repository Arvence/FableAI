import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app import config
from app.conversation_contracts import TurnPlan
from app.main import create_app


def test_loads_backend_env_with_quotes_comments_and_windows_utf8_bom(tmp_path: Path) -> None:
    database_url = "postgresql+psycopg://test:private-password@127.0.0.1:5432/fableai"
    config.ENV_FILE.write_text(f'GEMINI_API_KEY="fake-file-key" # test only\nGEMINI_MODEL=mock-model\nFABLEAI_DATABASE_URL="{database_url}"\nFABLEAI_LOCAL_DEMO=true\nUNRELATED_SETTING=ignored\n', encoding="utf-8-sig")

    settings = config.load_settings()

    assert settings.gemini_api_key.get_secret_value() == "fake-file-key"
    assert settings.gemini_model == "mock-model"
    assert settings.database_url.get_secret_value() == database_url
    assert "private-password" not in settings.model_dump_json()
    assert "fake-file-key" not in settings.model_dump_json()


@pytest.mark.parametrize("value", ["fake-process-value", ""])
def test_process_environment_overrides_file_even_when_empty(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    config.ENV_FILE.write_text("GEMINI_API_KEY=fake-file-key\nGEMINI_MODEL=mock-file-model\nFABLEAI_DATABASE_URL=postgresql+psycopg://localhost/file_db\n", encoding="utf-8")
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "FABLEAI_DATABASE_URL"):
        monkeypatch.setenv(name, value)

    settings = config.load_settings()

    assert (settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None) == (value or None)
    assert settings.gemini_model == (value or None)
    assert (settings.database_url.get_secret_value() if settings.database_url else None) == (value or None)


def test_missing_file_does_not_load_cwd_env_or_example_template(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("GEMINI_API_KEY=fake-unrelated-key\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("GEMINI_API_KEY=fake-template-key\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    settings = config.load_settings()

    assert settings.gemini_api_key is None
    assert settings.gemini_model is None


def test_file_loading_is_independent_of_working_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config.ENV_FILE.write_text("GEMINI_API_KEY=fake-backend-key\nGEMINI_MODEL=mock-model\n", encoding="utf-8")
    elsewhere = tmp_path / "different-working-directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert config.load_settings().gemini_api_key.get_secret_value() == "fake-backend-key"


def test_file_values_are_literal_and_do_not_mutate_process_environment() -> None:
    config.ENV_FILE.write_text("GEMINI_API_KEY='fake-${LITERAL}-key'\nGEMINI_MODEL=mock-model\n", encoding="utf-8")

    settings = config.load_settings()

    assert settings.gemini_api_key.get_secret_value() == "fake-${LITERAL}-key"
    assert "GEMINI_API_KEY" not in os.environ
    assert "GEMINI_MODEL" not in os.environ


def test_loading_settings_again_reads_updated_file() -> None:
    config.ENV_FILE.write_text("GEMINI_API_KEY=fake-first-key\n", encoding="utf-8")
    first = config.load_settings()
    config.ENV_FILE.write_text("GEMINI_API_KEY=fake-second-key\n", encoding="utf-8")

    assert first.gemini_api_key.get_secret_value() == "fake-first-key"
    assert config.load_settings().gemini_api_key.get_secret_value() == "fake-second-key"


def test_application_startup_passes_file_configuration_to_mocked_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.persistence.database import Database

    database_path = tmp_path / "startup.sqlite3"
    database_url = "postgresql+psycopg://test:private-password@localhost/test"
    database_factory = Mock(side_effect=lambda _: Database(database_path))
    monkeypatch.setattr("app.main.Database", database_factory)
    config.ENV_FILE.write_text(f'GEMINI_API_KEY=fake-startup-key\nGEMINI_MODEL=mock-startup-model\nFABLEAI_DATABASE_URL="{database_url}"\nFABLEAI_LOCAL_DEMO=true\n', encoding="utf-8")
    provider = Mock()
    provider.generate = AsyncMock(return_value=TurnPlan(action="message"))
    factory = Mock(return_value=provider)
    monkeypatch.setattr("app.main.GeminiProvider", factory)

    with TestClient(create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        conversation_id = client.post("/api/v1/conversations").json()["conversation_id"]
        response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"instruction": "Save this fictional drafting preference."})
        assert response.status_code == 200
        assert response.json()["kind"] == "message"
        assert "fake-startup-key" not in response.text
        assert client.app.state.repository.database.path == database_path

    database_factory.assert_called_once_with(database_url)
    factory.assert_called_once()
    settings = factory.call_args.args[0]
    assert settings.gemini_api_key.get_secret_value() == "fake-startup-key"
    assert settings.gemini_model == "mock-startup-model"
    provider.generate.assert_awaited_once()


def test_legacy_sqlite_environment_does_not_silently_select_or_modify_old_data(tmp_path: Path) -> None:
    legacy = tmp_path / "old.sqlite3"
    legacy.write_bytes(b"preserve this legacy file")
    config.ENV_FILE.write_text(f"FABLEAI_DATABASE_PATH={legacy.as_posix()}\n", encoding="utf-8")
    settings = config.load_settings()
    assert settings.database_url is None and settings.database_path is None
    assert legacy.read_bytes() == b"preserve this legacy file"


def test_local_demo_is_disabled_by_default_and_requires_explicit_valid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.load_settings().local_demo is False
    config.ENV_FILE.write_text("FABLEAI_LOCAL_DEMO=true\n", encoding="utf-8")
    assert config.load_settings().local_demo is True
    monkeypatch.setenv("FABLEAI_LOCAL_DEMO", "false")
    assert config.load_settings().local_demo is False
    monkeypatch.setenv("FABLEAI_LOCAL_DEMO", "not-a-boolean")
    with pytest.raises(ValueError, match="FABLEAI_LOCAL_DEMO"):
        config.load_settings()
