# ASSERT v2 Boundary And Schemas

## Status

- Date: 2026-06-17
- Scope: `ConversationAgentEvals` issue #73 phase 1
- Depends on: [assert-v2-decision-and-deletion-inventory.md](./assert-v2-decision-and-deletion-inventory.md)
- Code anchors:
  - `apps/api/app/schemas/assert_v2.py`
  - `apps/api/app/services/assert_v2_boundary.py`

## Decision

`ConversationAgentEvals` v2 will invoke ASSERT through one server-side boundary only:

- `create_assert_run(spec_ref, evidence, runtime_config, platform_metadata)`
- `create_assert_suite_run(spec_ref, scenarios, runtime_config, platform_metadata)`
- `ingest_assert_result(platform_run_id, assert_run_id, result_manifest)`

The API, workers, stores, and web UI should all speak the platform-side v2 schemas in `apps/api/app/schemas/assert_v2.py`. No route or worker should directly call the legacy local evaluator functions after the cutover.

## Boundary Shape

The boundary is defined in `apps/api/app/services/assert_v2_boundary.py`.

Responsibilities kept on the platform side:

- authenticate the caller and resolve project ownership
- normalize evidence into a manifest of transcript, vCon, audio, tool trace, and final-state artifacts
- attach wrapper metadata such as retention, labels, project lineage, and retry ancestry
- queue or persist the platform-facing run record
- ingest ASSERT result artifacts into operator views, exports, and history indexes

Responsibilities delegated to ASSERT:

- spec loading and version resolution
- scenario execution semantics
- judging/scoring and verdict production
- failure taxonomy generation
- result-manifest creation for canonical benchmark truth

## Integration Shape

The integration shape is intentionally singular:

- Transport: `http_sidecar`
- Local development: API talks to a local ASSERT sidecar at `http://127.0.0.1:8091`
- Production: API and workers talk to an ASSERT sidecar/service at `http://assert-sidecar:8091`
- Package ownership: ASSERT remains the package that owns execution logic; `ConversationAgentEvals` does not embed a fallback evaluator or deterministic scorer

Why this shape:

- it avoids a second code path for local CLI execution versus production service execution
- it keeps the boundary explicit and testable from API code
- it allows worker retry/cancellation logic to remain platform concerns while ASSERT owns run semantics

Non-decisions ruled out:

- no direct API calls into `eval_service.py`
- no route-specific CLI wrapper for local-only execution
- no long-lived dual mode with a repo-local deterministic evaluator fallback

## v2 Contracts

### Spec reference

`AssertSpecRef`

- `spec_id`: canonical ASSERT suite or scenario id
- `spec_kind`: `scenario` or `suite`
- `spec_version` or `spec_hash`: required to make run lineage stable
- `assert_project` and `assert_commit`: optional provenance for exact spec source

### Evidence ingress

`AssertEvidenceInput`

- first-class slots for `transcript`, `conversation`, `vcon`, `call_media`, `action_trace`, `final_state`, and `assert_bundle`
- artifact pointers can be inline or URI-backed
- provenance stays attached as metadata, but does not redefine the canonical result format

`AssertArtifactPointer`

- normalizes input, output, and derived artifacts into one manifest shape
- carries `artifact_id`, `kind`, `role`, location, readiness, and metadata
- is the shared primitive for storage pointers, exports, and audit views

### Runtime

`AssertRuntimeConfig`

- `execution_mode`: `sync`, `async`, or `batch`
- `invocation_target`: always an ASSERT `http_sidecar`
- `worker_queue`, `retry_policy`, `scenario_overrides`, and `environment_labels`

This separates platform scheduling concerns from ASSERT’s evaluation semantics.

### Platform metadata and lineage

`PlatformRunMetadata`

- `user_id` and `project_id`
- `root_run_id`, `retry_parent_run_id`, and `resume_parent_run_id`
- `labels`, `notes`, retention, quota, and billing tags

This is where v2 intentionally keeps wrapper metadata instead of leaking it into ASSERT’s scoring schema.

### Run and suite records

`PlatformRunRecord`

- `platform_run_id`: platform-owned durable id
- `assert_run_id`: ASSERT-owned execution id
- `spec_ref`, `runtime_config`, `platform_metadata`
- `status`, timestamps, `verdict`, `failure_taxonomy`, `artifact_manifest`, and `audit_artifacts`

`PlatformSuiteRunRecord`

- suite-level wrapper record for a parent ASSERT suite run or a platform-managed fanout
- contains child `PlatformSuiteScenarioRef` entries so suite history can remain queryable without reconstructing lineage from opaque blobs

### Result ingest

`AssertResultManifest`

- `verdict`: pass/fail/needs-review/error plus score and metrics
- `failures`: canonical ASSERT failure taxonomy items
- `artifacts`: canonical output artifacts from ASSERT
- `summary_artifacts`: exports or derived records ready for operator download

`AuditArtifactView`

- platform-facing summary of artifact readiness and export pointers
- used by audit/history endpoints after ASSERT result ingestion

## Route Cutover

These are the legacy direct execution entrypoints named in the boundary module and scheduled for removal from production flow:

- `app.routes.evals.run_voice_eval`
- `app.services.eval_service.run_eval`
- `app.services.benchmark_service.run_scenario`
- `app.services.benchmark_service.run_suite`
- `app.services.benchmark_service.simulate_scenario`
- `app.services.benchmark_service.simulate_suite`

The replacement entrypoints are:

- `create_assert_run(spec_ref, evidence, runtime_config, platform_metadata)`
- `create_assert_suite_run(spec_ref, scenarios, runtime_config, platform_metadata)`
- `ingest_assert_result(platform_run_id, assert_run_id, result_manifest)`

## API Surface Changes Required In The Next Card

The next implementation card should make these route-level changes:

1. Replace `/api/evals/run` with an ASSERT-backed run-creation surface or remove it entirely.
2. Change benchmark run creation endpoints so they validate into `AssertRunCreateRequest` or `AssertSuiteRunCreateRequest` before any worker logic runs.
3. Make stores persist `platform_run_id` plus `assert_run_id` and manifests instead of deterministic report JSON as the primary truth.
4. Make audit/export endpoints read from `AuditArtifactView` and `AssertResultManifest` derivatives.

## Data Contract Breaks

v2 should intentionally stop promising these legacy fields as canonical:

- `overall_score`
- `rubric_score`
- `completed_actions`
- `missing_actions`
- `hard_check_failures`
- `evaluator_version`
- local `run_id` derivation from transcript inputs

Instead, consumers should read:

- `verdict.status`
- `verdict.score`
- `failure_taxonomy[]`
- `artifact_manifest[]`
- `platform_run_id` and `assert_run_id`

## Why The Next Card Can Move To Ready

This card leaves the repo with a concrete single-boundary definition rather than only prose:

- boundary service definition in `apps/api/app/services/assert_v2_boundary.py`
- v2 schema models in `apps/api/app/schemas/assert_v2.py`
- tests covering validation, queued run creation, suite fanout shape, result ingest, and the enforced sidecar transport in `apps/api/tests/test_assert_v2_boundary.py`

The next ready card should be the first implementation cutover: wire benchmark run creation and persistence to the new ASSERT v2 boundary, then start deleting the legacy evaluator entrypoints named above.
