# OpenAI Codex OAuth

ConversationAgentEvals can use a local OpenAI/Codex sign-in for the LLM judge and the
`openai_codex` text target. Deterministic evaluation remains available without a connection.

This integration uses the localhost Codex OAuth flow and the ChatGPT Codex backend. It is
intended for local development, is not a hosted multi-user authentication mechanism, and may
change independently of this project.

## Connect

1. Start the app with `npm run dev`.
2. Open the benchmark or agent configuration UI.
3. Choose **Connect OpenAI** and complete the browser sign-in.
4. Return to the app after the callback completes.

The registered redirect is:

```text
http://localhost:1455/auth/callback
```

Docker Compose publishes port `1455` for this callback. The API stores tokens in
`.local/openai-codex-oauth.json`, which is gitignored, and can import an existing
`~/.codex/auth.json` connection when the local store is empty. Disconnecting removes the CAE
token and suppresses automatic re-import until the next explicit connection.

## API

| Route | Purpose |
| --- | --- |
| `GET /api/product/providers` | List provider states |
| `POST /api/product/providers/openai/oauth/start` | Start PKCE and return the authorization URL |
| `GET /api/product/providers/openai/status` | Read connection state |
| `GET /api/product/providers/openai/models` | List supported models, with a built-in fallback |
| `POST /api/product/providers/openai/disconnect` | Remove the local CAE connection |

Access tokens refresh when possible. CI uses mocked OAuth and response calls; it never requires
a live OpenAI login.

## Security notes

- Treat `.local/openai-codex-oauth.json` as a credential.
- Never copy the local token store into a container image or shared artifact.
- The localhost callback does not provide authentication for the CAE API or web app.
- Use a platform API key or a separately designed hosted OAuth flow for server deployments.
