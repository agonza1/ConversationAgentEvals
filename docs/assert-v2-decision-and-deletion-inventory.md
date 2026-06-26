# ASSERT v2 Decision And Deletion Inventory

## Status

- Date: 2026-06-17
- Scope: `ConversationAgentEvals` issue #73 phase 0
- Decision: v2 is an ASSERT-first breaking migration. `ConversationAgentEvals` stops owning a legacy evaluator, dual runtime, or deterministic fallback scoring path.

This document replaces the earlier adapter-friendly direction captured in [docs/assert-refactor-direction.md](./assert-refactor-direction.md) and [docs/implementation-plan.md](./implementation-plan.md). Those documents assumed ASSERT could coexist with a long-lived local evaluator. That is not the v2 target.

## Baseline Decision

ASSERT becomes the only canonical home for:

- eval specs, rubrics, requirements, and scenario execution semantics
- runtime orchestration for benchmark/eval runs
- judging and scoring logic
- canonical result schema, verdicts, failure taxonomy, and artifact manifests

`ConversationAgentEvals` keeps the hosted wrapper around ASSERT:

- API surfaces for projects, suites, runs, reruns, exports, and audit views
- queueing, worker lifecycle, storage indexes, auth, quotas, and billing hooks
- evidence ingestion and normalization for transcript, vCon, voice, tool-trace, and final-state artifacts
- report packaging, operator summaries, regression UX, and artifact browsing

There is no supported dual runtime in v2. The existing local evaluator is a migration target for deletion, not a fallback to preserve.

## Current Code Boundaries

The repo currently mixes four different responsibilities:

1. Benchmark/spec catalog ownership in `apps/api/app/services/benchmark_service.py` via `_SUITES`, `_scenario_contract`, `get_suite_contract_manifest`, and `get_scenario_contract`.
2. Local eval execution and scoring in `apps/api/app/services/eval_service.py` and remaining migration-only benchmark simulation helpers. Production benchmark run creation now goes through the ASSERT v2 boundary and stores ASSERT artifacts as canonical results.
3. Platform persistence/export UX in `apps/api/app/services/benchmark_run_store.py`, `benchmark_suite_run_store.py`, `apps/api/app/routes/benchmarks.py`, and `apps/web/components/BenchmarkRunner.tsx`.
4. Evidence normalization and post-processing in `apps/api/app/services/assert_adapter.py` plus `benchmark_service.py` helpers for audit summaries, voice summaries, and vCon export packaging.

The v2 migration should separate those concerns instead of carrying them forward inside one service file.

## Inventory

### Replace with ASSERT core

| Current path | Current responsibility | v2 action |
| --- | --- | --- |
| `apps/api/app/routes/evals.py` | Deleted local evaluator endpoint that called `run_eval`. | Keep deleted; no second evaluator surface in v2. |
| `apps/api/app/services/eval_service.py` | Deleted transcript-first deterministic eval runtime. | Keep deleted; reusable evidence normalization belongs behind ASSERT ingress only. |
| `apps/api/app/services/benchmark_evaluator.py` | Deleted legacy local hard-check scorer. | Replaced in production by ASSERT v2 manifest-backed checks; shared trace parsing moved to `assert_trace.py`. |
| `apps/api/app/services/benchmark_service.py` `_SUITES`, `_scenario_contract`, `get_suite_contract_manifest`, `get_scenario_contract` | Repo-owned benchmark/spec catalog and contract manifest semantics. | Move canonical spec ownership into ASSERT. Keep only light platform-side metadata/lookups if needed. |
| `apps/api/app/services/benchmark_service.py` `run_scenario`, `run_suite`, `simulate_scenario`, `simulate_suite` | Local run orchestration, deterministic scoring, synthetic simulation, verdict assembly, and suite aggregation. | Replace with one ASSERT-only orchestration boundary used by routes, workers, and reruns. |
| `apps/api/app/schemas/evals.py` | Deleted local evaluator request/response schema centered on transcript + criteria + deterministic report. | ASSERT-first run/spec/artifact schemas are the supported contract. |
| `apps/api/app/schemas/benchmarks.py` run and suite request/response models | Local benchmark contracts centered on transcript-first scoring and synthetic simulation payloads. | Replace with v2 schemas that reference ASSERT specs, evidence manifests, runtime config, and platform metadata. |

### Keep as platform wrapper, but rewire to ASSERT artifacts

