# Environment Reference

Use `.env.example` for the first-run local demo. It is intentionally small: copy it to `.env`, run `npm run setup`, then run `npm run dev`.

## Required for the minimal local demo

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `3012` | Browser-facing web app port. `npm run dev` also uses it for the local web port unless `WEB_PORT` is set. |
| `API_PORT` | `8025` | Host port for the FastAPI service. |
| `PIPECAT_PORT` | `8110` | Host port for the Pipecat service. The benchmark demo can run without live microphone ASR. |
| `APP_ENV` | `development` | Runtime environment label. Local sidecars and demo affordances assume a development-like value. |
| `PRODUCTION` | `false` | Set `true` only when non-live testing controls should be hidden. |

`npm run check:env`, `npm run dev`, and `npm run test:benchmark-smoke` validate these variables and print a focused error if one is missing or malformed.

## Optional browser check settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `PLAYWRIGHT_BASE_URL` | `http://127.0.0.1:3012` | Browser URL for Playwright when reusing a running local stack. |
| `PLAYWRIGHT_REUSE_EXISTING_SERVER` | `1` | Reuse the server already started by `npm run dev`. |

## Service URL overrides

`npm run dev` derives service URLs from the ports above and passes them to the API, Pipecat service, and web app. Leave these unset for the blessed local demo unless you are wiring a custom topology:

```bash
API_BASE_URL=http://localhost:8025
PIPECAT_SERVICE_URL=http://localhost:8110
NEXT_PUBLIC_API_BASE_URL=http://localhost:8025
NEXT_PUBLIC_PIPECAT_SERVICE_URL=http://localhost:8110
WEB_PORT=3012
```

## Docker Compose and persistence

The default Compose demo does not require Postgres. It uses SQLite through the `COMPOSE_DATABASE_URL` default baked into `docker-compose.yml`.

Use these only for Compose customization or the `persistence` profile:

```bash
POSTGRES_DB=conversation_agent_evals
POSTGRES_USER=cae
POSTGRES_PASSWORD=cae_local_password
POSTGRES_PORT=54329
DATABASE_URL=postgresql://cae:cae_local_password@localhost:54329/conversation_agent_evals
COMPOSE_DATABASE_URL=sqlite:////workspace/storage/conversation_agent_evals.db
WORKER_POLL_INTERVAL_SECONDS=30
```

For the persistence profile, point API and worker traffic at the Compose database:

```bash
COMPOSE_DATABASE_URL=postgresql://cae:cae_local_password@db:5432/conversation_agent_evals docker compose --profile persistence up --build
```

## Live ASR and voice experiments

The first-run benchmark demo uses transcript, action-trace, and final-state evidence. It does not require live microphone ASR.

Conversation demos use `rtc-asr` as the speech-to-text provider contract. Pipecat expects a service reachable at `RTC_ASR_BASE_URL` and streams audio to `RTC_ASR_STREAM_PATH` using 16 kHz mono PCM16 little-endian input.

```bash
RTC_ASR_BASE_URL=http://localhost:8000
RTC_ASR_HEALTH_PATH=/health
RTC_ASR_STREAM_PATH=/v1/stt/stream
KOKORO_BASE_URL=http://localhost:8880
KOKORO_MODEL=kokoro
KOKORO_VOICE=af_heart
REFERENCE_STT_BACKEND=whisper
REFERENCE_STT_MODEL=base
REFERENCE_LLM_MODEL=gpt-5.4-mini
```

The built-in generalist voice target is a real local reference pipeline:
Pipecat tester → Kokoro caller audio → separate Pipecat agent → rtc-asr → configured
OpenAI-compatible/Codex LLM → Kokoro reply audio → tester observation. Select
`REFERENCE_STT_BACKEND=whisper` for rtc-asr Whisper base (the service may report
`faster-whisper` / `base.en`) or `REFERENCE_STT_BACKEND=parakeet` for the existing
MLX Parakeet rtc-asr lane. The API fails closed if the configured backend does not
match the healthy rtc-asr service.

