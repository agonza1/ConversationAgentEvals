# ConversationAgentEvals

Managed QA and regression testing for voice and conversation agents, powered by [ASSERT](https://github.com/responsibleai/ASSERT).

ConversationAgentEvals is a hosted/on-demand wrapper around ASSERT. ASSERT owns the evaluation core: specifications, scenarios, runtime orchestration, judging, scoring, failure taxonomy, and portable artifacts. This repository adds the product layer around that core: evidence ingestion, projects and runs, queue lifecycle, persistence, reports, exports, and deployment.

There is one product and one supported evaluation path. Historical product variants and the former local evaluator are not supported.

## Acknowledgment

[Thank you to the ASSERT project and its maintainers](https://github.com/responsibleai/ASSERT) for building and sharing the evaluation foundation this project wraps.

## What this repository adds

- Hosted projects, suites, runs, reruns, comparisons, exports, and audit views.
- Evidence normalization for transcripts, conversations, vCon records, tool/action traces, final-state snapshots, audio pointers, and voice metadata.
- Platform-owned authentication, project lineage, retention, labels, retries, cancellation, quotas, billing hooks, and cost limits.
- Durable ASSERT artifact manifests with searchable platform metadata indexes.
- Operator-facing QA reports, saved-run history, regression comparisons, and export workflows.
- A focused benchmark runner at `/benchmarks`.
- Voice and WebRTC integration groundwork through the Pipecat service.

ASSERT artifacts remain the canonical evaluation results. The application database stores product metadata and indexes; it does not replace ASSERT's result model.

## Local development

Prerequisites:

- Node.js 20+
- npm 10+
- Python 3.11+ with `venv`

Install dependencies and create a local environment:

```bash
npm run setup
cp .env.example .env
npm run check:env
```

Start the API, web app, and Pipecat service:

```bash
npm run dev
```

Open the printed web URL and visit `/benchmarks`. The basic demo uses transcript, action-trace, and final-state evidence, so it does not require live microphone ASR. Advanced environment settings are documented in [docs/environment.md](docs/environment.md).

For the shortest end-to-end walkthrough, see [docs/assert-flow-demo.md](docs/assert-flow-demo.md) or its [machine-readable example](docs/examples/assert-flow-demo.json).

## Architecture

```mermaid
flowchart LR
  Inputs["Requests + evidence"] --> Platform["ConversationAgentEvals wrapper"]
  Platform --> Core["ASSERT core"]
  Core --> Artifacts["Canonical ASSERT artifacts"]
  Platform --> Metadata["Platform metadata + indexes"]
  Artifacts --> Reports["Reports + exports"]
  Metadata --> Reports
```

Core ownership:

- `apps/web`: Next.js product UI and benchmark runner.
- `apps/api`: FastAPI product API, ASSERT boundary, evidence ingestion, persistence, queue lifecycle, and exports.
- `apps/pipecat`: media orchestration groundwork for voice and WebRTC paths.
- `docs`: environment, operations, demos, and product-specific implementation notes.

Canonical ASSERT wrapper anchors:

- [apps/api/app/schemas/assert_contracts.py](apps/api/app/schemas/assert_contracts.py)
- [apps/api/app/services/assert_boundary.py](apps/api/app/services/assert_boundary.py)
- [apps/api/app/services/assert_artifact_store.py](apps/api/app/services/assert_artifact_store.py)
- [apps/api/app/services/assert_queue_lifecycle.py](apps/api/app/services/assert_queue_lifecycle.py)
- [docs/assert-boundary-and-schemas.md](docs/assert-boundary-and-schemas.md)

The local sidecar endpoint is `POST /api/assert/runs`; completed manifests use `local-artifact://assert/runs/{run_id}/manifest.json`.

## Docker

Run the static parity check and start the default API/web stack:

```bash
npm run docker:check
npm run docker:up
```

The default Compose stack uses SQLite at `./storage/conversation_agent_evals.db`. Optional profiles add voice and persistent services:

```bash
docker compose --profile voice up --build

COMPOSE_DATABASE_URL=postgresql://cae:cae_local_password@db:5432/conversation_agent_evals \
  docker compose --profile persistence up --build
```

See [docs/environment.md](docs/environment.md) and [docs/ops-checklist.md](docs/ops-checklist.md) for configuration and deployment details.

## Validation

```bash
npm run check:env
npm run lint:web
npm run build:web
npm run test:api
npm run test:benchmark-smoke
apps/api/.venv/bin/python -m pytest apps/api/tests/test_assert_boundary.py apps/api/tests/test_benchmarks.py -q
```

`npm run test:api` uses Docker when available and otherwise falls back to an existing local API virtual environment. Run `npm run setup` first for local-only testing.

## Benchmark families

- Call-center voice AI: appointments, cancellations, transfers, interruptions, and escalation.
- Telehealth intake: patient routing, privacy boundaries, medication, and emergency handling.
- Online teaching: adaptive tutoring, quiz flow, confusion handling, and grading boundaries.
- Fintech support: identity checks, disputes, card freezes, fraud escalation, and compliance.

## Scope

ConversationAgentEvals is the reusable product wrapper and evidence system. It is not a second evaluator competing with ASSERT, and it is not the Voice Agent Reliability Lab's commercial operating layer. Product differentiation belongs in hosted workflows, evidence ingestion, report UX, operations, and voice/conversation QA packaging.
