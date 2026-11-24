# FableAI

FableAI generates explicitly fictional News, Announcement, Update, Article, and Blog drafts as validated JSON. It provides two entry points:

- **Project API:** consuming applications use saved project settings, reusable instructions, authorized samples, and a scoped credential.
- **Local chat/playground:** a React interface for generation, selected revisions, review decisions, and persistent conversation history.

Every generated title starts with `[FICTIONAL]`. Entries have `is_synthetic: true` and initially `status: draft`. Accepting a draft records a review decision; it does not publish content, verify events, or certify writing quality.

## Architecture

Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL 16, the Google Gen AI SDK, React, TypeScript, and Vite.

```text
api/app/generation/   Prompt construction, reference safeguards, generation and revision
api/app/persistence/  SQLAlchemy repositories and database lifecycle
api/app/transport/    HTTP routes, credentials, request safeguards
api/migrations/       Versioned Alembic schema
api/tests/            Offline unit/integration tests and fake browser backend
frontend/src/         Chat/playground and local writing review
frontend/tests/       Playwright browser regressions
scripts/              Dependency setup and development process management
examples/             Small Node.js HTTP consumer and offline tests
.github/workflows/    Credential-free CI checks
```

Project configuration and content generation are separate operations. Saving settings or samples never invokes generation. Project generation returns a `DraftBatch` and does not create chat records or a persistent generation archive. Chat messages, active drafts, revisions, references, and reviews are persisted separately.

PostgreSQL is the application database. SQLite is retained for disposable offline fixtures and safe detection of old local files; existing SQLite history is not migrated automatically.

## Install and start

Requirements: Python 3.12, Node.js 22.12 or newer, npm, and Docker with Compose for the default PostgreSQL development path. CI uses Node.js 24. Docker must be running before starting the backend.

From the repository root:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
npm.cmd run setup
```

Use `npm` instead of `npm.cmd` on Linux/macOS; copy the root template only if `.env` does not exist, using `cp -n .env.example .env`. No root `npm install` is required. Setup creates `api/.venv`, installs the pinned backend dependencies and locked frontend dependencies, and copies the blank `api/.env.example` to `api/.env` if missing. It preserves an existing file and does not prompt for Gemini settings.

Edit the ignored root `.env` and set `POSTGRES_PASSWORD` to a long random value. Keep it private. The PostgreSQL image initializes its user, password, and database only when its volume is first created: changing these values later does not rotate an existing database password. Never remove a normal data volume to resolve a configuration mismatch.

```powershell
npm.cmd run start:all
```

Open [the playground](http://127.0.0.1:5173), [API documentation](http://127.0.0.1:8000/docs), or [health](http://127.0.0.1:8000/health). Health returns `{"status":"ok"}` without contacting Gemini. The launcher starts PostgreSQL using the development Compose override, derives a percent-encoded loopback connection URL, applies migrations, then starts the API and Vite. The optional container entrypoint also builds its database URL with proper credential encoding and refuses to serve after a migration failure.

Generation can remain unconfigured: health, project configuration, credentials, and conversation history still work. A generation attempt then returns a safe missing-credentials error without a network request.

| Command | Behavior |
| --- | --- |
| `npm run setup` | Refresh dependencies and prepare the blank API environment template; no servers. |
| `npm run start:all` | Start PostgreSQL, migrate, and run API plus frontend. |
| `npm run start:backend` | Start PostgreSQL, migrate, and run the host API. |
| `npm run start:frontend` | Run Vite only; requires an appropriately configured backend. |
| `npm run start:all:setup` | Refresh dependencies, then start both servers. |
| `npm run db:migrate` | Apply Alembic migrations using the configured API database URL. |
| `npm run test:scripts` | Test process lifecycle, reload, cancellation, and port conflicts. |
| `npm run test:examples` | Test the external HTTP client using fake responses. |

Press Ctrl+C to stop the launched API/frontend processes. PostgreSQL stays running so its data remains available. The launcher reports port conflicts and stops its own peer process if a development server exits. If a Compose API was already running, the launcher temporarily stops and later restores it. Code reload interrupts in-flight requests; wait for generation to finish before editing.

The standard browser/HTTP development path is the **host API with Docker PostgreSQL**. The optional Compose API image publishes only `127.0.0.1:8000` and explicitly enables container mode to accept private Docker bridge peers with a loopback Host. Host/origin checks and API credentials still apply; bridge peers cannot use anonymous demo or the ephemeral local development credential. This is not a public deployment configuration.

## Configuration and credentials

The root `.env.example` contains PostgreSQL initialization values. `api/.env.example` contains backend configuration. Neither template contains credentials.

| Variable | Location and purpose |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` | Root environment: Compose PostgreSQL initialization and launcher connection settings. |
| `FABLEAI_DATABASE_URL` | API environment: PostgreSQL URL for direct Uvicorn, migrations, and credential CLI. Percent-encode reserved characters in credentials. The development launcher derives its own URL from root PostgreSQL settings. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | API environment: optional owner-supplied key and exact model ID. No default model is selected. |
| `FABLEAI_LOCAL_DEMO` | API environment: default false; explicitly enables anonymous conversation routes on loopback only. Project routes still require credentials. |
| `FABLEAI_CONTAINER_MODE` | Process-only, default false. Compose enables it for private bridge peers; use the provided loopback port binding and credentials. |
| `FABLEAI_LOCAL_DEVELOPMENT_KEY` | Generated in memory by combined/backend startup; grants conversation access only. The combined Vite process adds it server-side. It is not saved or put in the browser bundle. |

