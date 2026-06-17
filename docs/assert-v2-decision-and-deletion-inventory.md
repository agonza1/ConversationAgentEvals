# ASSERT v2 Decision And Deletion Inventory

## Status

- Date: 2026-06-17
- Scope: `ConversationAgentEvals` issue #73 phase 0
- Decision: `ConversationAgentEvals` v2 is a breaking-change migration where ASSERT becomes the only canonical eval core.

This document supersedes the earlier repo framing that treated ASSERT as optional infrastructure or an import/export compatibility layer. In particular, [docs/assert-refactor-direction.md](./assert-refactor-direction.md) and [docs/implementation-plan.md](./implementation-plan.md) describe an adapter-friendly direction that is no longer the target for v2.

## Baseline Decision

`ConversationAgentEvals` v2 should stop owning its own evaluator semantics, scenario simulation runtime, scoring engine, and dual-path runner behavior.

ASSERT should become the only canonical home for:

- eval specs, requirements, rubrics, and scenario generation
- execution/runtime orchestration for eval runs
- trace-aware judging and scoring
- result schema, verdicts, failure taxonomy, and artifact manifests
- portable canonical artifacts for local, CI, and hosted storage

`ConversationAgentEvals` v2 should own the hosted wrapper around ASSERT:

- project, suite, run, rerun, comparison, and export APIs
- tenant/project/user metadata, auth, quotas, and billing hooks
- evidence ingestion and normalization for transcripts, vCon, tool logs, final state, and voice metadata
- artifact storage pointers, audit views, report packaging, and regression UX
- queue/worker lifecycle, health checks, deployment, and observability

There should be no supported dual runtime in v2. Legacy deterministic evaluator behavior should be replaced, not preserved as a production fallback.

## Current Inventory

### Replace with ASSERT core

| Current path | Why it is core eval logic today | v2 action |
| --- | --- | --- |
| `apps/api/app/routes/evals.py` | Exposes a first-class local evaluator endpoint that directly calls local judging logic. | Replace with an ASSERT-backed boundary or remove entirely if benchmark/project routes become the only supported entrypoint. |
| `apps/api/app/services/eval_service.py` | Owns local criteria splitting, evidence matching, failure-layer tagging, scoring, artifact creation, and vCon report generation. | Delete after an ASSERT-backed replacement exists. Any reusable evidence normalization should move behind the shared v2 ASSERT boundary. |
| `apps/api/app/routes/benchmarks.py:262-365` | Invokes local `run_scenario`, `run_suite`, `simulate_scenario`, and background suite execution directly from API routes. | Replace with one ASSERT-only service boundary for run creation and queue orchestration. |
| `apps/api/app/services/benchmark_service.py:18-759` | Owns benchmark definitions, deterministic rubric scoring, run id generation, simulation, suite orchestration, and report assembly. | Split apart. Move eval specs/runtime/judging into ASSERT; keep only platform orchestration and report decoration in this repo. |
| `apps/api/app/services/benchmark_evaluator.py` | Implements required-action, forbidden-action, task-completion, final-state, and ordering semantics locally. | Delete or quarantine once ASSERT provides the canonical hard-check semantics. |
| `apps/api/app/schemas/evals.py` | Defines a local deterministic eval contract instead of an ASSERT run/spec/artifact contract. | Replace with v2 schemas centered on ASSERT specs, runs, manifests, verdicts, and platform metadata. |
| `apps/api/app/schemas/benchmarks.py:41-184` | Encodes legacy request/response contracts around transcript-first deterministic scoring and local simulation outputs. | Replace with v2 contracts that point at ASSERT specs, evidence inputs, runtime config, canonical artifact manifests, and platform metadata. |

### Keep as platform wrapper, but rewire to ASSERT artifacts

| Current path | Why it stays | v2 action |
| --- | --- | --- |
| `apps/api/app/services/benchmark_run_store.py:20-180` | Stores run metadata, retention windows, export views, and operator summaries. That is platform value, not core judging. | Keep conceptually, but rewrite around ASSERT artifact manifests and artifact pointers instead of storing deterministic report bodies as canonical truth. |
| `apps/api/app/services/benchmark_suite_run_store.py:18-180` | Tracks queued/running/completed suite lifecycle, suite-level summaries, and export surfaces. | Keep conceptually, but point suite lifecycle/state at ASSERT-backed run records and canonical suite artifacts. |
| `apps/api/app/routes/benchmarks.py:1-259` | List, get, export, audit, and vCon bundle routes are platform surfaces the product still needs. | Keep these surfaces if useful, but migrate their payloads to v2 artifact-oriented contracts. |
| `apps/web/components/BenchmarkRunner.tsx:7-220` | Exposes benchmark selection, evidence panels, audit artifact views, and saved-run UX that remain differentiating wrapper features. | Keep the UI role, but migrate the types and rendering to ASSERT run/spec/artifact contracts and platform metadata. |
| `apps/api/app/main.py:73-91` | App wiring, route mounting, and service composition remain platform concerns. | Keep, but ensure only ASSERT-backed eval routes are mounted for v2. |

