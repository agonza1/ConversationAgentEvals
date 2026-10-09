# Single ASSERT semantic-review pipeline

CAE uses `assert-ai==0.3.0` for all LLM judging. The application retains evidence, executes deterministic checks, authorizes access, limits spend, saves reviews, and renders results. The upstream judge-only CLI remains the sole semantic engine; there is no legacy prompt/completion fallback.

## Upload and live-test workflows

1. Upload a vCon/transcript or run a configured agent. Imported evidence is normalized without generating tool receipts or final state from spoken claims.
2. **Evaluate** runs CAE's local checks and persists the full normalized evidence, automatic findings, and a frozen approved scenario contract. It does not call an LLM judge.
3. **LLM Judge** on uploaded/benchmark evidence, or **Review with LLM judge** on a completed live test, invokes the same saved-conversation ASSERT review service. Uploaded evidence is represented as an explicitly labelled evidence replay, never a live agent execution.
4. Review the saved semantic assessment alongside unchanged automatic evidence. An explicit confirmation is required to apply its proposal. Read/export operations never invoke a judge.

The upload button submits only the persisted benchmark ID and owner identity, not a browser-provided report or verdict. Its result links to the common run-detail page for history, confirmation and HTML export. New evaluations must retain complete evidence; an old 700-character transcript preview is not a substitute for the original.

## Configuration and readiness

```bash
ASSERT_UPSTREAM_JUDGE_ENABLED=1
ASSERT_JUDGE_MODEL=openai/gpt-4.1-mini
ASSERT_JUDGE_ALLOWED_MODELS=openai/gpt-4.1-mini
OPENAI_API_KEY=<operator-configured-key>
# LLM_JUDGE_API_KEY is also accepted as an OpenAI API-key alias.
ASSERT_JUDGE_MAX_N=1
ASSERT_JUDGE_MAX_CONCURRENT=2
ASSERT_JUDGE_MAX_TOKENS=8000
ASSERT_JUDGE_TIMEOUT_SECONDS=300
LLM_JUDGE_DAILY_CREDIT_LIMIT=200
LLM_JUDGE_RESERVED_DAILY_CREDITS=0
```

`GET /api/assert/readiness` returns the common read-only preflight used by the UI and product configuration: enabled state, pinned runtime, allowed configured model, provider credential configuration, available process slots, and application-credit budget. It neither reserves credits nor contacts a model. Ready means configured, not that a provider availability/credential probe succeeded. Non-OpenAI models use the pinned LiteLLM environment validator, including supported local providers. Configure their provider-specific environment variables explicitly.

Codex OAuth connectivity is **not** judge readiness. OAuth remains available for compatible target/authoring features and is never forwarded to ASSERT. There is no automatic model/provider substitution. The old `LLM_JUDGE_MODEL` and `LLM_JUDGE_PROVIDER` settings are unused.

## API

```text
POST /api/assert/benchmarks/<persisted-benchmark-run-id>/judge
POST /api/assert/runs/<execution-run-id>/conversations/<conversation-id>/judge
```

Both accept the same strict body:

```json
{"user_id":"<owner>","model_name":"openai/gpt-4.1-mini","judge_n":1}
```

`model_name` and `judge_n` are optional; the default is one judge. `request_id` is optional safe text of 8–128 characters. The upload route reloads an authorized benchmark and adapts its evidence before calling the same execution-review service. Active conversations or missing deterministic verdicts are rejected. Ownership and exact personal/workspace-project visibility are checked before saved results are looked up.

HTTP 429 indicates budget/concurrency admission; 503 indicates disabled/unconfigured judging or an output-retention outage; 502 indicates an evaluator/provider or score-validation failure. These are evaluator states, not evidence of agent failure. HTTP 409 covers stale/missing provenance, conflicting request identity, or an already-running identical review. Unexpected report/transcript/plan fields are rejected with 422. The retired `/api/product/judge` route returns 404.

## Evidence, applicability and aggregation

Programmatic results expose `outcome`: `pass`, `fail`, `not_observable`, or `not_applicable`. Existing `status` values remain for report compatibility. Explicit check inapplicability comes from the approved contract; native conditional-event checks are scoped to triggers in the recorded trace, not unseen activity.

A tool explicitly returning failure is different from having no tool telemetry. Missing/partial observations block verification where the contract requires them, but do not prove an action failed or a caller-facing claim was false. `unverified_operational_outcome` represents an unverified required/claimed result; `unsupported_operational_claim` covers claims contradicted by recorded evidence. Transcript-only uploads can still be judged for communication and policy meaning.

Automatic keyword/phrase scores remain labelled diagnostics (`heuristic_verdict`), not semantic proof. Required semantic behaviors keep the overall automatic verdict at `needs_review` until reviewed. An exact authored `transcript_contains` check verifies literal text only. Order is checked only when a contract explicitly declares `required_order` or a structured event-order check; a list of actions is not an implicit script.

