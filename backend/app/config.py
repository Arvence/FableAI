from os import getenv
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseModel):
    app_name: str = "FableAI"
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    gemini_timeout_seconds: float = Field(default=60.0, gt=0, le=60, strict=True)
    database_path: Path = Path(__file__).resolve().parents[1] / "data" / "fableai.sqlite3"


def load_settings() -> Settings:
    file_values = dotenv_values(ENV_FILE, encoding="utf-8-sig", interpolate=False)
    values = {"gemini_api_key": getenv("GEMINI_API_KEY", file_values.get("GEMINI_API_KEY")) or None, "gemini_model": getenv("GEMINI_MODEL", file_values.get("GEMINI_MODEL")) or None}
    if path := getenv("FABLEAI_DATABASE_PATH", file_values.get("FABLEAI_DATABASE_PATH")):
        values["database_path"] = path
    return Settings(**values)
