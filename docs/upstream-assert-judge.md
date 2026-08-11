# Upstream ASSERT judging for CAE execution evidence

ConversationAgentEvals owns target execution and evidence capture. The upstream ASSERT package can optionally provide the semantic judgment over that evidence without taking over SIP, PSTN, WebRTC, Pipecat, vendor SDK, or media orchestration.

## Boundary

```text
CAE tester / external voice transport
  -> external or built-in target
  -> CAE turns, transcript, action trace, final state, voice metadata
  -> CAE deterministic checks
  -> ASSERT inference_set.jsonl adapter
  -> assert-ai judge stage
  -> scores.jsonl
  -> CAE pending review
```

CAE deterministic evidence remains authoritative. The ASSERT result is a semantic review and cannot manufacture proof that an external action occurred.

## Enable

Install the API requirements, which pin `assert-ai`, and set:

```bash
ASSERT_UPSTREAM_JUDGE_ENABLED=1
ASSERT_JUDGE_MODEL=openai/gpt-4.1-mini
OPENAI_API_KEY=...
```

For the OpenAI API-key fallback already used by CAE, `LLM_JUDGE_API_KEY` is copied to `OPENAI_API_KEY` for the ASSERT subprocess when `OPENAI_API_KEY` is not set.

Optional controls:

```bash
ASSERT_JUDGE_MAX_TOKENS=8000
ASSERT_JUDGE_TIMEOUT_SECONDS=300
```

The current CAE Codex OAuth session is not automatically forwarded into LiteLLM. Use a provider credential supported by ASSERT for this initial integration.

## Run-analysis UI integration

The existing run-analysis **Review with LLM judge** action now routes completed execution conversations through the upstream ASSERT endpoint:

```text
POST /api/assert/runs/<execution-run-id>/conversations/<conversation-id>/judge
```

The browser sends only the run owner identifier. The server reloads the persisted conversation and constructs the ASSERT transcript, taxonomy, and judge configuration from trusted run evidence.

The legacy endpoint remains available for standalone report or transcript reviews that are not attached to an execution conversation:

```text
POST /api/product/judge
```

Therefore the product boundary is explicit:

- execution-conversation button -> upstream ASSERT judge;
- standalone report/transcript review -> legacy CAE product judge;
- deterministic execution, final-state, media, and voice checks -> CAE.

## Run against a completed conversation directly

```bash
curl -X POST \
  http://127.0.0.1:8000/api/assert/runs/<execution-run-id>/conversations/<conversation-id>/judge \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "<run-owner>",
    "model_name": "openai/gpt-4.1-mini",
    "judge_n": 1
  }'
```

The endpoint rejects active runs and conversations. It executes the existing command:

```bash
assert-ai run --config <judge-only.yaml> --force-stage judge --output json
```

## Evidence mapping

- CAE caller/tester turns become ASSERT `user` events.
- CAE target/agent turns become ASSERT `assistant` events.
- Action trace entries become ASSERT tool-call events.
- The CAE final state becomes a `cae_final_state_snapshot` tool event.
- Voice metadata, source text, and ASR receipts remain attached as raw event evidence.
- Text-only external targets are supported; their evidence level is marked `black_box`.
- Runs with action or final-state evidence are marked `partial_structured` or `gray_box`.

The adapter evaluates the text actually recorded by CAE. For external voice agents, this should normally be the transcript observed at the media boundary rather than an assumed internal agent transcript.

## Artifacts

Each invocation writes an immutable fingerprinted directory beneath:

```text
artifacts/execution-runs/<run-id>/assert/<conversation-id>/<fingerprint>/
```

It contains:

```text
judge-only.yaml
results/<suite>/<fingerprint>/inference_set.jsonl
results/<suite>/taxonomy.json
results/<suite>/<fingerprint>/scores.jsonl
```

The upstream result is also stored as a pending CAE judge review. Applying that review continues to use CAE's existing confirmation flow and does not replace the original deterministic evidence.

## Initial limitations

- The run UI uses the ASSERT path for execution conversations, but still presents the shared LLM-review result component. A later UX slice can expose ASSERT dimensions, behavior-node judgments, and artifact links more directly.
- The taxonomy is compiled from the active CAE scenario contract. A later slice should use the approved, versioned editable ASSERT spec directly.
- OpenTelemetry/OpenInference trace import remains a separate future path. Structured CAE action and final-state evidence are mapped directly for now.
- Automatic judgment for every run is intentionally not enabled because it incurs model cost and requires provider credentials.
