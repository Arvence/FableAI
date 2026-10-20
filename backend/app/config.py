from os import getenv
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    app_name: str = "FableAI"
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    gemini_timeout_seconds: float = Field(default=30.0, gt=0, le=60, strict=True)
    database_path: Path = Path(__file__).resolve().parents[1] / "data" / "fableai.sqlite3"


def load_settings() -> Settings:
    values = {"gemini_api_key": getenv("GEMINI_API_KEY") or None, "gemini_model": getenv("GEMINI_MODEL") or None}
    if path := getenv("FABLEAI_DATABASE_PATH"):
        values["database_path"] = path
    return Settings(**values)