API and Pipecat protect the local completion callback with
`REFERENCE_AGENT_INTERNAL_TOKEN`. `npm run dev` creates one ephemeral token and passes
it to both processes when the variable is absent. Managed or separately started
processes must set the same non-empty value explicitly; it is never included in run
artifacts or provenance.

When Pipecat runs in the Compose `voice` profile and rtc-asr runs on the host, use the host-reachable base URL:

```bash
RTC_ASR_BASE_URL=http://host.docker.internal:8000 docker compose --profile voice up --build
```

When `RTC_ASR_BASE_URL` is empty or unhealthy, live session startup records ASR as `not_configured` or `unavailable` and logs a `rtc_asr_skipped` event. The `/sessions/{id}/ask` transcript loop remains non-production demo support, not the ASR provider contract.

## Optional product integrations

These are not needed for the minimal local demo. Set them only when working on the related integration path.

```bash
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_REALTIME_MODEL=gpt-realtime-mini
OPENAI_RESPONSES_MODEL=gpt-4.1-mini
LLM_JUDGE_PROVIDER=openai_codex
LLM_JUDGE_MODEL=gpt-5.4-mini
LLM_JUDGE_API_KEY=
OPENAI_CODEX_OAUTH_PATH=
OPENAI_CODEX_IMPORT_HOME=1
LLM_JUDGE_DAILY_CREDIT_LIMIT=200
LLM_JUDGE_RESERVED_DAILY_CREDITS=0
HEYGEN_LIVE_AVATAR_API_KEY=
HEYGEN_API_KEY=
HEYGEN_AVATAR_ID=
HEYGEN_SANDBOX=true
HEYGEN_SANDBOX_AVATAR_ID=dd73ea75-1218-4ef3-92ce-606d5f7fbc0a
FIREBASE_PROJECT_ID=
FIREBASE_API_KEY=
NEXT_PUBLIC_FIREBASE_PROJECT_ID=
NEXT_PUBLIC_FIREBASE_API_KEY=
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=
NEXT_PUBLIC_FIREBASE_APP_ID=
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=us-central1
STRIPE_SECRET_KEY=
NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=
STRIPE_STARTER_PRICE_ID=
STRIPE_TEAM_PRICE_ID=
STRIPE_CHECKOUT_BASE_URL=
BUSINESS_CONTACT_URL=
REALTIME_REQUEST_TIMEOUT_MS=5000
ASSERT_LOCAL_SIDECAR_ENABLED=
```

### Local OpenAI Codex OAuth judge

`LLM_JUDGE_PROVIDER=openai_codex` uses the Connect OpenAI control in the benchmark runner. OAuth tokens are stored in the gitignored `.local/openai-codex-oauth.json` file by default; set `OPENAI_CODEX_OAUTH_PATH` only to move that local store. An existing `~/.codex/auth.json` is imported when the local store is empty unless `OPENAI_CODEX_IMPORT_HOME=0`. `LLM_JUDGE_API_KEY` remains an optional API-key fallback for CI.

This Codex-style ChatGPT OAuth integration is unofficial, brittle, local-only, and OpenAI-only. It is not a hosted OAuth flow. Claude is not implemented yet, but can be added later through the same provider interface.

For Docker Compose, the API service publishes the fixed mapping `1455:1455` so the browser redirect to `http://localhost:1455/auth/callback` reaches the ephemeral callback listener inside the container. The host callback port is not configurable because the Codex OAuth redirect URI is fixed to `localhost:1455`; choose a different API port if `1455` is already in use. OAuth tokens persist through the `./.local:/workspace/.local` bind mount. Override the listener bind address with `OPENAI_CODEX_CALLBACK_BIND_HOST` only if you need a non-default host interface.

User-created scenarios persist under `storage/user_scenarios.json` (Compose-mounted at `/workspace/storage`). Override with `USER_SCENARIOS_PATH` if needed. A one-time copy from the legacy `apps/api/data/user_scenarios.json` path runs when the new file is missing.
