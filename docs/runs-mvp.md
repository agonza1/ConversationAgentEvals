# Runs MVP

Execute → analyze loop for ConversationAgentEvals.

## Model: Target → Tester → Executor → Evidence

- **Target** is the agent under test and its destination: HTTP endpoint, built-in sample agent,
  browser/WebRTC agent, SIP URI, or E.164 phone number.
- **Tester** drives the scenario as the simulated user or caller.
- **Executor** conducts the test: local async text runner, CAE local audio loop, saved-evidence
  replay, or a future ACC browser/SIP/phone adapter.
- **Evidence** is what gets scored: generated/provider responses, a local audio-loop capture,
  saved replay, or future ACC live capture.

These roles are stored independently in every run. A media transport is never represented as
the target, and a saved conversation replay is evidence—not a new agent target.

## Flow

1. Register **agent targets** under `/targets` (file-backed JSON in `artifacts/agents/`; legacy `/agents` redirects here).
2. Launch an evaluation from `/runs` (pick the target, tester, and compatible executor).
3. Open `/runs` for the list, then `/runs/[executionRunId]` for analysis:
   - Metric summary tiles (interruption, latency, call resolution)
   - Latency detail + stub dual-track waveform
   - Transcript / turns

## API

- `GET/POST /api/agents`, `GET/PATCH/DELETE /api/agents/{id}` (registry still named agents in the API)
- `POST /api/execution/runs` accepts optional `agent_id`, plus explicit `tester_id` and `executor_id`
- `GET /api/execution/acc-connection` and `POST /api/execution/acc-connection/test` report
  ACC media readiness separately from CAE executor availability
- Conversations include `metrics_summary` and `timeline`
- Durable snapshot: `artifacts/execution-runs/{id}/run.json` (+ `inference_set.jsonl`)

## Notes

- Each execution snapshots target, tester, executor, and evidence provenance before queueing.
- The external HTTP JSON adapter POSTs `message`, OpenAI-style `history`, and scenario metadata,
  then reads reply text from a configured dot path (default `response`). It is real black-box
  invocation, but has no tool/trace visibility unless the target response provides it later.
- HTTP authentication stores an opaque lowercase credential ID in `secret_ref`, never an
  environment-variable name or raw secret. The server resolves only the dedicated
  `CAE_HTTP_TARGET_SECRET_*` namespace: for example, credential ID `support-staging`
  resolves from `CAE_HTTP_TARGET_SECRET_SUPPORT_STAGING`.
- The built-in sample voice agent uses the CAE local audio loop and is labeled honestly: no
  browser, SIP, or phone call occurs.
- Browser WebRTC, SIP URI, and E.164 phone are separate destination types and validators.
- ACC remains the formal live media owner. Its readiness endpoint may report media healthy,
  but CAE keeps Create/Run disabled until the corresponding launch and evidence-capture adapter
  is actually implemented. Readiness never implies executability.
