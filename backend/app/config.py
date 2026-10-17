from os import getenv

from pydantic import BaseModel, SecretStr


class Settings(BaseModel):
    app_name: str = "FableAI"
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None


def load_settings() -> Settings:
    return Settings(gemini_api_key=getenv("GEMINI_API_KEY") or None, gemini_model=getenv("GEMINI_MODEL") or None)