The backend reads `api/.env` independently of its working directory, literally without variable interpolation. Process variables take precedence, including empty values. Restart after changes. For direct CLI use, set `FABLEAI_DATABASE_URL` in your shell or API environment to the same database used by the launcher.

FableAI service credentials are separate from Gemini credentials. Bootstrap an administrator using local database access:

```powershell
Push-Location api
.\.venv\Scripts\python.exe -m app.manage_keys issue-admin --name "Local operator"
Pop-Location
```

On Linux/macOS use `.venv/bin/python`. The command prints a new administrator secret once. Store it privately. The CLI also supports `rotate <credential-id>` and `revoke <credential-id>`. Administrator keys can create/list projects and manage project keys. Scoped keys can read/update/generate only their own project; they cannot access key management or chat/history. The database stores SHA-256 key hashes, not raw keys; revoked keys are rechecked on every request, including cached replays.

Combined `start:all` makes the local playground usable without an administrator key in browser code. Separate frontend/backend terminals do not share the ephemeral key automatically. For that deliberate local-only workflow, enable `FABLEAI_LOCAL_DEMO=true` in the backend or share an explicit process credential privately. A built `frontend/` preview is not a separate authenticated product: it needs the same deliberate backend access setup.

See [the executable Project API walkthrough](docs/project-api.md) for project creation, key issuance, settings, output schema, generation, rotation, and error examples. [examples/generate.mjs](examples/generate.mjs) is a small HTTP consumer, not a published SDK.

## Chat/playground

Create a chat, type a meaningful topic, and send. Writing Options provide content type, tone, language, body length, and count. The browser starts with one News draft, neutral tone, English, and a target of 100?300 body characters. Settings persist per chat in browser session storage. Explicit instructions can override them. A trailing topic number is part of the subject unless it explicitly specifies a draft count.

Attach authorized plain-text `.txt` samples with the paperclip. Each is a removable tile above the composer. HTTPS links in a message are stored as link metadata. Request/response turns have separate dividers and expanded JSON with a copy button. Accept/Reject records review status. Select for revision targets specific entries; revisions preserve their IDs, increment their versions, and reset their review status. Unselected entries retain their content and reviews. Conversation history survives API restarts.

Length presets are targets; usable off-target drafts receive a notice. Explicit numerical character ranges remain strict. A standalone topic uses the current message and supplied references; explicit follow-ups reuse bounded prior context. Only genuinely ambiguous or unsupported requests need clarification.

The current UI does not expose project/key administration or the separate reference-import authorization form. Those operations remain available through authenticated HTTP routes. It does not claim to retrieve current news.

## HTTP contracts and recovery

Project routes use `/api/v1/projects`:

| Method | Path suffix | Access |
| --- | --- | --- |
| POST / GET | empty | Administrator: create/list projects |
| GET / PATCH | `/{project_id}` | Administrator or matching project key |
| GET | `/{project_id}/output-contract` | Administrator or matching project key |
| POST | `/{project_id}/generations` | Administrator or matching project key |
| POST / GET | `/{project_id}/keys` | Administrator: issue/list scoped keys |
| DELETE | `/{project_id}/keys/{credential_id}` | Administrator: revoke key |
| POST | `/{project_id}/keys/{credential_id}/rotate` | Administrator: rotate key |

Project generation accepts `instruction` and optional partial `overrides`. Overrides merge with saved defaults for that request and never mutate project configuration. Successful output is `{"entries":[...]}`, without chat IDs or content versions. Key secrets are returned only at issuance/rotation.

Conversation routes use `/api/v1/conversations`: create/list/read/delete; POST/GET `/{id}/messages`; POST `/{id}/references/import`; PATCH `/{id}/entries/{entry_id}` for reviews; GET `/{id}/entries/{entry_id}/versions` and `/reviews`. These require an administrator, the ephemeral local development credential, or explicitly enabled local demo. Message responses have kinds `message`, `clarification`, `generation`, `revision`, or `error`. Review bodies contain `status` and `expected_version`.

