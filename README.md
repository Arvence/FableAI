# FableAI

FableAI is a local conversational app for creating explicitly fictional news and announcement drafts from authorized examples. This initial setup provides a FastAPI health endpoint.

## Stack

- Python 3.12, FastAPI, Pydantic, and Uvicorn.
- pytest and HTTPX2 for local tests.
- Planned for later TODOs: Gemini, SQLite/SQLAlchemy, and React/TypeScript with Vite.

## Local installation

Install Python 3.12 with pip and ensure `python --version` resolves to it on Windows, or `python3.12 --version` on macOS/Linux. Dependencies are managed with pip and `backend/requirements.txt`.

From the repository root, on Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On macOS/Linux:

```sh
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The following commands run from `backend/` and do not require virtual environment activation.

## Environment configuration

No credentials or environment file are required for this setup. `backend/.env.example` contains empty `GEMINI_API_KEY` and `GEMINI_MODEL` placeholders for later TODOs; leave them empty for now.

The configuration module reads process environment variables. It does not automatically load `.env` files. Local `.env` files are ignored, while `.env.example` remains tracked. Keep credentials out of source control. Gemini Free Tier verification belongs to TODO 01.

## Development server

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

macOS/Linux:

```sh
.venv/bin/python -m uvicorn app.main:app --reload
```

Uvicorn defaults to loopback at `http://127.0.0.1:8000`. `GET /health` returns `{"status":"ok"}`. Stop the server with `Ctrl+C`.

## Tests

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

macOS/Linux:

```sh
.venv/bin/python -m pytest -q
```

The startup test removes Gemini environment variables and checks the health response in process, without internet access or LLM calls.
