import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_isolated_compose_startup_migrates_and_serves_local_health(request: pytest.FixtureRequest, tmp_path: Path) -> None:
    if not request.config.getoption("--docker"):
        pytest.skip("Use --docker to build and start an isolated Compose test stack.")
    project = "fableai-ci-" + uuid4().hex
    password = secrets.token_hex(24) + "@:/%"
    environment = {**os.environ, "POSTGRES_USER": "fableai", "POSTGRES_DB": "fableai", "POSTGRES_PASSWORD": password, "FABLEAI_DATABASE_URL": "", "GEMINI_API_KEY": "", "GEMINI_MODEL": "", "GOOGLE_API_KEY": "", "FABLEAI_LOCAL_DEVELOPMENT_KEY": ""}
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")
    override = tmp_path / "compose.test.yml"
    override.write_text('services:\n  api:\n    ports: !reset []\n  postgres:\n    ports: !reset []\nnetworks:\n  default:\n    internal: true\n', encoding="utf-8")
    command = ["docker", "compose", "--env-file", str(empty_env), "--project-name", project, "-f", str(ROOT / "docker-compose.yml"), "-f", str(override)]

    def compose(*arguments: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
        result = subprocess.run([*command, *arguments], cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if check and result.returncode:
            pytest.fail("Isolated Docker check failed at " + arguments[0] + ". Container output was withheld to avoid exposing configuration.")
        return result

    try:
        configuration = json.loads(compose("config", "--format", "json").stdout)
        assert not configuration["services"]["api"].get("ports")
        assert not configuration["services"]["postgres"].get("ports")
        assert configuration["networks"]["default"]["internal"] is True
        assert configuration["services"]["api"]["environment"]["GEMINI_API_KEY"] == ""
        assert configuration["services"]["api"]["environment"]["GEMINI_MODEL"] == ""
        compose("up", "--build", "--detach", "--wait", "--wait-timeout", "120", timeout=600)
        probe = """
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from app.config import load_settings
from app.persistence.database import Database, migration_config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
settings = load_settings()
assert settings.gemini_api_key is None and settings.gemini_model is None
assert settings.local_demo is False and settings.container_mode is True
with urlopen('http://127.0.0.1:8000/health', timeout=5) as response:
    assert response.status == 200 and json.load(response) == {'status': 'ok'}
try:
    urlopen('http://127.0.0.1:8000/api/v1/projects', timeout=5)
except HTTPError as response:
    assert response.code == 401
    assert json.load(response)['error']['code'] == 'unauthorized'
else:
    raise AssertionError('The project API must reject unauthenticated requests.')
from sqlalchemy.engine import URL
url = settings.database_url.get_secret_value() if settings.database_url else URL.create("postgresql+psycopg", username=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], host="postgres", port=5432, database=os.environ["POSTGRES_DB"])
with urlopen(Request('http://api:8000/health', headers={'Host': '127.0.0.1:8000'}), timeout=5) as response:
    assert response.status == 200
try:
    urlopen(Request('http://api:8000/api/v1/projects', headers={'Host': '127.0.0.1:8000'}), timeout=5)
except HTTPError as response:
    assert response.code == 401
else:
    raise AssertionError('Bridge peers must still authenticate.')
database = Database(url)
try:
    database.initialize()
    with database.engine.connect() as connection:
        assert set(MigrationContext.configure(connection).get_current_heads()) == set(ScriptDirectory.from_config(migration_config()).get_heads())
finally:
    database.close()
"""
        compose("exec", "-T", "api", "python", "-c", probe)
        packaging = "from pathlib import Path; p = Path('/app/api'); assert not (p / '.env').exists(); assert not (p / 'tests').exists(); assert not list(p.rglob('*.sqlite3')); assert not list(p.rglob('*.db'))"
        compose("exec", "-T", "api", "python", "-c", packaging)
        compose("restart", "api")
        compose("up", "--detach", "--wait", "--wait-timeout", "120", timeout=150)
        compose("exec", "-T", "api", "python", "-c", probe)
    finally:
        cleanup = compose("down", "--volumes", "--remove-orphans", "--rmi", "local", check=False, timeout=90)
        if cleanup.returncode:
            if sys.exc_info()[0] is None:
                pytest.fail("Could not remove the isolated Compose test stack.")
            else:
                print("Could not remove isolated Compose project " + project, file=sys.stderr)