### Keep as evidence ingestion or report post-processing

| Current path | Why it stays | v2 action |
| --- | --- | --- |
| `apps/api/app/services/assert_adapter.py` | The useful part is not the compatibility framing; it is the evidence normalization seam from external artifacts into internal inputs. | Replace the current compatibility adapter with a true ASSERT boundary and pre-processing layer that normalizes transcripts, vCon, tool traces, and metadata into ASSERT-compatible inputs. |
| `apps/api/app/services/benchmark_service.py:1033-1102` | `evidence_audit_summary`, group-call summaries, and voice metadata extraction are platform-side evidence decoration. | Keep the capability, but attach it to ASSERT artifacts instead of deterministic reports. |
| `apps/api/app/services/benchmark_service.py:588-590` and store export helpers | vCon export/report packaging is part of the hosted QA wrapper story. | Keep as post-processing layered on canonical ASSERT results plus platform metadata. |
| `apps/api/app/schemas/benchmarks.py:48-95` | Acceptance of transcripts, calls, vCon, tool traces, and final-state evidence is still needed. | Preserve the evidence domains, but move them into v2 evidence-ingestion schemas rather than the current legacy run schema. |

### Delete or quarantine from the production path

| Current path | Why it should leave the production path | v2 action |
| --- | --- | --- |
| `apps/api/app/services/eval_service.py` | Entirely local deterministic judging path. | Delete after replacement. |
| `apps/api/app/routes/evals.py` | Preserves a second evaluator surface outside the benchmark/project run flow. | Remove or quarantine. |
| `apps/api/app/services/benchmark_evaluator.py` | Competing local scoring semantics. | Delete after ASSERT parity. |
| `apps/api/app/services/benchmark_service.py:593-682` | Local synthetic scenario generation and validation are core eval concerns that ASSERT should own. | Delete or quarantine behind tests only during migration. |
| `apps/api/app/services/benchmark_service.py:15` and downstream `DETERMINISTIC_EVALUATOR_VERSION` usage | Bakes the legacy evaluator into persistence, audit summaries, and exports. | Remove and replace with ASSERT version/commit plus adapter/platform version fields. |
| `apps/api/tests/test_evals.py` | Tests a legacy deterministic evaluator endpoint. | Delete or replace with ASSERT-backed contract tests. |
| `apps/api/tests/test_benchmark_evaluator.py` | Tests local evaluator semantics that should no longer be canonical. | Delete or replace with ASSERT-backed golden artifact tests. |
| `apps/api/tests/test_benchmarks.py:187-314` and similar deterministic fixture sections | Assert the current adapter-into-legacy-pipeline behavior and deterministic scoring details. | Replace with tests that validate evidence normalization into ASSERT inputs and stable ASSERT artifact handling. |

## Paths explicitly outside the delete list

- `apps/api/app/services/voice_lab.py` should not be treated as the canonical eval core. It is better viewed as an external scenario runner or proof harness that may continue to produce evidence artifacts for platform ingestion.
- `apps/api/app/services/pipecat_service.py` is a runtime integration seam for live presentation/demo flows, not the benchmark scoring core. It is not part of the primary v2 deletion target.

## Required v2 Contract Breaks

The following contract changes should be treated as intentional breaking changes, not shimmable compatibility layers:

1. Replace transcript-first `/api/evals/run` and `/api/benchmarks/*/run` semantics with ASSERT-first run creation semantics.
2. Replace `BenchmarkRunRequest` and `BenchmarkSuiteRunRequest` payloads with v2 payloads that reference ASSERT specs, evidence inputs, runtime configuration, and canonical artifact storage.
3. Replace deterministic report fields such as `overall_score`, `rubric_score`, `completed_actions`, `hard_check_failures`, and `evaluator_version` with ASSERT-native verdict, taxonomy, and artifact structures plus platform wrapper metadata.
4. Replace local `run_id` derivation based on deterministic report inputs with run identifiers tied to ASSERT execution plus platform lineage metadata.
5. Replace local persistence of full deterministic report JSON with storage of ASSERT artifacts or manifests as canonical results and database rows for platform indexes only.
6. Replace retry and resume behavior that assumes local deterministic reruns with queue or worker behavior that tracks ASSERT runtime state, cancellation, retries, and artifact completion.

## Minimum deletion sequence

1. Add one server-side ASSERT boundary used by all new run creation.
2. Convert one benchmark vertical end to end so API, worker, persistence, and UI read from ASSERT artifacts.
3. Remove `routes/evals.py` and the local `eval_service.py` path.
4. Remove direct route calls to `run_scenario`, `run_suite`, and `simulate_*` from production entrypoints.
5. Delete `benchmark_evaluator.py` and deterministic evaluator tests after ASSERT-backed contract coverage exists.
6. Remove `DETERMINISTIC_EVALUATOR_VERSION` from storage, exports, and audit summaries.

## Proof for issue #73

This inventory was derived from the current repo surfaces below:

- `apps/api/app/routes/evals.py`
- `apps/api/app/routes/benchmarks.py`
- `apps/api/app/services/eval_service.py`
