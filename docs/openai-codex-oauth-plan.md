# Plan: Codex-style OpenAI OAuth for local LLM judge

## Goal

Add Codex-style OpenAI OAuth for local ConversationAgentEvals so the signed-in user becomes the LLM judge provider (OpenAI-only first), with a provider interface that can later host Claude Code OAuth.

## Decision (locked)

Use **Codex/OpenClaw ChatGPT OAuth** (PKCE + `localhost:1455/auth/callback`), not Platform API keys. Tokens bill against the user’s ChatGPT/Codex subscription via `https://chatgpt.com/backend-api/codex` with `Authorization: Bearer` + `ChatGPT-Account-Id`.

This is unofficial/brittle for third-party products; acceptable for local-first CAE. Claude Code OAuth will plug into the same provider interface later.

## Current state

- Implemented on `feature/openai-codex-oauth-judge`: provider abstraction, local PKCE callback/token storage, provider routes, connection-gated Codex Responses judge execution, benchmark UI controls, and mocked tests.
- The deterministic eval path remains available without OAuth; the LLM judge points disconnected users to Connect OpenAI instead of a paid-plan upgrade.
- OpenClaw reference: client_id `app_EMoamEEZ73f0CkXaXp7hrann`, authorize/token on `auth.openai.com`, redirect `http://localhost:1455/auth/callback`, scopes `openid profile email offline_access`.

## Architecture

```text
UI → POST /api/product/providers/openai/oauth/start
API → PKCE + bind localhost:1455 → authorize_url
UI → open browser → auth.openai.com
Auth → GET /auth/callback?code= → API exchanges + stores tokens
UI → Request LLM judge → API refreshes → Codex Responses
```

## Implementation

### 1. Provider abstraction (OpenAI first, Claude-ready)

Add `apps/api/app/services/llm_providers/`:

- `base.py` — `LlmAuthProvider` protocol: `provider_id`, `status()`, `start_oauth()`, `disconnect()`, `ensure_access_token()`, `complete(prompt) -> str`
- `openai_codex.py` — Codex OAuth + Codex Responses client
- `registry.py` — `get_provider('openai')` now; later `'claude'`

### 2. Local OAuth + token store

- PKCE S256; one-shot `http.server` on `127.0.0.1:1455`
- Persist to project-local `.local/openai-codex-oauth.json` (gitignored)
- Optionally import from `~/.codex/auth.json` when CAE store is empty
- Refresh via `grant_type=refresh_token` before expiry / on 401

### 3. API routes

| Route | Behavior |
|-------|----------|
| `GET /api/product/providers` | List providers + status |
| `POST /api/product/providers/openai/oauth/start` | Start PKCE; return authorize URL |
| `GET /api/product/providers/openai/status` | Connected / disconnected / expired |
| `POST /api/product/providers/openai/disconnect` | Delete local token file |

### 4. Unlock + execute judge via OpenAI

- Gate on provider connection, not paid plan
- When connected: call Codex Responses with evidence-grounded judge prompt
- Keep credit/budget plumbing; label provider `openai_codex`
- Env API-key path remains CI/test fallback

### 5. UI

In `BenchmarkRunner.tsx` auth panel:

- Connect OpenAI → start OAuth + open authorize URL
- Poll status; show connected account + Disconnect
- Request LLM judge blocked copy points to Connect OpenAI

### 6. Safety / docs

- `.gitignore` the local token file
- Document unofficial/local-only nature; Claude later
- Mocked unit tests only (no live OAuth in CI)

## Out of scope

- Hosted/cloud redirect OAuth (Codex client_id is localhost-bound)
- Claude Code OAuth implementation (interface only)
- Removing demo `user_id` identity for saved runs
- Changing deterministic (non-LLM) eval path

## Acceptance criteria

1. Local user can Connect OpenAI via Codex-style browser OAuth.
2. Tokens persist locally and refresh without re-login while refresh token is valid.
3. LLM judge executes through Codex Responses when connected; blocked with clear message when not.
4. Provider interface is structured so Claude Code OAuth can be added without rewriting the judge gate.
5. CI covers mocked oauth/status/judge paths; no dependency on live OpenAI login.
