# Runs MVP

Execute → analyze loop for ConversationAgentEvals.

## Flow

1. Register **agent targets** under `/targets` (file-backed JSON in `artifacts/agents/`; legacy `/agents` redirects here).
2. Launch an evaluation from `/runs` (pick an agent target; live OpenAI or built-in mock/fixture).
3. Open `/runs` for the list, then `/runs/[executionRunId]` for analysis:
   - Metric summary tiles (interruption, latency, call resolution)
   - Latency detail + stub dual-track waveform
   - Transcript / turns

## API

- `GET/POST /api/agents`, `GET/PATCH/DELETE /api/agents/{id}` (registry still named agents in the API)
- `POST /api/execution/runs` accepts optional `agent_id`
- Conversations include `metrics_summary` and `timeline`
- Durable snapshot: `artifacts/execution-runs/{id}/run.json` (+ `inference_set.jsonl`)

## Notes

- Metrics and waveform are fixture/synthetic for this MVP (no real audio playback).
- Built-in **testing targets**: **Mock text target**, **ACC voice fixture target**.
- Custom targets are connections toward systems under test (for personas/runs to hit an endpoint).
