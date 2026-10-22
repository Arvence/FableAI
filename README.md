# FableAI

FableAI is a local chat application for creating explicitly fictional news and announcement drafts from authorized samples, headlines, and optional reference URLs. It returns validated JSON, remembers conversation context, and supports revisions to selected entries with stable IDs and version history.

Generated content is for creative drafting, demos, and testing. Every draft carries a fictional title marker and a synthetic-content label. Accepting a draft records a local review decision; it does not publish anything or verify real events.

## Stack and layout

- Python 3.12, FastAPI, Pydantic, Uvicorn, and the Google Gen AI Python SDK.
- SQLite with SQLAlchemy for conversations, references, messages, reviews, and entry versions.
- React, TypeScript, and Vite for the browser interface.
- pytest with mocked HTTP transports and Playwright for browser tests.

Application code and backend tests live in `backend/app/` and `backend/tests/`. The browser app and its tests live in `frontend/src/` and `frontend/tests/`. No separate database server is required.

## Install and start locally

Install Python 3.12 and Node.js 22.12 or newer with npm. On Windows, check that `python --version` selects Python 3.12; on macOS/Linux use `python3.12`.

From the repository root, install the backend and create its environment file only if one does not already exist.

Windows PowerShell:

~~~powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
~~~

macOS/Linux:

~~~sh
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
~~~

The app can start, list saved conversations, and serve its health endpoint without Gemini credentials. To submit model-backed chat turns, first complete the account checks below and fill in `backend/.env` yourself.

Start the backend from `backend/`:

~~~powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
~~~

On macOS/Linux:

~~~sh
.venv/bin/python -m uvicorn app.main:app --reload
~~~

In a second terminal, starting at the repository root:

~~~powershell
cd frontend
npm.cmd ci
npm.cmd run dev
~~~

On macOS/Linux, use `npm ci` and `npm run dev` instead. Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Vite forwards `/api` requests to the backend at `127.0.0.1:8000`. Both servers bind to loopback; port 5173 must be available.

