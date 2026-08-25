# ASSERT Boundary and Schemas

ConversationAgentEvals uses ASSERT-compatible specifications, evidence, taxonomy, and artifact conventions across its evaluation workflows. The repository currently has three related but distinct paths: the primary CAE benchmark runtime, a development-only synthetic sidecar, and an optional upstream `assert-ai` semantic judge. They must not be described as one interchangeable runtime.

## Canonical code

- Schema models: `apps/api/app/schemas/assert_contracts.py`
- Boundary and lifecycle helpers: `apps/api/app/services/assert_boundary.py`
- Primary local benchmark runtime: `apps/api/app/services/benchmark_service.py`
- Target execution and evidence capture: `apps/api/app/services/execution_runner.py`
- Development sidecar route: `apps/api/app/routes/assert_sidecar.py`
- Synthetic sidecar implementation: `apps/api/app/services/assert_sidecar.py`
- Upstream semantic judge: `apps/api/app/services/upstream_assert_judge.py`
- Artifact persistence: `apps/api/app/services/assert_artifact_store.py`
- Queue lifecycle: `apps/api/app/services/assert_queue_lifecycle.py`
- Boundary tests: `apps/api/tests/test_assert_boundary.py`

## 1. Primary CAE evaluation runtime

The checked-in benchmark and execution paths run inside ConversationAgentEvals:

- `/api/benchmarks/...` loads the selected scenario contract and evaluates submitted evidence;
- `/api/execution/runs` executes a configured target or replay path, normalizes current-run evidence, and invokes the same local benchmark evaluation;
- CAE produces the deterministic score, verdict, findings, ASSERT-compatible manifests, persistence records, reports, and exports.

This is the default runnable product. It does not require an external ASSERT service.

## 2. Development-only synthetic sidecar

The local sidecar exposes:

```text
POST /api/assert/runs
GET /api/assert/runs/{platform_run_id}
```

It accepts `AssertRunCreateRequest`, exercises the queue/ingestion and artifact contracts, and returns a synthetic local manifest. Its verdict reflects input-artifact readiness: complete input is accepted, while explicitly missing input produces `needs_review`. It does not execute an upstream ASSERT scenario or semantic judge and must not be presented as an external evaluator.

The sidecar lifecycle is enabled by default only in development-like environments. It is disabled on Cloud Run and when `APP_ENV` is production unless the mounting behavior is changed in code; `ASSERT_LOCAL_SIDECAR_ENABLED` controls the local development route where applicable.

Synthetic sidecar manifests use locations such as:

```text
local-artifact://assert-sidecar/runs/{platform_run_id}/manifest.json
```

## 3. Optional upstream semantic judge

Completed execution conversations can be reviewed through the separately mounted endpoint:

```text
POST /api/assert/runs/{execution_run_id}/conversations/{conversation_id}/judge
```

When `ASSERT_UPSTREAM_JUDGE_ENABLED=1` and provider credentials are configured, CAE converts the persisted conversation into ASSERT transcript and taxonomy inputs, invokes the pinned `assert-ai` judge stage, validates the returned score contract, and stores the result as a pending semantic review.

This path does not execute the target, replace CAE's deterministic verdict, or manufacture missing action/final-state evidence. There is no silent fallback to the standalone CAE product judge when upstream ASSERT judging fails. See [upstream-assert-judge.md](upstream-assert-judge.md).

## Ownership boundary

ConversationAgentEvals owns:

- target, tester, and executor configuration;
- text, WebRTC, voice, and replay execution paths;
- evidence capture and normalization;
- scenario-contract selection and deterministic scoring;
- product metadata, lineage, retention, labels, and cost controls;
- persistence, history, reports, comparisons, and exports.

The upstream ASSERT package owns, only when the optional judge path is invoked:

- its judge-stage semantics and model invocation;
- upstream taxonomy and score-file conventions;
- the semantic dimensions and behavior-node judgments it returns.

ASSERT-compatible contracts remain the portability boundary between those concerns.

## Contracts

`AssertSpecRef` identifies an ASSERT-compatible scenario or suite and requires a stable spec version or hash.

`AssertEvidenceInput` accepts transcript, conversation, vCon, call media, action trace, final state, ASSERT bundle, and additional artifact pointers.

`AssertRuntimeConfig` describes invocation, execution mode, retry policy, scenario overrides, and environment labels for the boundary lifecycle.

`PlatformRunMetadata` carries wrapper-only data such as user, project, lineage, labels, retention, quota, and billing tags.

`AssertResultManifest` carries a verdict, failure taxonomy, artifacts, and summary exports. `PlatformRunRecord` and `PlatformSuiteRunRecord` add product lifecycle and ownership data without changing the embedded result contract.

## Practical rule

Use the normal benchmark or execution endpoints for product evaluation. Use the local sidecar only to exercise ASSERT-shaped lifecycle and ingestion locally. Use the upstream judge endpoint only for an explicit semantic second opinion over completed CAE evidence.