| Current path | Why it stays | v2 action |
| --- | --- | --- |
| `apps/api/app/services/benchmark_run_store.py` | Useful hosted value: run indexing, retention, exports, audit views, and per-project history. | Keep conceptually, but persist ASSERT run ids, artifact manifests, pointers, and wrapper metadata instead of full deterministic reports as truth. |
| `apps/api/app/services/benchmark_suite_run_store.py` | Useful hosted value: queued/running/completed suite lifecycle, suite summaries, audit bundles, and export surfaces. | Keep conceptually, but make it track ASSERT-backed scenario runs and suite manifests instead of deterministic suite reports. |
| `apps/api/app/routes/benchmarks.py` list/get/export endpoints | Product-facing wrapper APIs remain valuable. | Keep the surface where it serves product UX, but point the implementation to ASSERT-backed services and v2 schemas. |
| `apps/api/app/main.py` route/service wiring | Platform composition remains here. | Keep, but only mount ASSERT-backed eval/run flows in v2. |
| `apps/web/components/BenchmarkRunner.tsx` | UI for suite selection, saved runs, audit artifacts, contract visibility, and operator workflows. | Keep the UX role, but replace deterministic report assumptions with ASSERT manifest/verdict/taxonomy contracts. |

### Keep as evidence ingestion or report post-processing

| Current path | Why it stays | v2 action |
| --- | --- | --- |
| `apps/api/app/services/assert_adapter.py` | The durable value is evidence normalization from external bundles into a server-side eval input shape. | Replace the current compatibility adapter with a true ASSERT ingress layer that normalizes transcript, vCon, tool traces, final state, provenance, and metadata into ASSERT input artifacts. |
| `apps/api/app/services/benchmark_service.py` `_evidence_audit_summary`, `_group_call_artifact_summary`, `_voice_interaction_summary`, `_vcon_export`, `_suite_vcon_export` | Evidence decoration, operator summaries, and post-processing remain platform value. | Keep these capabilities, but make them read from ASSERT artifacts/manifests rather than generate canonical scoring truth. |
| `apps/api/app/schemas/benchmarks.py` evidence fields | Transcript, call, group-call, vCon, tool-trace, and final-state evidence domains still matter. | Preserve the evidence domains, but move them into explicit v2 evidence-ingestion schemas rather than the current run schema. |

### Delete or quarantine from the production path

| Current path | Why it should leave production | v2 action |
| --- | --- | --- |
| `apps/api/app/routes/evals.py` | Deleted second local evaluator entrypoint. | Keep deleted. |
| `apps/api/app/services/eval_service.py` | Deleted entirely local deterministic evaluator flow. | Keep deleted. |
| `apps/api/app/services/benchmark_evaluator.py` | Deleted competing local scoring semantics. | Keep deleted; do not restore as a fallback runtime. |
| `apps/api/app/services/benchmark_service.py` local simulation helpers and `simulate_*` flows | Synthetic runtime generation belongs in ASSERT tooling or migration-only fixtures, not the hosted production path. | Delete from production path or quarantine under test-only tooling during migration. |
| `apps/api/app/services/benchmark_service.py` `DETERMINISTIC_EVALUATOR_VERSION` and downstream usages | Bakes legacy evaluator identity into audit, retention, and exports. | Remove; replace with ASSERT version/commit and optional platform wrapper version fields. |
| `apps/api/tests/test_evals.py` | Deleted legacy deterministic evaluator endpoint tests. | ASSERT-backed contract tests cover supported acceptance paths. |
| `apps/api/tests/test_benchmark_evaluator.py` | Deleted old scorer parity tests. | ASSERT-backed behavior is covered by boundary and benchmark manifest tests. |
| Deterministic report assertions inside `apps/api/tests/test_benchmarks.py` | Lock in local scoring behavior and adapter-to-legacy-pipeline assumptions. | Rewrite around ASSERT ingress normalization, artifact persistence, and wrapper exports. |

## Paths explicitly outside the delete list

- `apps/api/app/services/voice_lab.py` is not the canonical eval core. It can remain as an external scenario runner or evidence producer.
- `apps/api/app/services/pipecat_service.py` and the realtime/bootstrap services are live demo/runtime integration seams, not the benchmark scoring core.
- `apps/api/app/routes/sessions.py`, `bootstrap.py`, and presentation/deck paths are outside this migration except where they may later emit evidence artifacts for ASSERT.

## Required v2 Contract Breaks

These should be treated as intentional breaking changes, not compatibility-shim candidates:

