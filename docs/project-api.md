# Project API walkthrough

Run the supported host API/PostgreSQL setup from the [README](../README.md). The API is loopback-only at `http://127.0.0.1:8000`. This walkthrough uses PowerShell; on Linux/macOS, send the same JSON with an HTTP client and use `.venv/bin/python` for the CLI.

These are operator instructions, not an automatic audit. The generation step uses your configured provider when you run it. The repository's browser/consumer tests demonstrate the same flow with a fake provider and no Gemini requests.

## Bootstrap the local administrator

Configure `FABLEAI_DATABASE_URL` in `api/.env` or your process environment to match PostgreSQL. For the default local database it is `postgresql+psycopg://fableai:<percent-encoded-password>@127.0.0.1:5432/fableai`. The launcher supplies its database URL only to the processes it starts; a new terminal needs its own configuration.

```powershell
npm.cmd run db:migrate
Push-Location api
.\.venv\Scripts\python.exe -m app.manage_keys issue-admin --name "Local operator"
Pop-Location
```

The CLI prints a JSON object containing `credential_id` and the raw `api_key` once. Keep it private. This credential belongs to FableAI and is not a Gemini API key.

```powershell
$base = 'http://127.0.0.1:8000/api/v1/projects'
$adminKey = '<administrator-api_key-from-the-local-command>'
$adminHeaders = @{ Authorization = 'Bearer ' + $adminKey }
```

## Create a configured project and scoped credential

```powershell
$project = @{
    project_id = 'sample-editorial'
    display_name = 'Sample Editorial'
    generation_settings = @{
        count = 1
        tone = 'neutral'
        category = 'news'
        language = 'en'
        min_body_chars = 100
        max_body_chars = 300
        body_length_mode = 'target'
    }
    instructions = @('Use short paragraphs.')
    references = @(@{
        text = 'This self-authored sample uses a brief introduction and a clear description of a fictional activity.'
        authorization = @{
            basis = 'owned'
            evidence = 'Written by our editorial team for this example.'
            model_use_allowed = $true
            non_confidential = $true
        }
    })
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri $base -Headers $adminHeaders -ContentType 'application/json' -Body $project
$issued = Invoke-RestMethod -Method Post -Uri "$base/sample-editorial/keys" -Headers $adminHeaders -ContentType 'application/json' -Body '{"name":"Example consumer"}'
$projectHeaders = @{ Authorization = 'Bearer ' + $issued.api_key }
```

Project IDs are immutable lowercase slugs up to 100 characters. Duplicate IDs return 409. Reference IDs are generated when omitted. Saved samples require explicit model-use permission and non-confidential confirmation. Project keys can read, update, and generate only their own project; administrator-only listing, project creation, key management, and conversation access are denied to them.

## Inspect settings and the output schema

```powershell
Invoke-RestMethod -Uri "$base/sample-editorial" -Headers $projectHeaders
Invoke-RestMethod -Method Patch -Uri "$base/sample-editorial" -Headers $projectHeaders -ContentType 'application/json' -Body '{"generation_settings":{"tone":"informative"}}'
Invoke-RestMethod -Uri "$base/sample-editorial/output-contract" -Headers $projectHeaders
```

PATCH merges supplied generation settings with the saved settings. Supplying `instructions` or `references` replaces that entire list; an empty list clears it. Empty updates and explicit nulls are rejected. The output-contract response is the current JSON Schema for `DraftBatch`.

## Generate and recover an uncertain request

```powershell
$requestId = [guid]::NewGuid().ToString()
$generationHeaders = @{ Authorization = 'Bearer ' + $issued.api_key; 'Idempotency-Key' = $requestId }
$body = '{"instruction":"An imaginary harbor festival","overrides":{"category":"announcement","count":1}}'
$result = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$base/sample-editorial/generations" -Headers $generationHeaders -ContentType 'application/json' -Body $body
$result.Content | ConvertFrom-Json
$result.Headers
```

The per-request category/count override does not modify the saved project. The other settings, reusable instructions, and authorized sample patterns are applied from that project.

Illustrative successful response; actual text depends on the provider:

```json
{"entries":[{"title":"[FICTIONAL] Kite Workshop Adds Floating Mosaics","body":"In the invented harbor of Tessara, painted kites carry a shifting mosaic over a paper pier. Visitors arrange bright tiles above the pretend tide.","category":"announcement","tags":["fiction"],"language":"en","is_synthetic":true,"status":"draft"}]}
```

The response includes `Idempotency-Key`, `X-Request-ID`, `Idempotency-Scope: process`, `Idempotency-Retention-Seconds: 600`, `Idempotency-Replayed`, and a generation/length message header on success.

If a connection fails after submission, reuse the **same UUID and identical JSON body** to check the current process's cached result. A changed body with the same key returns 409. Pending duplicates return 409 without another provider attempt. Completed results expire after ten minutes; cache capacity is 128 and results are lost on restart. This is not a durable exactly-once guarantee. Do not automatically resend after expiry or restart.

The small Node consumer performs one attempt and verifies the echoed request ID:

```powershell
$env:FABLEAI_API_URL = 'http://127.0.0.1:8000'
$env:FABLEAI_API_KEY = $issued.api_key
node examples/generate.mjs sample-editorial 'An imaginary harbor festival'
Remove-Item Env:FABLEAI_API_KEY
```

It does not implement automatic recovery or publish content. Use HTTPS if adapting the consumer for an explicitly secured external server; the included server configuration itself remains local-only.

## Errors and revocation

An unauthenticated request returns HTTP 401 with `WWW-Authenticate: Bearer` and:

```json
{"kind":"error","conversation_id":null,"message_id":null,"error":{"code":"unauthorized","message":"A valid FableAI API credential is required.","details":[]}}
```

Wrong project/scope returns 403; unknown projects return 404; invalid requests/settings return 422; quota/cache capacity returns 429; provider or output-validation failures return 502; storage failures return 503. Project-operation errors normally include a generated request ID. Error JSON never contains rejected draft content or provider credentials. No provider retry or model fallback is made. With generation unconfigured, a valid request returns 502 identifying missing backend credentials.

Rotate or revoke using the administrator credential:

```powershell
$replacement = Invoke-RestMethod -Method Post -Uri "$base/sample-editorial/keys/$($issued.credential_id)/rotate" -Headers $adminHeaders
Invoke-RestMethod -Method Delete -Uri "$base/sample-editorial/keys/$($replacement.credential_id)" -Headers $adminHeaders
```

The original key is invalid after rotation. The replacement is invalid after revocation, including for requests whose results are cached. GET `/{project_id}/keys` lists metadata only. The administrator CLI can also rotate/revoke by credential ID.

Project outputs contain no chat IDs or revisions and are not stored as chat history. The chat API's durable submission/review/version behavior is a separate contract documented in the [README](../README.md).
