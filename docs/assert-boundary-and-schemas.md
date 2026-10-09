# ASSERT Boundary and Schemas

ConversationAgentEvals uses ASSERT 0.3 specifications, taxonomy, transcript, and score conventions across its evaluation workflows. CAE has two explicit responsibilities: its in-process deterministic evaluator and the opt-in ASSERT 0.3 semantic judge. There is no synthetic ASSERT service or compatibility runtime for earlier ASSERT releases.

## Canonical code

- Schema models: `apps/api/app/schemas/assert_contracts.py`
- Boundary and lifecycle helpers: `apps/api/app/services/assert_boundary.py`
- Sole `assert-ai` import boundary: `apps/api/app/integrations/assert_runtime.py`
- Primary local benchmark runtime: `apps/api/app/services/benchmark_service.py`
- Target execution and evidence capture: `apps/api/app/services/execution_runner.py`
- Upstream semantic judge: `apps/api/app/services/upstream_assert_judge.py`
- Artifact persistence: `apps/api/app/services/assert_artifact_store.py`
- Boundary tests: `apps/api/tests/test_assert_boundary.py`

## 1. Primary CAE evaluation runtime

The checked-in benchmark and execution paths run inside ConversationAgentEvals:

- `/api/benchmarks/...` loads the selected scenario contract and evaluates submitted evidence;
- `/api/execution/runs` executes a configured target or replay path, normalizes current-run evidence, and invokes the same local benchmark evaluation;
- CAE produces the deterministic score, verdict, findings, ASSERT-compatible manifests, persistence records, reports, and exports.

This is the default runnable product. Its invocation target is `in_process`; it does not require or emulate an external ASSERT service.

## 2. ASSERT 0.3 semantic judge

Completed execution conversations can be reviewed through the separately mounted endpoint:

```text
POST /api/assert/runs/{execution_run_id}/conversations/{conversation_id}/judge
```

When `ASSERT_UPSTREAM_JUDGE_ENABLED=1` and provider credentials are configured, CAE converts the persisted conversation into ASSERT transcript and taxonomy inputs, invokes the pinned `assert-ai==0.3.0` judge stage, validates the returned score contract, and stores the result as a pending semantic review. API startup fails if a different ASSERT version is installed.

The spec API exposes the behavior and judge-preset libraries shipped by that installed version under `/api/specs/assert-library/behaviors` and `/api/specs/assert-library/judges`; CAE does not copy or fork the preset definitions.
The evaluation-design editor consumes those endpoints directly. Its behavior preset, judge preset, N/A, disabled built-in dimension, and ordinal scale controls compile through the same validated API model. Ordinal scale grade identifiers are strings because JSON object keys cannot retain numeric key types.

This path does not execute the target, replace CAE's deterministic verdict, or manufacture missing action/final-state evidence. Uploaded/benchmark reviews delegate to this same service through `/api/assert/benchmarks/{run_id}/judge`. The legacy product judge has been removed; evaluator failures are surfaced without another model/provider fallback. See [upstream-assert-judge.md](upstream-assert-judge.md).

## Ownership boundary

ConversationAgentEvals owns:

- target, tester, and executor configuration;
- text, WebRTC, voice, and replay execution paths;
- evidence capture and normalization;
- scenario-contract selection and deterministic scoring;
- product metadata, lineage, retention, labels, and cost controls;
- persistence, history, reports, comparisons, and exports.

The ASSERT 0.3 package owns, only when the semantic judge path is invoked:

- its judge-stage semantics and model invocation;
- upstream taxonomy and score-file conventions;
- the semantic dimensions and behavior-node judgments it returns.

ASSERT-compatible contracts remain the portability boundary between those concerns.

## Contracts

`AssertSpecRef` identifies an ASSERT-compatible scenario or suite and requires a stable spec version or hash.

`AssertEvidenceInput` accepts transcript, conversation, vCon, call media, action trace, final state, ASSERT bundle, and additional artifact pointers.

`AssertRuntimeConfig` records the in-process invocation, execution mode, retry policy, scenario overrides, and environment labels for the boundary lifecycle.

`PlatformRunMetadata` carries wrapper-only data such as user, project, lineage, labels, retention, quota, and billing tags.

`AssertResultManifest` carries a verdict, failure taxonomy, artifacts, and summary exports. `PlatformRunRecord` and `PlatformSuiteRunRecord` add product lifecycle and ownership data without changing the embedded result contract.

## Practical rule

Use the normal benchmark or execution endpoints for deterministic product evaluation. Use the ASSERT judge endpoint for an explicit semantic second opinion over completed CAE evidence. Do not add version shims or a second ASSERT-shaped runtime; new integration work belongs in the central ASSERT 0.3 boundary.