1. Remove transcript-first `/api/evals/run` semantics. All new evaluation entrypoints should create or reference ASSERT runs/specs.
2. Replace `BenchmarkRunRequest` and `BenchmarkSuiteRunRequest` with v2 contracts that explicitly separate `spec_ref`, `evidence`, `runtime_config`, and `platform_metadata`.
3. Replace local deterministic report fields such as `overall_score`, `rubric_score`, `completed_actions`, `missing_actions`, `hard_check_failures`, and `evaluator_version` with ASSERT-native verdict/taxonomy/artifact structures plus wrapper metadata.
4. Replace local `run_id` derivation from deterministic report inputs with ASSERT execution ids plus platform lineage fields such as `platform_run_id`, `assert_run_id`, `root_run_id`, and `retry_parent_run_id`.
5. Replace persistence of full deterministic report JSON as canonical truth with persistence of ASSERT artifact manifests and storage pointers; database rows become indexes and operator summaries.
6. Replace retry/resume behavior that assumes local reruns with worker state that tracks ASSERT queueing, retries, cancellation, and artifact completion.
7. Replace suite/spec contract hashes derived from repo-local `_SUITES` with hashes/version refs derived from ASSERT-managed specs.

## API/Data Contract Targets For The Next Card

Issue #73's next card, `define v2 ASSERT boundary and schemas`, can start from this minimum boundary without revisiting the migration decision:

### Server-side ASSERT boundary

- `create_assert_run(spec_ref, evidence_manifest, runtime_config, platform_metadata) -> platform_run_record`
- `create_assert_suite_run(spec_ref, scenario_inputs, runtime_config, platform_metadata) -> platform_suite_run_record`
- `ingest_assert_result(assert_run_id, artifact_manifest, verdict, taxonomy, summary) -> persisted wrapper state`
- `load_assert_run_view(platform_run_id) -> UI/export/audit payload`

### Minimum v2 schema set

- `AssertSpecRef`: ASSERT suite/scenario/spec identifiers plus version/commit/hash.
- `AssertEvidenceInput`: transcript, vCon, call media, action trace, final state, provenance, and arbitrary evidence artifact pointers.
- `AssertRuntimeConfig`: execution mode, worker/queue options, retry policy, scenario overrides, and optional environment labels.
- `PlatformRunMetadata`: project/user ownership, notes, labels, retention, billing/quota tags, and regression lineage.
- `PlatformRunRecord`: `platform_run_id`, `assert_run_id`, status, timestamps, summary, artifact pointers, and lightweight denormalized fields for UI.
- `PlatformSuiteRunRecord`: suite-level wrapper record with child run refs and aggregate summary.
- `AuditArtifactView`: operator-facing manifest of stored artifacts, readiness flags, and export pointers.

### Explicit non-goals for the next card

- No compatibility shim for `EvalRunRequest`.
- No promise to preserve deterministic field names.
- No production dual-path runner.
- No requirement that local synthetic simulation remain a supported runtime.

## Minimum deletion sequence

1. Add one server-side ASSERT boundary used by all new run creation.
2. Convert one benchmark vertical end to end so API, worker, persistence, and UI consume ASSERT artifacts.
3. Remove `apps/api/app/routes/evals.py` and `apps/api/app/services/eval_service.py`.
4. Remove production entrypoints that directly call local `run_scenario`, `run_suite`, `simulate_scenario`, or `simulate_suite`.
5. Keep `apps/api/app/services/benchmark_evaluator.py` and `apps/api/tests/test_benchmark_evaluator.py` deleted; do not reintroduce deterministic scorer parity as an acceptance path.
6. Remove `DETERMINISTIC_EVALUATOR_VERSION` and deterministic report assumptions from stores, exports, audit views, and UI types.

## Proof For Issue #73

This inventory was verified against the repo's current implementation surfaces:

- `apps/api/app/routes/evals.py`
- `apps/api/app/services/eval_service.py`
- `apps/api/app/routes/benchmarks.py`
- `apps/api/app/services/benchmark_service.py`
- `apps/api/app/services/benchmark_run_store.py`
- `apps/api/app/services/benchmark_suite_run_store.py`
- `apps/api/app/services/assert_adapter.py`
- `apps/api/app/schemas/evals.py`
- `apps/api/app/schemas/benchmarks.py`
- `apps/web/components/BenchmarkRunner.tsx`
- `apps/api/tests/test_evals.py`
- `apps/api/tests/test_benchmarks.py`

## Outcome

- Durable artifact exists in-repo at `docs/assert-v2-decision-and-deletion-inventory.md`.
- Legacy benchmark evaluator and old scorer parity tests have been removed from the production-supported code path.
- The next card can move to `ready` once Workboard is updated in a session with mutation access.
