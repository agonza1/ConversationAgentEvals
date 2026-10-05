# Upstream ASSERT judging for CAE execution evidence

ConversationAgentEvals owns target execution and evidence capture. The sole supported semantic runtime is `assert-ai==0.3.0`; it can optionally judge that evidence without taking over SIP, PSTN, WebRTC, Pipecat, vendor SDK, or media orchestration. CAE refuses to start with another ASSERT version installed.

## Boundary

```text
CAE tester / external voice transport
  -> external or built-in target
  -> CAE turns, transcript, action trace, final state, voice metadata
  -> CAE deterministic checks
  -> ASSERT inference_set.jsonl adapter
  -> assert-ai judge stage
  -> validated scores.jsonl
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

The current CAE Codex OAuth session is not automatically forwarded into LiteLLM. OpenAI-backed ASSERT judging therefore requires `OPENAI_API_KEY` or `LLM_JUDGE_API_KEY`.

## Cost and request controls

ASSERT uses the same process-level daily judge-credit ledger as the existing CAE product judge. A successful single judgment reserves 10 credits; `judge_n` multiplies that amount. Credits are refunded when the ASSERT subprocess, score parsing, or score-contract validation fails.

```bash
LLM_JUDGE_DAILY_CREDIT_LIMIT=200
LLM_JUDGE_RESERVED_DAILY_CREDITS=0
ASSERT_JUDGE_MAX_N=1
ASSERT_JUDGE_MAX_CONCURRENT=2
```

Direct model overrides are denied unless the model is explicitly allowed:

```bash
ASSERT_JUDGE_ALLOWED_MODELS=openai/gpt-4.1-mini,openai/gpt-4.1
```

The configured `ASSERT_JUDGE_MODEL` is always allowed. Concurrency is enforced per API process; deployment-level worker limits should still be configured when multiple API processes run in parallel.

Optional execution controls:

```bash
ASSERT_JUDGE_MAX_TOKENS=8000
ASSERT_JUDGE_TIMEOUT_SECONDS=300
```

## Run-analysis UI integration

The existing run-analysis **Review with LLM judge** action routes completed execution conversations through the upstream ASSERT endpoint:

```text
POST /api/assert/runs/<execution-run-id>/conversations/<conversation-id>/judge
```

The browser sends only the run owner identifier. The server reloads the persisted conversation and constructs the ASSERT transcript, taxonomy, and judge configuration from trusted run evidence.

Successful ASSERT review requests also write the existing `judge.requested` product audit event, including the trusted project, provider, model, credit, status, and agreement metadata.

The legacy endpoint remains available for standalone report or transcript reviews that are not attached to an execution conversation:

```text
POST /api/product/judge
```

Therefore the product boundary is explicit:

- execution-conversation button -> upstream ASSERT judge;
- standalone report/transcript review -> legacy CAE product judge;
- deterministic execution, final-state, media, and voice checks -> CAE.

There is no silent fallback from ASSERT to the legacy judge for execution conversations. Missing configuration or an ASSERT failure is surfaced rather than changing judge semantics without notice.

The dedicated routing test and the existing run-analysis Playwright specifications all intercept the ASSERT endpoint and verify that the browser submits only `user_id` for execution-conversation reviews.

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

The endpoint rejects active runs and conversations, as well as conversations that do not have a deterministic verdict. It executes the existing command:

```bash
assert-ai run --config <judge-only.yaml> --force-stage judge --output json
```

Busy and exhausted-budget requests return HTTP 429. Disabled or unconfigured providers return HTTP 503. ASSERT execution or score-contract failures return HTTP 502.

## Evidence mapping

- CAE caller/tester turns become ASSERT `user` events.
- CAE target/agent turns become ASSERT `assistant` events.
- Action trace entries become ASSERT tool-call events.
- Actions with explicit `before_turn_index`, `after_turn_index`, `turn_index`, or exchange anchors are interleaved with messages.
- Unanchored actions are retained after the conversation messages rather than assigned an invented chronology.
- The CAE final state becomes a `cae_final_state_snapshot` tool event.
- Voice metadata, source text, and ASR receipts remain attached as raw event evidence.
- Text-only external targets are supported; their evidence level is marked `black_box`.
- Runs with action or final-state evidence are marked `partial_structured` or `gray_box`.

The adapter evaluates the text actually recorded by CAE. For external voice agents, this should normally be the transcript observed at the media boundary rather than an assumed internal agent transcript.

## Score acceptance boundary

A zero exit code from `assert-ai` is not sufficient. CAE accepts a semantic result only when:

- exactly one score row matches the requested conversation;
- the raw and inferred `judge_status` are both `ok`;
- every required built-in and CAE custom dimension is present;
- boolean dimensions are strict booleans;
- ordinal dimensions use a value declared by their configured scale;
- `not_applicable` is accepted only for dimensions that explicitly allow it and carry an ASSERT 0.3 N/A score key;
- all dimension justifications are present;
- the node-judgment set covers every generated taxonomy behavior;
- every returned node judgment references a real taxonomy behavior and has valid fields;
- the narrative and score JSON are structurally valid.

Rows marked `judge_failed`, `filter_skipped`, or `scoring_skipped` are rejected and are never persisted as successful reviews.

## Artifacts and persisted provenance

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

The pending CAE review preserves ASSERT provenance inside `judge_result.provenance`, including:

- ASSERT version;
- input fingerprint;
- score SHA-256;
- artifact paths;
- score keys, N/A keys, and ordinal scales;
- validated dimensions;
- per-dimension applicability and justifications;
- behavior-node judgments.

Applying the review continues to use CAE's existing confirmation flow and does not replace the original deterministic evidence.

## Current limits

- The run UI exposes the ASSERT version, evidence level, dimension outcomes, and justifications. Artifact links and complete behavior-node drill-down remain future UX work.
- The taxonomy is compiled from the active CAE scenario contract. The evaluation-design editor discovers ASSERT 0.3 behavior and judge presets from the pinned runtime and exposes preset selection, N/A policy, built-in-dimension disabling, and ordinal-scale controls; execution binding to a separately approved spec version remains future work.
- Editable-spec ordinal grades use non-empty string identifiers (for example `unresolved`, `partial`, and `resolved`). This is intentional because JSON object keys cannot preserve numeric key types across the web/API boundary.
- OpenTelemetry/OpenInference trace import remains a separate future path. Structured CAE action and final-state evidence are mapped directly for now.
- Automatic judgment for every run is intentionally not enabled because it incurs model cost and requires provider credentials.
- The process-local concurrency counter does not coordinate across multiple API replicas; production deployments should add a shared queue or distributed limiter when scaling horizontally.

## Portable HTML report

In a completed conversation's Resolution Evidence panel, select a saved ASSERT review and choose **Export ASSERT HTML report**. The selected review is downloaded; export never starts a judge or applies its proposal. The file works offline and pairs the semantic assessment with transcript and expandable tool/state evidence. It identifies CAE as the renderer, preserves the recorded ASSERT version/model, and displays the deterministic verdict separately from applied adjudication. Audio remains in CAE and citations without reliable anchors are shown as unresolved.

```text
GET /api/assert/runs/<execution-run-id>/conversations/<conversation-id>/reviews/<review-id>/report.html?user_id=<run-owner>
```

The endpoint requires ownership and current project visibility before reading conversation evidence. Missing or malformed reviews, active executions and changed judging inputs return 409. Wrong owners and inaccessible/ambiguous projects return a non-disclosing 404. Internal artifact files are never read; known credentials/internal paths are omitted and all dynamic content is escaped.

The [export spec](specs/assert-html-report-export.md) records the saved-data contract and limitations. Synthetic browser validation can be reproduced with `playwright test apps/web/tests/assert-html-report.spec.ts --config apps/web/playwright.config.ts` from the repository root after normal setup; it saves desktop/mobile screenshots and the downloaded report under the ignored `artifacts/assert-html-report-export/` directory.
