# Runs MVP

Execute → analyze loop for ConversationAgentEvals.

## Flow

1. Register **agent targets** under `/targets` (file-backed JSON in `artifacts/agents/`; legacy `/agents` redirects here).
2. Launch an evaluation from `/runs` (pick the target under test and the tester that drives the scenario).
3. Open `/runs` for the list, then `/runs/[executionRunId]` for analysis:
   - Metric summary tiles (interruption, latency, call resolution)
   - Latency detail + stub dual-track waveform
   - Transcript / turns

## API

- `GET/POST /api/agents`, `GET/PATCH/DELETE /api/agents/{id}` (registry still named agents in the API)
- `POST /api/execution/runs` accepts optional `agent_id`, plus explicit `tester_id` and `executor_id`
- Conversations include `metrics_summary` and `timeline`
- Durable snapshot: `artifacts/execution-runs/{id}/run.json` (+ `inference_set.jsonl`)

## Notes

- Each execution preserves three roles: **target under test**, **tester**, and the unscored **executor**.
- The external HTTP JSON adapter POSTs `message`, OpenAI-style `history`, and scenario metadata,
  then reads reply text from a configured dot path (default `response`). It is real black-box
  invocation, but has no tool/trace visibility unless the target response provides it later.
- HTTP authentication uses an environment-variable name in `secret_ref`; raw secrets are never stored.
- Metrics and waveform remain fixture/synthetic for voice in this MVP (no real audio playback).
- Built-in **testing targets**: **Mock text target**, **ACC voice fixture target**.
- Browser WebRTC and SIP/phone target registration remain unavailable until end-to-end media proof exists.
