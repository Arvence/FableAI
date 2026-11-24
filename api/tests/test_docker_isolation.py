import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_docker import test_isolated_compose_startup_migrates_and_serves_local_health as run_docker_check


@pytest.mark.parametrize("failure", [None, "up", "exec", "timeout"])
def test_docker_check_isolates_credentials_resources_and_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-owner-key-must-not-be-inherited")
    monkeypatch.setenv("GEMINI_MODEL", "fake-owner-model-must-not-be-inherited")
    monkeypatch.setenv("POSTGRES_PASSWORD", "fake-owner-password-must-not-be-inherited")
    calls = []

    def run(arguments, **options):
        calls.append((arguments, options))
        start = arguments.index("-f", arguments.index("-f") + 1) + 2
        operation = arguments[start]
        if operation == "config":
            data = {"services": {"api": {"environment": {"GEMINI_API_KEY": "", "GEMINI_MODEL": ""}}, "postgres": {}}, "networks": {"default": {"internal": True}}}
            return subprocess.CompletedProcess(arguments, 0, json.dumps(data), "")
        if operation == "up" and failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, 1)
        return subprocess.CompletedProcess(arguments, 1 if operation == failure else 0, "", "withheld fake failure")

    monkeypatch.setattr(subprocess, "run", run)
    request = Mock()
    request.config.getoption.return_value = True
    if failure == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            run_docker_check(request, tmp_path)
    elif failure:
        with pytest.raises(pytest.fail.Exception, match="Isolated Docker check failed"):
            run_docker_check(request, tmp_path)
    else:
        run_docker_check(request, tmp_path)

    projects = {arguments[arguments.index("--project-name") + 1] for arguments, _ in calls}
    assert len(projects) == 1 and next(iter(projects)).startswith("fableai-ci-")
    for arguments, options in calls:
        environment = options["env"]
        assert environment["GEMINI_API_KEY"] == environment["GEMINI_MODEL"] == environment["GOOGLE_API_KEY"] == ""
        assert environment["POSTGRES_PASSWORD"] != "fake-owner-password-must-not-be-inherited"
        assert Path(arguments[arguments.index("--env-file") + 1]).read_text() == ""
    assert calls[-1][0][-5:] == ["down", "--volumes", "--remove-orphans", "--rmi", "local"]
    override = (tmp_path / "compose.test.yml").read_text()
    assert override.count("ports: !reset []") == 2
    assert "internal: true" in override
