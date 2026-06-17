# ConversationAgentEvals

Managed QA and regression testing for voice and conversation agents, scoring task completion, tool actions, policy compliance, and final outcomes from real evidence.

## What this repo does

- Defines domain benchmark suites for consequential agent workflows.
- Runs scenario tests against conversation evidence: transcript, action/tool trace, and final observed state.
- Scores task completion, required actions, forbidden actions, policy constraints, final-state correctness, and evidence quality.
- Normalizes the evidence needed for operator-facing QA reports, audit trails, and regression reruns.
- Provides a focused benchmark runner UI at `/benchmarks`.
- Exposes configuration APIs for benchmark catalogs, saved runs, judge controls, and evidence artifacts.
- Keeps the architecture ready for voice, WebRTC, phone, and group-call evaluation as the product expands.

## Product Direction

Most conversation eval tools grade tone or transcript quality. This project is aimed at a stricter question:

> Did the AI agent actually complete the job?

The repo should no longer be positioned as a generic eval harness or a generic plain-language-spec-to-eval generator. ASSERT already sits directly in that lane as a requirement-driven, trace-aware eval framework with synthetic case generation, scoring, and local artifacts.

The refactor direction is:

- Use ASSERT as optional infrastructure and a compatibility layer where spec-driven eval generation or trace-aware judging helps.
- Differentiate on voice-native QA operations: audio/call evidence, vCon, WebRTC or phone metadata, STT/TTS failure modes, interruption handling, latency, and transfer behavior.
- Prove task completion against external state, not just transcript quality.
- Turn production failures into saved regression scenarios with operator-ready evidence and reports.

The MVP can still start with text-first scenario simulation and deterministic scoring, but that path should now be understood as an ingestion and reporting wedge for voice and conversation QA, not the final product identity.

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
  Web[Next.js web app] -->|scenario selection and evidence| API[FastAPI app]
  API --> Suites[Benchmark suite service]
  API --> Evaluator[Deterministic benchmark evaluator]
  API --> Evidence[Evidence normalization and audit artifacts]
  API --> Runs[Saved runs and benchmark catalog]
  API --> Judges[Judge controls and cost guardrails]
  API --> ASSERT[Optional ASSERT import/export adapter]
  Evaluator --> Report[Scores, evidence, failures, suggested fixes]
  Evidence --> Report
  Web --> Report

  Cloud[Google Cloud Run, Firestore, Storage, Tasks] --> API
  Voice[Future voice/WebRTC/phone runs] --> API
  GroupCalls[Future group-call artifacts] --> API
  Tools[Future agent tool traces] --> Evaluator
```

Core ownership:

- `apps/web`: Next.js SaaS homepage, benchmark runner, and presentation/demo surfaces.
- `apps/api`: FastAPI backend for sessions, evals, benchmark suites, simulation, and scoring.
- `apps/pipecat`: live media orchestration groundwork for voice/WebRTC paths.
- `docs`: product notes, implementation plan, and benchmark direction.

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

## Useful Commands

```bash
npm run build:web
npm run test:api
npm run test:api:docker
npm run test:api:local
npm run test:benchmark-smoke
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

## Current MVP Boundary

The current product surface is a focused benchmark runner and evidence/reporting API. The runner can load benchmark suites, simulate a scenario or full suite, inspect transcript/action/final-state/group-call evidence, produce scored benchmark reports, export vCon-compatible records, request controlled LLM judge runs, and save runs for regression comparison.

Near-term next slices:

- Add an ASSERT adapter for import/export and optional trace-grounded judging instead of rebuilding a parallel spec-to-eval taxonomy.
- Ingest one real artifact type well first: vCon or call transcript plus metadata plus tool/action log.
- Define 10-20 hard task-completion and failure-diagnosis checks for one production vertical.
- Expand QA reports so they cite transcript spans, tool calls, timestamps, final state, and failure layer.
- Add a production-failure-to-regression flow so real missed bookings, bad escalations, or failed transfers become rerunnable saved scenarios.
