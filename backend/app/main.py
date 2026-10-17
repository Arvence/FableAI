from fastapi import FastAPI

from app.config import load_settings


app = FastAPI(title=load_settings().app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