For generation and conversation submissions, send a UUID4 `Idempotency-Key` and reuse the identical body/key after an uncertain connection failure.

- **Project generation:** replay state is held in the current process only, expires 600 seconds after completion, and is capped at 128 requests. It is cleared on restart and is not shared across workers. After expiry/restart, a repeated key may start a new generation. Response headers describe scope, retention, replay, and request ID. A pending duplicate returns 409.
- **Conversations:** submission results and history are durable in PostgreSQL. Repeated completed submissions replay without another provider attempt. Interrupted claims become recoverable after a three-minute lease; they are not automatically rerun. The browser offers an explicit same-submission retry.
- Provider timeout, quota, malformed JSON, and validation failures produce safe errors. Each invocation has a 60-second timeout and no automatic retries or model fallback. Chat interpretation and drafting may use two invocations; project generation uses one. Startup, history, settings, keys, and review do not call Gemini.

Lists support bounded pagination. Project/conversation/version lists use `offset`; message/review lists use `after`. Exact schemas are served at `/openapi.json`.

## Safety, references, and limits

Use only non-confidential material that you own or are authorized to submit for model use. Keep credentials, private documents, and personal information out of prompts. Read the applicable [Google AI terms](https://ai.google.dev/gemini-api/terms) and verify model access, regional availability, [pricing](https://ai.google.dev/gemini-api/docs/pricing), and account limits yourself. FableAI cannot inspect billing or guarantee zero cost for an arbitrary account.

Reference imports require a separate confirmed action, permission basis and evidence for each exact URL, and explicit automated-access permission. Model use also requires permission and non-confidential confirmation. Redirect destinations require permission too. Public reachability alone is not authorization. Imports only allow bounded public HTTPS HTML, with DNS/address/redirect validation; private and metadata networks are blocked. No crawler, paywall bypass, or automatic import runs. Samples are reduced to local structural summaries; user instructions and bounded conversation context are still sent during generation.

| Limit | Value |
| --- | --- |
| Request body / upload deadline | 512 KiB / 10 seconds |
| Instruction / saved project instruction total | 4,000 characters |
| References per conversation/project | 10, with 30,000 total characters |
| Conversation URLs | 5 |
| Entries per generation / body | 1?20 / up to 6,000 characters |
| Batch body budget | 12,000 characters |
| Active conversation drafts / bodies | 20 / 24,000 characters |

Schema validation checks metadata, counts, budgets, fictional labels, certain unsupported claims/quotations, and lexical overlap. It does not establish semantic originality, language fluency, canon accuracy, attribution, or factual truth. Keep human review.

This is a local application, not a public multi-user deployment. Host/origin checks enforce local browser access; the host API also requires loopback peers. Explicit Compose container mode permits private bridge peers with credentials. Project credentials enforce isolation. TLS termination, remote identity, public hosting, automated publishing, and current-information retrieval are outside scope. PostgreSQL data and backups are not encrypted by FableAI; protect the host and use appropriate database backup tools. Never use `docker compose down --volumes` on a normal stack unless you intend to delete its data.

## Verification

From the repository root:

```powershell
npm.cmd run test:scripts
npm.cmd run test:examples
Push-Location api
.\.venv\Scripts\python.exe -m pytest --postgres -q
.\.venv\Scripts\python.exe -m pip check
Pop-Location
npm.cmd --prefix frontend run build
Push-Location frontend
npx.cmd playwright install chromium
npm.cmd test
Pop-Location
```

On Linux/macOS use `.venv/bin/python`, `npm`, and `npx`. Plain pytest runs offline and skips PostgreSQL and Docker integration checks. `--postgres` creates and destroys a uniquely named disposable PostgreSQL service/databases. `pytest tests/test_docker.py --docker -q` builds an isolated Compose stack, checks in-container health/migrations/restart, and removes only its own resources.

Playwright starts the existing fake-provider backend with disposable SQLite data on ports 8000/5173, plus Vite. It exercises real browser/HTTP workflows, including the Node consumer, without real Gemini or source imports. Stop conflicting development servers deliberately. Microsoft Edge can be selected with `PLAYWRIGHT_CHANNEL=msedge`.

[CI](.github/workflows/ci.yml) runs backend checks on Linux/Windows, PostgreSQL on Linux, the frontend build/browser suite, launcher/client tests, and a separate Docker smoke check. It uses no Gemini secrets or deployment automation. [Hosted workflow runs](https://github.com/Arvence/FableAI/actions/workflows/ci.yml) record validation for published commits. Live Gemini quality remains unverified. The offline quality fixtures are acceptance/heuristic checks, not a live-model benchmark.

FableAI is complete within its intended local API and chat/playground scope and is in maintenance mode for bug fixes, security updates, and dependency maintenance. Later agent or AI Engineering experiments belong to a separate project scope. Live Gemini quality remains unverified.
