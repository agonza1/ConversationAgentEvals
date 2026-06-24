# ConversationAgentEvals

ASSERT-first managed QA and regression testing for voice and conversation agents.

ConversationAgentEvals v2 is the hosted/on-demand platform wrapper around ASSERT. ASSERT owns the canonical eval core: specs, scenarios, runtime orchestration, judging, scoring, failure taxonomy, and portable artifacts. ConversationAgentEvals owns the product wrapper: evidence ingestion, tenant/project/run APIs, platform metadata, queue/worker lifecycle, report UX, exports, and operational deployment.

## What this repo does

- Wraps ASSERT so hosted users can create projects, suites, runs, reruns, comparisons, exports, and audit views.
- Normalizes production evidence into ASSERT-compatible inputs: transcripts, vCon/call metadata, tool/action traces, final-state snapshots, audio pointers, and related voice metadata.
- Stores ASSERT artifacts/manifests as canonical eval evidence/results while keeping platform metadata and indexes in the app database.
- Records ASSERT provenance for every v2 run: ASSERT version/commit, adapter version, spec version, provider/model settings, artifact manifest location, and platform version.
- Provides queue/worker lifecycle primitives for long-running ASSERT jobs: queued, running, completed, failed, canceled, retry, cancellation, and cost-limit handling.
- Packages ASSERT artifacts plus platform context into operator-facing QA reports, audit trails, exports, and regression reruns.
- Provides a focused benchmark runner UI at `/benchmarks`.
- Exposes configuration APIs for benchmark catalogs, saved runs, judge controls, evidence artifacts, and platform wrapper state.
- Keeps the architecture ready for voice, WebRTC, phone, and group-call evaluation as the product expands.

## Product Direction

Most conversation eval tools grade tone or transcript quality. This project is aimed at a stricter question:

> Did the AI agent actually complete the job?

The #73 migration makes this a breaking ASSERT-first v2 architecture. The repo should no longer be positioned as a generic eval harness, a plain-language-spec-to-eval generator, or a second evaluator competing with ASSERT.

The current direction is:

- ASSERT is the only canonical eval core for v2.
- ConversationAgentEvals is the deployable platform around ASSERT.
- Legacy deterministic evaluator/runtime paths are migration targets for deletion or quarantine, not fallback paths to preserve.
- Differentiation lives in hosted workflows, evidence ingestion, report UX, saved runs, regression comparison, queueing, storage, auth, quotas, billing hooks, observability, and voice/conversation QA packaging.
- Voice-native QA remains a key product wedge: audio/call evidence, vCon, WebRTC or phone metadata, STT/TTS failure modes, interruption handling, latency, transfer behavior, and final-state proof.

## Relationship To Voice Agent Reliability Lab

ConversationAgentEvals is the reusable evaluation and evidence system. It defines scenarios, ingests transcripts/tool traces/call artifacts, scores outcomes, and produces audit-ready reports.

The Voice Agent Reliability Lab is an operating program that uses tools like this repo to prove whether voice agents can perform real business work end-to-end. The lab owns mission packets, buyer-facing proof cycles, QA gates, and go/no-go decisions. This repo can supply evidence bundles and regression checks for the lab, but it is not the lab itself.

Commercial packaging is intentionally out of scope for this repository's current architecture. If a packaging layer is needed later, it should wrap the eval system rather than live inside the core eval runner.

## Benchmark Families

- Call center voice AI: appointments, cancellations, transfers, interruptions, escalation.
- Telehealth intake: patient routing, privacy boundaries, medication and emergency handling.
- Online teaching: adaptive tutoring, quiz flow, confusion handling, grading boundaries.
- Fintech support: identity checks, disputes, card freezes, fraud escalation, compliance.

## Architecture

