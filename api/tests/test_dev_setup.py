import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from dotenv import dotenv_values


SPEC = importlib.util.spec_from_file_location("setup_env", Path(__file__).resolve().parents[2] / "scripts" / "setup_env.py")
setup_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_env)


@pytest.fixture
def environment_files(tmp_path: Path) -> tuple[Path, Path]:
    template = tmp_path / ".env.example"
    template.write_text("# Backend only.\nGEMINI_API_KEY=\nGEMINI_MODEL=\nFABLEAI_DATABASE_PATH=\n", encoding="utf-8", newline="\n")
    return tmp_path / ".env", template


def test_first_noninteractive_run_creates_only_blank_template(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    env_path, template = environment_files
    prompt = Mock(side_effect=AssertionError("Must not prompt without a terminal."))
    monkeypatch.setattr(setup_env.getpass, "getpass", prompt)
    monkeypatch.setattr("builtins.input", prompt)

    setup_env.configure_env(env_path, template, interactive=False)

    assert env_path.read_bytes() == template.read_bytes()
    assert "Generation is not configured" in capsys.readouterr().out
    prompt.assert_not_called()


def test_completed_setup_preserves_existing_file_exactly(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    env_path, template = environment_files
    original = b"\xef\xbb\xbf# Preserve this comment\r\nGEMINI_API_KEY='fake-existing-key'\r\nGEMINI_MODEL=mock-existing\r\nFABLEAI_DATABASE_PATH=custom.sqlite3\r\n"
    env_path.write_bytes(original)
    prompt = Mock(side_effect=AssertionError("Must not ask again."))
    monkeypatch.setattr(setup_env.getpass, "getpass", prompt)
    monkeypatch.setattr("builtins.input", prompt)

    setup_env.configure_env(env_path, template, interactive=True)

    assert env_path.read_bytes() == original
    prompt.assert_not_called()


def test_missing_settings_use_hidden_key_input_and_roundtrip_literal_values(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    env_path, template = environment_files
    key = "fake-" + "$" + "{LITERAL}-'quoted'\\key"
    hidden_prompt = Mock(return_value=key)
    monkeypatch.setattr(setup_env.getpass, "getpass", hidden_prompt)
    monkeypatch.setattr("builtins.input", lambda _: "mock-model")

    setup_env.configure_env(env_path, template, interactive=True)

    values = dotenv_values(env_path, encoding="utf-8-sig", interpolate=False)
    assert values["GEMINI_API_KEY"] == key
    assert values["GEMINI_MODEL"] == "mock-model"
    assert values["FABLEAI_DATABASE_PATH"] == ""
    assert key not in capsys.readouterr().out
    hidden_prompt.assert_called_once_with("GEMINI_API_KEY (hidden): ")


def test_only_missing_model_is_changed(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    env_path, template = environment_files
    env_path.write_text("# Keep this comment\nGEMINI_API_KEY=fake-existing\nGEMINI_MODEL=\nFABLEAI_DATABASE_PATH='custom path/chat.sqlite3'\n", encoding="utf-8")
    monkeypatch.setattr(setup_env.getpass, "getpass", Mock(side_effect=AssertionError("Key already exists.")))
    monkeypatch.setattr("builtins.input", lambda _: "mock-model")

    setup_env.configure_env(env_path, template, interactive=True)

    values = dotenv_values(env_path, encoding="utf-8-sig", interpolate=False)
    assert values["GEMINI_API_KEY"] == "fake-existing"
    assert values["GEMINI_MODEL"] == "mock-model"
    assert values["FABLEAI_DATABASE_PATH"] == "custom path/chat.sqlite3"
    assert "# Keep this comment" in env_path.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("inherited", ["", "fake-process-value"])
def test_process_variables_are_not_saved_or_overwritten(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, inherited: str) -> None:
    env_path, template = environment_files
    env_path.write_bytes(template.read_bytes())
    for name in setup_env.SETTINGS:
        monkeypatch.setenv(name, inherited)
    prompt = Mock(side_effect=AssertionError("Process variables take precedence."))
    monkeypatch.setattr(setup_env.getpass, "getpass", prompt)
    monkeypatch.setattr("builtins.input", prompt)

    setup_env.configure_env(env_path, template, interactive=True)

    assert env_path.read_bytes() == template.read_bytes()
    prompt.assert_not_called()


def test_skipping_settings_keeps_generation_unconfigured(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    env_path, template = environment_files
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda _: "")
    monkeypatch.setattr("builtins.input", lambda _: "")

    setup_env.configure_env(env_path, template, interactive=True)

    assert env_path.read_bytes() == template.read_bytes()


def test_secure_input_failure_never_falls_back_to_visible_input(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    env_path, template = environment_files
    monkeypatch.setattr(setup_env.getpass, "getpass", Mock(side_effect=setup_env.getpass.GetPassWarning("No secure input.")))
    visible_prompt = Mock(side_effect=AssertionError("Do not use visible input for the key."))
    monkeypatch.setattr("builtins.input", visible_prompt)

    setup_env.configure_env(env_path, template, interactive=True)

    assert env_path.read_bytes() == template.read_bytes()
    visible_prompt.assert_not_called()


def test_multiline_values_are_not_written(environment_files: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    env_path, template = environment_files
    monkeypatch.setattr(setup_env.getpass, "getpass", lambda _: "fake-key\nINJECTED=value")
    monkeypatch.setattr("builtins.input", lambda _: "mock-model")

    setup_env.configure_env(env_path, template, interactive=True)

    values = dotenv_values(env_path, encoding="utf-8-sig", interpolate=False)
    assert values["GEMINI_API_KEY"] == ""
    assert "INJECTED" not in values
    assert values["GEMINI_MODEL"] == "mock-model"
