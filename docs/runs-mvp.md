# Runs MVP

Execute → analyze loop for ConversationAgentEvals.

## Flow

1. Register agents under `/agents` (file-backed JSON in `artifacts/agents/`).
2. Launch an evaluation from `/benchmarks` (Launch evaluation panel; pick an agent).
3. Open `/runs` for the list, then `/runs/[executionRunId]` for analysis:
   - Metric summary tiles (interruption, latency, call resolution)
   - Latency detail + stub dual-track waveform
   - Transcript / turns

## API

- `GET/POST /api/agents`, `GET/PATCH/DELETE /api/agents/{id}`
- `POST /api/execution/runs` accepts optional `agent_id`
- Conversations include `metrics_summary` and `timeline`
- Durable snapshot: `artifacts/execution-runs/{id}/run.json` (+ `inference_set.jsonl`)

## Notes

- Metrics and waveform are fixture/synthetic for this MVP (no real audio playback).
- Seed agents: **Mock text agent**, **ACC voice fixture agent**.