```mermaid
flowchart LR
  subgraph Inputs[Inputs]
    Req["Eval requests<br/>project / suite / run / rerun"]
    Evidence["Production evidence<br/>transcripts / vCon / tool logs<br/>audio pointers / metadata"]
    Config["Runtime config<br/>models / tools / credentials<br/>cost guardrails"]
  end

  subgraph Platform[ConversationAgentEvals v2 platform wrapper]
    Web[Next.js web app]
    API[FastAPI API]
    Ingest["Evidence ingestion<br/>ASSERT-compatible specs/traces"]
    Boundary["ASSERT service boundary<br/>single server-side interface"]
    Ops["Platform operations<br/>queues / workers / health<br/>quotas / billing / observability"]
    Reports["Report + export UX<br/>saved runs / comparisons / audit trail"]
  end

  subgraph Core[ASSERT core]
    Specs["Specs + scenarios<br/>requirements / rubrics"]
    Runtime["Runtime orchestration<br/>multi-turn execution / tools / providers"]
    Judge["Judging + scoring<br/>verdicts / failure taxonomy"]
    Manifest["Artifact manifest<br/>canonical eval evidence/results"]
  end

  subgraph Storage[Artifacts and metadata]
    ArtifactStore["Artifact storage<br/>immutable ASSERT outputs"]
    MetadataDB["Platform DB<br/>tenants / projects / run indexes<br/>lineage / cost / labels"]
    Exports[Exports + client-ready reports]
  end

  Req --> API
  Evidence --> Ingest
  Config --> Boundary
  Web --> API
  API --> Ingest
  API --> Ops
  Ingest --> Boundary
  Boundary --> Specs
  Ops --> Runtime
  Specs --> Runtime
  Runtime --> Judge
  Judge --> Manifest
  Manifest --> ArtifactStore
  API --> MetadataDB
  Ops --> MetadataDB
  Manifest --> Reports
  MetadataDB --> Reports
  Reports --> Exports
  ArtifactStore --> Exports
```

Core ownership:

- `apps/web`: Next.js SaaS homepage, benchmark runner, and presentation/demo surfaces.
- `apps/api`: FastAPI backend for sessions, benchmark APIs, ASSERT v2 boundary, evidence ingestion, run persistence, report metadata, and exports.
- `apps/pipecat`: live media orchestration groundwork for voice/WebRTC paths.
- `docs`: ASSERT v2 migration docs, product notes, implementation plan, and benchmark direction.

Important code anchors:

- `docs/assert-v2-decision-and-deletion-inventory.md`
- `docs/assert-v2-boundary-and-schemas.md`
- `apps/api/app/schemas/assert_v2.py`
- `apps/api/app/services/assert_v2_boundary.py`

## Local Setup

```bash
cp .env.example .env
npm run setup
```

Then run the stack:

```bash
npm run dev
```

Or use Docker:

```bash
npm run docker:check
npm run docker:up
```

The Docker path is intended to mirror a production-style container startup:

- `docker compose up --build` builds the API, Pipecat, and web images from the checked-in Dockerfiles.
- Compose starts Postgres first, runs the one-shot `seed` service to create the demo workspace and benchmark projects, then starts the API, worker, Pipecat, and web services behind health checks.
- Containers run the code baked into those images; source directories are not bind-mounted over the built app.
- Local state remains mounted for MVP persistence: `./storage` for generated artifacts and the named `postgres_data` volume for database state.
- The API and worker use the same API image and container-internal `DATABASE_URL` pointed at the `db` service; `.env.example` also includes a localhost `DATABASE_URL` for host-side tools.
- The worker is intentionally lightweight for the MVP: it verifies database connectivity and the seeded benchmark catalog on an interval so Compose exercises the background-process shape before a queue is introduced.
- The web image builds Next.js during `docker build` with compose-provided build args for internal service URLs and browser-facing localhost ports, then starts the prebuilt app at container runtime.
- Compose uses internal service URLs for server-side traffic (`http://api:8000`, `http://pipecat:8110`) and localhost URLs only for browser-facing `NEXT_PUBLIC_*` values.
- `npm run docker:check` is a fast static smoke check for compose defaults, database/worker/seed wiring, and Dockerfile parity. It does not build images or require Docker to be running.
- `npm run test:api` is Docker-first API validation: when Docker is available it builds the checked-in API Dockerfile and runs pytest inside that image, so validation does not depend on host Python virtualenv or ensurepip support. If Docker is unavailable, it falls back to an existing local venv for sandbox-only iteration; use `npm run test:api:docker` to require the hermetic path or `npm run test:api:local` after `npm run setup` for local-only iteration.

