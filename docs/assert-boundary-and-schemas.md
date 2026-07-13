# ASSERT Boundary and Schemas

ConversationAgentEvals invokes [ASSERT](https://github.com/responsibleai/ASSERT) through one server-side boundary. The wrapper owns product concerns; ASSERT owns evaluation semantics and canonical results.

## Canonical code

- Schema models: `apps/api/app/schemas/assert_contracts.py`
- Boundary service: `apps/api/app/services/assert_boundary.py`
- Local sidecar route: `apps/api/app/routes/assert_sidecar.py`
- Artifact persistence: `apps/api/app/services/assert_artifact_store.py`
- Queue lifecycle: `apps/api/app/services/assert_queue_lifecycle.py`
- Boundary tests: `apps/api/tests/test_assert_boundary.py`

## Supported entrypoints

- `create_assert_run(spec_ref, evidence, runtime_config, platform_metadata)`
- `create_assert_suite_run(spec_ref, scenarios, runtime_config, platform_metadata)`
- `ingest_assert_result(platform_run_id, assert_run_id, result_manifest)`

The local sidecar exposes `POST /api/assert/runs` and `GET /api/assert/runs/{platform_run_id}`. Production API and workers use the same HTTP-sidecar transport contract.

## Ownership boundary

ConversationAgentEvals owns:

- caller authentication and project ownership
- evidence normalization
- product metadata, lineage, retention, and labels
- queueing, retries, cancellation, and cost guards
- persistence indexes, history, reports, and exports

ASSERT owns:

- specification loading and resolution
- scenario execution semantics
- judging, scoring, and verdicts
- failure taxonomy
- canonical result manifests and artifacts

There is no fallback evaluator or parallel product runtime.

## Contracts

`AssertSpecRef` identifies an ASSERT scenario or suite and requires a stable spec version or hash.

`AssertEvidenceInput` accepts transcript, conversation, vCon, call-media, action-trace, final-state, ASSERT bundle, and additional artifact pointers.

`AssertRuntimeConfig` defines HTTP-sidecar execution, worker queue, retry policy, scenario overrides, and environment labels.

`PlatformRunMetadata` carries wrapper-only data such as user, project, lineage, labels, retention, quota, and billing tags.

`AssertResultManifest` carries the ASSERT verdict, failure taxonomy, canonical artifacts, and summary exports. `PlatformRunRecord` and `PlatformSuiteRunRecord` add product lifecycle and ownership data without redefining evaluation truth.

## Artifacts and lifecycle

Completed manifests use `local-artifact://assert/runs/{run_id}/manifest.json`. Database rows retain the product index and manifest pointer.

The wrapper records ASSERT provenance, adapter identity, spec version, provider/model settings, artifact location, and platform identity. Queue states are `queued`, `running`, `completed`, `failed`, and `canceled`, with bounded retries, explicit cancellation, and cost checks.
