# OpenAI Codex OAuth

ConversationAgentEvals can use a local OpenAI/Codex sign-in for the `openai_codex` text target, configured local reference-agent model calls, and CAE's standalone product-judge path. Deterministic evaluation remains available without a connection.

This integration uses the localhost Codex OAuth flow and the ChatGPT Codex backend. It is intended for local development, is not a hosted multi-user authentication mechanism, and may change independently of this project.

## Important judge distinction

CAE has two LLM-review paths:

- Standalone report or transcript review through `POST /api/product/judge` can use `LLM_JUDGE_PROVIDER=openai_codex` and the local Codex OAuth session.
- Completed execution-conversation review through `POST /api/assert/runs/{execution_run_id}/conversations/{conversation_id}/judge` invokes the optional upstream `assert-ai` judge. The Codex OAuth session is not forwarded into that subprocess. OpenAI-backed upstream ASSERT judging requires `OPENAI_API_KEY` or `LLM_JUDGE_API_KEY`.

The run-analysis **Review with LLM judge** action for an execution conversation uses the second path. See [upstream-assert-judge.md](upstream-assert-judge.md).

## Connect

1. Start the app with `npm run dev`.
2. Open the benchmark or target configuration UI.
3. Choose **Connect OpenAI** and complete the browser sign-in.
4. Return to the app after the callback completes.

The registered redirect is:

```text
http://localhost:1455/auth/callback
```

Docker Compose publishes port `1455` for this callback. The API stores tokens in `.local/openai-codex-oauth.json`, which is gitignored, and can import an existing `~/.codex/auth.json` connection when the local store is empty. Disconnecting removes the CAE token and suppresses automatic re-import until the next explicit connection.

Only one process can own the fixed callback port at a time. Free port `1455` before starting a new connection flow, or use an API-key/provider configuration instead. Changing `API_PORT` does not change the OAuth callback port.

## API

| Route | Purpose |
| --- | --- |
| `GET /api/product/providers` | List provider states |
| `POST /api/product/providers/openai/oauth/start` | Start PKCE and return the authorization URL |
| `GET /api/product/providers/openai/status` | Read connection state |
| `GET /api/product/providers/openai/models` | List supported models, with a built-in fallback |
| `POST /api/product/providers/openai/disconnect` | Remove the local CAE connection |

Access tokens refresh when possible. CI uses mocked OAuth and response calls; it never requires a live OpenAI login.

## Security notes

- Treat `.local/openai-codex-oauth.json` as a credential.
- Never copy the local token store into a container image or shared artifact.
- The localhost callback does not provide authentication for the CAE API or web app.
- Do not forward the token store into external tools or subprocesses.
- Use a platform API key or a separately designed hosted OAuth flow for server deployments.
