# Local ChatGPT-plan judging

## Implementation plan and scope

1. Register a local OSS OAuth client with explicit ChatGPT plan-use consent, PKCE,
   one-time state/nonce, signed ID-token validation, stable host ID and separately
   retained account/workspace registrations.
2. Register a `chatgpt_plan/<model>` LiteLLM transport and execute the unchanged
   pinned ASSERT 0.3 judge-only CLI. Do not copy its prompts, rubrics, schema,
   aggregation, or semantic judging into CAE.
3. Expose local Console Settings account/model controls. Readiness is configuration,
   not proof that inference succeeded. Keep upload and completed-live-run judging on
   the existing authorization, admission, review, confirmation, and export pipeline.
4. Validate signed tokens, consent, refresh, account switching, incomplete streams,
   real ASSERT/LiteLLM transport dispatch, security guards and frontend interactions.
   A user-authorized live sign-in and synthetic review remains the acceptance check
   for actual account eligibility and model access; CI never uses real credentials.

This slice adds **judging**, not a second judge engine or a replacement for CAE
identity. Legacy Codex agent execution/authoring remains unchanged and independently
connected. No credentials are imported from `~/.codex/auth.json` for this transport.
Speech recognition/TTS are not covered by this ChatGPT-plan flow.

## Enable and connect

- Use a local installation with API and web published only on loopback. Standard
  Compose publishes both HTTP entry points on `127.0.0.1`; native npm dev/start
  commands also bind loopback. The web Dockerfile explicitly overrides Next's
  listener to `0.0.0.0` **inside** its container, behind the loopback host publishing.
- Set `CHATGPT_PLAN_LOCAL_ENABLED=1` and `ASSERT_UPSTREAM_JUDGE_ENABLED=1`.
  It is always disabled when `APP_ENV=production`.
- Compose publishes `127.0.0.1:1456` for the new callback. If using an override with
  `ports: !override`, add `127.0.0.1:1456:1456` to the API ports there too.
- Open Console Settings → **Judge with your ChatGPT plan** → **Continue with ChatGPT**.
  Authorize ChatGPT plan use. Identity-only consent is retained but cannot judge.
- Load account models, select the exact desired model, and click **Use ChatGPT model
  for ASSERT reviews**. Reload Eval/run analysis to refresh its readiness.
- Switching accounts keeps ChatGPT as the chosen billing mode. If the new account
  has no saved judge model, judging is blocked until one is selected; it never falls
  back to a paid API-key provider. **Restore deployment judge** is the explicit opt-out.
- Turning off the local feature or changing to production mode preserves a saved
  ChatGPT billing choice and blocks reviews; it does not select an API-key judge.
  Restore the deployment judge explicitly while local controls are enabled before
  disabling them if you intend to change the judging provider.

This is deployment-local single-operator configuration. The custom control header,
loopback Host/Origin checks and disabled-by-default flag are defense in depth, **not
multi-tenant authentication**. Do not expose this local control plane through a public
reverse proxy, including one that rewrites Host/Origin to loopback. Hosted usage needs
its own authenticated user-scoped credential/control plane and OpenAI approval where
required; toggling this flag is not a hosted OAuth implementation.

## Security and inference contract

The protected, gitignored `.local/chatgpt-plan.json` file stores separate registrations,
tokens, the selected account/model, and a stable host ID. Atomic writes use private
file permissions; cross-process locking protects token rotation. On Windows also
restrict the directory's ACL to the local operator. Do not copy this file, its lock,
or temporary files into images, logs, reports or shared artifacts.

The browser callback is `http://127.0.0.1:1456/auth/callback`, not `localhost`.
The OAuth client is dynamically issued for ConVoice QA; it is not the legacy Codex
client ID. Reauthorization keeps the issued client ID and verified subject binding.
Failed first-time registrations are not saved or reused. Add account always starts
a fresh dynamic registration; the active account changes only after validation.
Confirmed terminal refresh errors clear unusable tokens but retain the registration
and billing choice for reconnect. Temporary network/server failures keep credentials.
Malformed credential fields block usage with a storage-repair error, never API fallback.
Disconnect clears selected-account tokens but retains registration/host identity.
If remote revocation cannot be confirmed, the UI reports that and directs the user
to disconnect in ChatGPT Settings.

Inference uses only `https://api.openai.com/v1/responses` with `store=false`,
`stream=true`, text evidence and the upstream ASSERT structured-output schema.
Unsupported plan fields (including output-token caps and temperature) are omitted
and not claimed as effective grader settings. Only `response.completed` with a
completed response and judge text is accepted. Failed, refused, incomplete, malformed,
or interrupted responses are evaluator errors and do not replace the recorded agent
result. Plan-limit or ambiguous transport failures are not automatically retried or
rerouted to another provider. ASSERT's own schema-repair calls remain upstream behavior.

Provenance includes the transport version and a non-secret SHA-256 account/client/host
binding. Token rotation does not invalidate reviews; account/workspace/model changes
do. The child process receives an expected binding, not an OAuth token in CLI args,
YAML, environment, or score artifacts; the transport reads the protected local store.
Existing application review-credit/concurrency limits still apply independently of
ChatGPT account limits. No claim is made about dollar cost or unlimited plan usage.

## Official references

- [Registration and consent](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts, refresh and revocation](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and completed inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Preview request limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)

These OpenAI Docs requirements are the integration baseline, not a guarantee of
eligibility for every account. A legacy Codex OAuth token is not automatically a
ChatGPT-plan Responses grant and must not be relabeled as one.
