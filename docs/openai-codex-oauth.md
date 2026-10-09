# OpenAI Codex OAuth

ConversationAgentEvals can use a local OpenAI/Codex sign-in for the `openai_codex` text target, configured local reference-agent model calls. Deterministic evaluation remains available without a connection.

This integration uses the localhost Codex OAuth flow and the ChatGPT Codex backend. It is intended for local development, is not a hosted multi-user authentication mechanism, and may change independently of this project.

## Semantic judging is separate from target authentication

CAE has one semantic judge: the pinned ASSERT runtime. Both the uploaded vCon/benchmark **LLM Judge** button and completed-run **Review with LLM judge** action use it. `GET /api/assert/readiness` is authoritative for judge configuration; a connected Codex OAuth session does not make ASSERT ready.

OpenAI-backed ASSERT requires `OPENAI_API_KEY` or its `LLM_JUDGE_API_KEY` alias, plus `ASSERT_UPSTREAM_JUDGE_ENABLED=1`. OAuth tokens are never forwarded into ASSERT. The retired product-judge endpoint and its completion implementation have been removed. See [upstream-assert-judge.md](upstream-assert-judge.md).

## Connect

For API-key-free ASSERT judging, use the separate [ChatGPT-plan connection](chatgpt-plan-oauth.md)
in Console Settings. It requires its own plan-use consent; the legacy execution
connection documented here does not grant that permission.

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