[GET /health](http://127.0.0.1:8000/health) returns `{"status":"ok"}` without a model call. API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs), with the machine-readable contract at `/openapi.json`. Stop each server with `Ctrl+C`.

For a local build preview, run `npm run build` followed by `npm run preview` from `frontend/`, with the backend running and the frontend development server stopped. Use `npm.cmd` in PowerShell. Preview uses the same loopback address, port, and API proxy.

## Backend configuration and Free Tier checks

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Your backend-only API key from your own verified Google AI Studio project. |
| `GEMINI_MODEL` | The exact model ID you have verified for your project's available Free Tier and structured JSON support. No default model is selected. |
| `FABLEAI_DATABASE_PATH` | Optional SQLite file path; an empty value uses `backend/data/fableai.sqlite3`. Prefer an absolute path when overriding it. |

Before filling in the key and model, verify these in your own Google AI Studio project:

1. The project is on Free Tier with no Cloud Billing account attached. Follow the project's zero-billing requirement; do not enable paid service to run FableAI. Check Google's [billing guidance](https://ai.google.dev/gemini-api/docs/billing).
2. The selected model has free text input/output available to your account and supports structured JSON. Check current [pricing](https://ai.google.dev/gemini-api/docs/pricing) and [structured-output support](https://ai.google.dev/gemini-api/docs/structured-output).
3. The model is available in your account/region and has sufficient current RPM, TPM, and RPD limits. Check your project's active limits in AI Studio and Google's [rate-limit guidance](https://ai.google.dev/gemini-api/docs/rate-limits).

Availability, pricing, and limits can change. FableAI does not inspect or enforce your account's billing status and cannot guarantee zero cost for a paid project. Leave generation unconfigured if these checks cannot be completed.

The backend reads `backend/.env` at startup, independently of the working directory. Process environment variables take precedence, including explicitly empty values. Values are read literally without interpolation. `.env.example` is a template only. Restart the backend after changing configuration. Keep keys out of frontend files and all `VITE_*` variables.

Each provider invocation makes one request with a 30-second default timeout. Interpreting a turn uses a model request; generating or revising drafts can use a second request. There are no automatic retries, model switches, tool calls, or paid fallbacks. A quota error tells you to wait. Starting the app, loading history, and accepting/rejecting an entry do not call Gemini.

Google's Unpaid Services terms permit service improvement use of prompts and responses and human review, with regional exceptions. Read the applicable [data-use terms](https://ai.google.dev/gemini-api/terms). Use only non-confidential, self-authored, public-domain, or appropriately licensed/authorized material. Do not submit personal information, credentials, or private documents.

## References and a sample conversation

Create a chat, open **Add samples, headlines, or URLs**, and paste or attach authorized plain-text examples. Headlines and HTTPS links have separate inputs. Saved references are reused in later turns.

For example:

1. Add a self-authored sample: "In the fictional town of Bramblewick, glass flowers brighten a quiet workshop. Guests choose colored petals for a pretend garden display."
2. Send: "Remember these preferences: English announcements about an imaginary paper-bird pavilion, neutral tone, one draft per request, and 100 to 240 characters per body."
3. Send: "Generate one fictional draft." Answer any clarification about missing constraints.
4. Inspect the draft and **View response JSON**. You can copy the exact response or mark the entry **Accept** or **Reject**.
5. Select **Revise entry 1** and send: "Make entry 1 shorter." Its ID stays the same, its version increases, and its review status returns to `draft`. Unselected entries and their reviews remain unchanged.
6. Reload or restart the backend and reopen the conversation to continue with its saved history.

These are illustrative generation and revision responses, grouped in one array for comparison. Each HTTP submission returns one response object, and a revision response contains only the revised entries.

~~~json
[
  {
    "kind": "generation",
    "conversation_id": "d438eb5c-c245-4a31-9741-e45be9761234",
    "message_id": "e42ab632-b38a-4d59-b0fe-d8d57fcf1234",
    "assistant_message": "Created 1 fictional draft for review.",
    "entries": [{
      "id": "f752a0b6-920c-4f66-8be1-807ca2401234",
      "version": 1,
      "title": "[FICTIONAL] Paper Birds Gather at the Glass Pavilion",
      "body": "Across the imaginary village of Mirrvale, paper birds gather around a glass pavilion. Visitors arrange painted feathers into a different pattern each evening.",
      "category": "announcement",
      "tags": ["fiction"],
      "language": "en",
      "is_synthetic": true,
      "status": "draft"
    }]
  },
  {
    "kind": "revision",
    "conversation_id": "d438eb5c-c245-4a31-9741-e45be9761234",
    "message_id": "a914de87-9717-421d-9d63-0ad3819b1234",
    "assistant_message": "Revised 1 selected fictional draft. Other entries are unchanged.",
    "entries": [{
      "id": "f752a0b6-920c-4f66-8be1-807ca2401234",
      "version": 2,
      "title": "[FICTIONAL] Paper Birds Gather at the Glass Pavilion",
      "body": "In the invented village of Mirrvale, paper birds circle a glass pavilion while visitors arrange bright feather patterns.",
      "category": "announcement",
      "tags": ["fiction"],
      "language": "en",
      "is_synthetic": true,
      "status": "draft"
    }]
  }
]
~~~

A pasted URL remains **Link only** until you authorize a separate import. Reachability does not establish permission. Record the permission basis and evidence for the exact URL, confirm automated access, and separately confirm permitted model use and non-confidential content. Redirects also require permission for their destinations. Blocked imports explain the failure and suggest pasting authorized excerpts.

Imports allow bounded public HTTPS HTML only. Private/metadata addresses, unsafe DNS answers and redirects, oversized pages, unsupported encodings, and non-HTML responses are rejected. No crawler, paywall bypass, or automatic import runs. Reference text is reduced to local pattern summaries for prompts; user instructions and bounded conversation/draft context are still sent to Gemini.

## HTTP API

Chat routes use `/api/v1/conversations`. Conversation and entry IDs are UUIDs.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check local service availability. |
| `POST` | `/api/v1/conversations` | Create a conversation. |
| `GET` | `/api/v1/conversations` | List conversations. |
| `GET` | `/api/v1/conversations/{id}` | Read current preferences, references, and entries. |
| `POST` | `/api/v1/conversations/{id}/messages` | Submit an instruction and optional references or revision targets. |
| `GET` | `/api/v1/conversations/{id}/messages` | Read ordered message history and saved responses. |
| `POST` | `/api/v1/conversations/{id}/references/import` | Import one explicitly authorized reference. |
| `PATCH` | `/api/v1/conversations/{id}/entries/{entry_id}` | Accept or reject a specific entry version. |
| `GET` | `/api/v1/conversations/{id}/entries/{entry_id}/versions` | Read content versions and their review status. |
| `GET` | `/api/v1/conversations/{id}/entries/{entry_id}/reviews` | Read review decisions. |

A message body requires `instruction` and may include `examples`, `headlines`, `source_urls`, `generation_count`, and `revision_targets`. Each revision target contains `entry_id` and `expected_version`. `import_urls` only records links and explains the separate authorization step; it never fetches a URL.

Successful chat responses use `message`, `clarification`, `generation`, or `revision`. Message/clarification responses have empty `entries`; errors use `kind: "error"` with a safe code, message, and details. Entry review bodies contain `status` (`accepted` or `rejected`) and `expected_version`; they do not change content versions. See `/docs` for exact request schemas, import permission fields, and error statuses.

For message and import submissions, supply a fresh UUID in the `Idempotency-Key` header. Reuse that key and the identical body to recover an uncertain response; a completed submission is replayed without another model call. Reusing a key for changed content returns a conflict. The UI preserves uncertain submissions and offers **Retry same submission** instead of sending automatically. Interrupted work can take up to three minutes to become recoverable; it is never automatically rerun.

Lists accept `limit` (default 20, maximum 100). Conversations and versions use `offset`/`next_offset`; messages and reviews use `after`/`next_after`.

## Storage, limits, and review

SQLite is created automatically on first startup. Conversations, references, messages, entry versions, and review decisions survive restarts. Browser session storage retains the selected chat, unsent composer content, and uncertain submission IDs for that browser session. Local databases are unencrypted; protect the workspace and any backups. Back up the SQLite file with the backend stopped.

Requests are limited to 512 KiB with a 10-second upload deadline. Instructions allow 4,000 characters. Each conversation allows 10 references total, including up to 5 URLs, with 30,000 reference characters in total. A batch allows 1–5 entries, at most 6,000 characters per body and 12,000 requested body characters overall. A conversation holds up to 20 active drafts and 24,000 active body characters; start another chat when that budget is full. Prompt context is bounded even though full history stays in SQLite.

Schema, count, length, category/language metadata, fictional labels, and basic lexical overlap are checked in code. Topic fit, actual body language, tone, factual attribution, semantic diversity, and paraphrased copying still require human review. Passing validation does not prove originality or model quality.

This is a local, single-user application. Conversation separation is not authentication. Public hosting, multi-user authorization, automated publishing, semantic verification, and unlimited memory are outside its scope.

## Tests

From `backend/`, on Windows:

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
~~~

On macOS/Linux:

~~~sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
~~~

From `frontend/`, on Windows:

~~~powershell
npm.cmd run build
npx.cmd playwright install chromium
npm.cmd test
~~~

On macOS/Linux, use `npm run build`, `npx playwright install chromium`, and `npm test`. The build includes strict TypeScript checking. No separate lint command is configured.

Playwright starts its own local backend and frontend with a temporary SQLite database; stop development servers on ports 8000 and 5173 before running it. If Microsoft Edge is already installed, set `$env:PLAYWRIGHT_CHANNEL = "msedge"` in PowerShell (or `export PLAYWRIGHT_CHANNEL=msedge` in a POSIX shell) and run the tests without installing Chromium.

Backend and browser tests use fake providers, synthetic fixtures, and mocked URL imports. Real outbound HTTP transports are blocked, and tests do not load your backend credentials or use your normal database. The quality fixtures measure deterministic constraints and expose semantic limitations; they do not evaluate live Gemini quality. Browser installation and dependency installation need downloads; executing the tests does not require Gemini access.

Git ignores `.env`, credentials, virtual environments, SQLite files/sidecars, caches, browser test output, and frontend builds. Only the blank `.env.example` template belongs in version control.
