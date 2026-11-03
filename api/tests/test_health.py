from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch


def test_health_starts_without_credentials(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    from app.config import Settings
    from app.main import create_app

    with TestClient(create_app(Settings(database_path=tmp_path / "health.sqlite3")), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}