Default local endpoints:

- Web app: `http://localhost:3012` with Docker, or the URL printed by `npm run dev`
- API: `http://localhost:8025`
- Pipecat service: `http://localhost:8110`

### ASR contract for conversation demos

Conversation demos use `rtc-asr` as the speech-to-text provider contract. Live Pipecat ASR expects an rtc-asr service reachable at `RTC_ASR_BASE_URL`, and streams audio to `RTC_ASR_STREAM_PATH` using the Local STT v1 WebSocket protocol. The default stream path is `/v1/stt/stream`; the default health path is `/health`.

The Pipecat live input contract is 16 kHz, mono, little-endian PCM16. The checked-in Pipecat transport and pipeline params set `audio_in_sample_rate=16000`; any future resampler/downmixer must be documented as the processor responsible for converting browser audio into that contract before rtc-asr receives it.

Required local env names:

```bash
RTC_ASR_BASE_URL=http://localhost:8000
RTC_ASR_HEALTH_PATH=/health
RTC_ASR_STREAM_PATH=/v1/stt/stream
```

When `RTC_ASR_BASE_URL` is empty or the health check is unavailable, live session startup marks ASR as `not_configured` or `unavailable` and records a `rtc_asr_skipped` event. The `/sessions/{id}/ask` transcript loop remains non-production demo support only; it is not the ASR provider contract.

## Useful Commands

```bash
npm run build:web
npm run test:api
npm run test:api:docker
npm run test:api:local
npm run test:benchmark-smoke
apps/api/.venv/bin/python -m pytest apps/api/tests/test_assert_v2_boundary.py apps/api/tests/test_benchmarks.py -q
npm run test:voice-lab-proof
npm run test:e2e
```

`npm run test:benchmark-smoke` is an API-level smoke path for the benchmark runner. It does not need browser or voice credentials; it lists suites, simulates pass and failure runs, verifies run metadata and audit fields, then saves, lists, and exports a run.

`npm run test:voice-lab-proof` runs the minimal integrated evidence runner. It executes the deterministic contact-center fixture plus the transcript-injected `/ask` loop, then writes a bundle manifest under `artifacts/voice-lab/voice-lab-bundle-<timestamp>/manifest.json` with per-scenario transcript, timeline, raw result files, scorecard fields, and explicit unsupported live layers.

Voice proof against a running stack:

```bash
PLAYWRIGHT_BASE_URL=http://127.0.0.1:13000 \
PLAYWRIGHT_API_BASE_URL=http://127.0.0.1:8025 \
PLAYWRIGHT_PIPECAT_BASE_URL=http://127.0.0.1:8110 \
npm run test:voice-proof
```

## Current ASSERT v2 Boundary

The current product surface is a focused benchmark runner and evidence/reporting API being migrated to ASSERT-first contracts. The platform can load benchmark suites, inspect transcript/action/final-state/group-call evidence, produce reports, export vCon-compatible records, request controlled judge runs, and save runs for regression comparison. The v2 direction is to make ASSERT artifacts and manifests the canonical truth behind those surfaces.

Implemented or in progress for issue #73:

- Phase 0 decision/inventory: v2 is a breaking ASSERT-first migration; no supported dual runtime.
- Phase 1 boundary/schemas: one server-side ASSERT boundary with v2 request/result schemas.
- Primary run path: benchmark run creation/report data are being wired through ASSERT artifacts plus platform metadata.

Still to finish:

- Persist canonical ASSERT run manifests outside inline result payloads, then store platform metadata/indexes and manifest pointers in the app database.
- Add the queue lifecycle surface for `queued`, `running`, `completed`, `failed`, `canceled`, retries, cancellation, and cost limits.
- Delete or quarantine legacy evaluator/runtime modules from production run creation.
- Remove deterministic fallback behavior from acceptance paths and tests.
- Finish UI/API migrations to v2 ASSERT contracts.
- Expand generic evidence adapters and lab-consumable report exports on top of ASSERT artifacts.