Executable hard failures and blocked required evidence cannot be upgraded to pass by a judge proposal or confirmed application. Unknowns are not averaged into perfect scores. Raw local findings and raw ASSERT scores are retained independently; a confirmed adjudication is an overlay. Judge outages never overwrite an agent's recorded result.

ASSERT inputs retain caller/assistant turns, original action events, actual state snapshots, ASR receipts and available voice metadata. Explicit event anchors are honored; unanchored actions are not assigned invented timestamps. Rubrics identify transcripts/tool output as untrusted evidence, not evaluator instructions. These safeguards and fixtures are not a proof of immunity to prompt injection.

A successful CLI exit is insufficient: CAE checks the matching conversation ID, `judge_status`, declared dimensions, strict booleans, allowed ordinal/N/A values, justifications, exact taxonomy-node coverage, narrative and JSON structure before retaining a successful result.

## Frozen contracts and versioned identity

Every new evaluation retains `evaluation_contract_snapshot`: schema version, spec identity, exact contract, resolved preset contents and a SHA-256 digest. Judging, freshness, export and confirmed apply use this snapshot, never the latest mutable catalog. A new contract produces a distinct evaluation ID; old reviews do not silently change meaning.

Fingerprint v3 is a full SHA-256 over normalized inference evidence, taxonomy and target context plus the actual judge configuration: ASSERT version, adapter/aggregation versions, custom and built-in dimensions, configured model, judge count, generation settings, timeout, and a stable SHA-256 digest of provider routing environment values. Raw provider URLs, userinfo, query credentials, private hostnames, cloud project names, and regions are never copied into review provenance; the digest still detects changes to those settings. Previously saved v2 reviews are not automatically rewritten and should be treated as sensitive if they captured raw routing values. Saved reviews explicitly store judge count and configuration; the system does not guess counts for new records. Provider model aliases can still change behind a stable name: choose dated model revisions where supported and re-calibrate when upgrading.

Historical reviews lacking this provenance remain historical/read-only. They are not silently upgraded or treated as current. Re-evaluate the original complete evidence against an explicitly selected approved contract to create a new evaluation.

## Idempotency, artifacts and limits

The database table `assert_judge_requests` stores unique owner/project/source-scoped admission keys. A normal repeated request reuses successful results for the identical full input/configuration fingerprint. Simultaneous duplicates receive 409 rather than starting another model call. The UI uses a new `request_id` for **Run a new ASSERT review**, retaining that identity across retries of that explicit sample. Reusing an ID with changed inputs is a conflict.

After a successful judge, raw output is retained before review/audit finalization. Retrying a finalization failure reuses that output, stable review ID and idempotent audit ID. Failed evaluator requests are retryable; they do not permanently poison the cache. If successful output cannot be retained, the request stays reserved rather than automatically reissuing a potentially billable call. Abandoned running requests are never automatically stolen: inspect the provider/storage state before choosing a new sample.

Each actual invocation retains `judge-only.yaml`, `taxonomy.json`, `inference_set.jsonl`, and `scores.jsonl` under a fingerprint-plus-invocation directory. Review provenance includes artifact pointers, score digest, model, versions and normalized/underlying judgments. The existing SQLAlchemy initialization creates the new admission table; deployments that manage schema separately must include it before enabling judging.

**Deployment limits:** database admission coordinates duplicate requests, but the existing conversation/artifact store and application-credit counter are file-backed with process-local locking. Use a single API writer with durable storage for this implementation. This is not a multi-replica queue, distributed spend limiter, or exactly-once guarantee for an external provider charge. Application credits are estimates, not token-level provider billing; a failed/timeout call may still be billable even when application credits are refunded. No new queue/Redis service or automatic multi-judge fanout is introduced. Run-level access continues to use CAE's existing identity model; localhost OAuth is not production multi-tenant API authentication.

Use one judge initially, execute only the semantic checks the contract calls for, and include successful cases in audit samples. Do not call this migration an accuracy improvement without independent human calibration. See [judge calibration](judge-calibration.md).

## Saved review export

```text
GET /api/assert/runs/<run>/conversations/<conversation>/reviews/<review>/status?user_id=<owner>
GET /api/assert/runs/<run>/conversations/<conversation>/reviews/<review>/report.html?user_id=<owner>
POST /api/execution/runs/<run>/conversations/<conversation>/judge-reviews/<review>/apply
```

Apply requires `{"user_id":"<owner>","confirm":true}`. Freshness and export fail closed on changed evidence, tampered contracts or changed grader configuration. Export uses saved data, never reads arbitrary internal artifact paths or starts a model. Offline HTML escapes dynamic content and separates the automatic verdict from adjudication; audio stays in CAE. Existing [export specifications](specs/assert-html-report-export.md) describe the rendering boundary.
