# ConversationAgentEvals Ops Checklist

## Current MVP

- Run locally with `npm run dev` or Docker Compose.
- Validate backend with `npm run test:api`.
- Validate frontend build with `npm run build:web`.
- GitHub Actions runs API tests and web build on pushes and pull requests.
- Saved run scaffolding is still prototype-grade until project/run persistence moves to a durable store.

## Before customer pilots

- Decide the durable project/run store: Firestore or PostgreSQL.
- Store benchmark runs with tenant/project ownership, run metadata, evidence artifacts, and export records.
- Add audit events for suite creation, run started, run completed, report exported, and judge requested.
- Add spend controls for LLM judge calls and future voice minutes.
- Add deployment secrets documentation for identity, OpenAI/Vertex or other judge providers, and storage.

## Release gates

- API tests pass.
- Web build passes.
- Seed benchmark suites load in the UI.
- At least one pass and one failure scenario can be simulated.
- Saved runs retain transcript, action trace, final state, run labels, and vCon-compatible export data.
- Optional judge controls are visible but do not block local deterministic demo paths.
