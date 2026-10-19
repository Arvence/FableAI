# FableAI

FableAI is a local conversational app for creating explicitly fictional news and announcement drafts from authorized examples. The backend provides a FastAPI health endpoint, validated chat/data contracts, and a mockable Gemini provider.

## Stack

- Python 3.12, FastAPI, Pydantic, and Uvicorn.
- Google Gen AI Python SDK (`google-genai`) for structured Gemini responses.
- pytest and HTTPX2 for local tests.
- Planned for later TODOs: SQLite/SQLAlchemy and React/TypeScript with Vite.

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

No credentials or environment file are required for the health endpoint. `backend/.env.example` records `GEMINI_MODEL=gemini-3.8-flash` and leaves `GEMINI_API_KEY` empty. Configure the real API key locally through environment variables only when API testing is required. Never put a real key in `.env.example`.

The configuration module reads process environment variables. It does not automatically load `.env` files. Local `.env` files are ignored, while `.env.example` remains tracked. Keep credentials out of source control.

Set the model in the terminal that will run the application:

```powershell
$env:GEMINI_MODEL = "gemini-3.8-flash"
```

On macOS/Linux:

```sh
export GEMINI_MODEL=gemini-3.8-flash
```

Use a model you have verified for your project's [Free Tier access](https://ai.google.dev/gemini-api/docs/pricing), with no Cloud Billing account attached, and stay within its current [AI Studio quotas](https://ai.google.dev/gemini-api/docs/rate-limits). Use only non-sensitive, appropriately authorized samples under the [Unpaid Services terms](https://ai.google.dev/gemini-api/terms).

The provider uses the configured model without a fallback and accepts a constructed prompt and a Pydantic response schema. Each invocation allows one request with a 30-second default deadline, capped at 60 seconds through backend settings. Automatic retries, continuation, and tool calls are disabled. Responses must pass Pydantic validation, and provider errors expose safe messages. The health endpoint does not invoke the provider; chat endpoints are not implemented yet.

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

Tests cover the health endpoint, chat/data contracts, and Gemini provider. Provider tests use fake credentials and in-memory HTTP responses, and real HTTP transports are blocked during tests. No test calls Gemini or consumes API quota.
