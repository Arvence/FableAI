# FableAI

FableAI is a local chat application for creating explicitly fictional news and announcement drafts from meaningful prompts, optionally guided by authorized samples, headlines, and reference URLs. It returns validated JSON, remembers conversation context, and supports revisions to selected entries with stable IDs and version history.

Generated content is for creative drafting, demos, and testing. Every draft carries a fictional title marker and a synthetic-content label. Accepting a draft records a local review decision; it does not publish anything or verify real events.

## Stack and layout

- Python 3.12, FastAPI, Pydantic, Uvicorn, and the Google Gen AI Python SDK.
- SQLite with SQLAlchemy for conversations, references, messages, reviews, and entry versions.
- React, TypeScript, and Vite for the browser interface.
- pytest with mocked HTTP transports and Playwright for browser tests.

Application code and backend tests live in `backend/app/` and `backend/tests/`. The browser app and its tests live in `frontend/src/` and `frontend/tests/`. No separate database server is required.

## Install and start locally

Install Python 3.12 and Node.js 22.12 or newer with npm. On Windows, check that `python --version` selects Python 3.12; the launcher also supports `py -3.12`. On macOS/Linux, install `python3.12`.

Run commands from the repository root. No root-level `npm install` is needed.

~~~sh
npm run start:all
~~~

On first start, the launcher creates `backend/.venv`, installs missing backend/frontend dependencies, and creates `backend/.env` from the blank template if needed. In an interactive terminal, it asks for missing `GEMINI_API_KEY` and `GEMINI_MODEL` values. The key is hidden while typing; settings are saved only in the Git-ignored backend environment file. Existing values and unrelated settings are preserved.

Use your own key and exact model ID after completing the account checks below. Setup does not create credentials, choose a model, verify billing, or make Gemini requests. Press Enter to skip a setting. Without credentials, the app can still open, load history, and serve its health endpoint. In a noninteractive terminal, edit `backend/.env` locally before using generation.

| Command | Action |
| --- | --- |
| `npm run start:all` | Start both development servers. |
| `npm run start:frontend` | Start only the frontend; does not ask for backend credentials. |
| `npm run start:backend` | Start only the backend. |
| `npm run start:all:setup` | Refresh both dependency installations, fill missing settings, then start both servers. |
| `npm run setup` | Refresh dependencies and fill missing settings without starting servers. |

You can also append `-- --setup` to any start command, for example `npm run start:backend -- --setup`. Use setup after pulling dependency changes. Normal starts reuse installed dependencies and configured credentials. If PowerShell blocks `npm.ps1`, use `npm.cmd` in these commands.

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Vite forwards `/api` requests to the backend at `127.0.0.1:8000`. Both servers bind to loopback. The relevant ports must be available; the launcher reports conflicts without stopping existing processes. Press `Ctrl+C` once to stop the launched servers. If either server exits, the launcher stops its peer.

[GET /health](http://127.0.0.1:8000/health) returns `{"status":"ok"}` without a model call. API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs), with the machine-readable contract at `/openapi.json`.

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

Each provider invocation makes one request with a 60-second default timeout. Interpreting a turn uses a model request; generating or revising drafts can use a second request. There are no automatic retries, model switches, tool calls, or paid fallbacks. A quota error tells you to wait. Starting the app, loading history, and accepting/rejecting an entry do not call Gemini.

Google's Unpaid Services terms permit service improvement use of prompts and responses and human review, with regional exceptions. Read the applicable [data-use terms](https://ai.google.dev/gemini-api/terms). Use only non-confidential, self-authored, public-domain, or appropriately licensed/authorized material. Do not submit personal information, credentials, or private documents.

## References and a sample conversation

Create a chat and write in **Your message**. Paste text and HTTPS links directly into that one box. Use the paperclip to attach authorized `.txt` samples; each file appears as a removable tile above the message. Attached samples are saved as references, while text in the message is treated as your instruction. Longer reference material belongs in an attached file.

Start with a topic such as "rainy day", a phrase such as "a library on Mars", or a request such as "create rainy day 6". References are optional. Missing settings use one draft, neutral tone, English, announcement category, and 100-300 characters per body. Explicit settings and saved conversation preferences take precedence over defaults. Up to six drafts can be requested per turn. Larger counts, including shorthand such as "create rainy day 7", prompt a count clarification before drafting; the topic is retained for the answer. Clarification is reserved for unclear topics, ambiguous revisions, and unsupported or conflicting requirements.

Requests and responses are grouped by turn, with divider lines between turns. User messages appear on the right and assistant responses on the left. Response JSON is always expanded; the copy icon on its right copies the exact JSON. The conversation scrolls within the chat workspace on desktop and mobile, and the message box stays below it. Saved references are reused in later turns.

For example:

1. Attach a `.txt` file containing this self-authored sample: "In the fictional town of Bramblewick, glass flowers brighten a quiet workshop. Guests choose colored petals for a pretend garden display."
2. Send: "Remember these preferences: English announcements about an imaginary paper-bird pavilion, neutral tone, one draft per request, and 100 to 240 characters per body."
3. Send: "Generate one fictional draft." Clarify only genuinely ambiguous or unsupported requirements when prompted.
4. Inspect the entries in the expanded **Response JSON**. Use the copy icon for the exact response. Entry acceptance and rejection are available through the HTTP API.
5. Send: "Make entry 1 shorter." Its ID stays the same, its version increases, and its review status returns to `draft`. Unselected entries and their reviews remain unchanged.
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
| `DELETE` | `/api/v1/conversations/{id}` | Permanently delete a chat and its saved history; returns 204. Active requests return 409. |
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

Use the sidebar to select saved chats and the **New chat** and **Delete chat** actions to manage them. On mobile, chats appear in a horizontal list above the conversation. Deletion requires confirmation and removes the chat's saved messages, references, drafts, versions, and review history. An active request must finish before its chat can be deleted.

SQLite is created automatically on first startup. Conversations, references, messages, entry versions, and review decisions survive restarts. Browser session storage retains the selected chat, unsent composer content, and uncertain submission IDs for that browser session. Local databases are unencrypted; protect the workspace and any backups. Back up the SQLite file with the backend stopped.

Requests are limited to 512 KiB with a 10-second upload deadline. Instructions allow 4,000 characters. Each conversation allows 10 references total, including up to 5 URLs, with 30,000 reference characters in total. A batch allows 1–6 entries, at most 6,000 characters per body and 12,000 requested body characters overall. A conversation holds up to 20 active drafts and 24,000 active body characters; start another chat when that budget is full. Prompt context is bounded even though full history stays in SQLite.

Schema, count, length, category/language metadata, fictional labels, and basic lexical overlap are checked in code. Quoted invented names and ordinary apostrophes are allowed. Attributed speech quotations, source links/citations, and unsupported verification or endorsement labels are rejected with the affected field and a specific reason; rejected content is not returned and no automatic retry is made. Topic fit, actual body language, tone, factual attribution, semantic diversity, and paraphrased copying still require human review. Passing validation does not prove originality or model quality.

This is a local, single-user application. Conversation separation is not authentication. Public hosting, multi-user authorization, automated publishing, semantic verification, and unlimited memory are outside its scope.

## Tests

From the repository root, run `npm run test:scripts` for launcher lifecycle and port-conflict checks. Backend tests also cover first-run configuration using temporary files and fake credentials.

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
