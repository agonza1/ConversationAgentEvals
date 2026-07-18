# Runs MVP

Execute → analyze loop for ConversationAgentEvals.

## Model: Target → Tester → Executor → Evidence

Keep these layers separate:

| Layer | Meaning | Examples |
| --- | --- | --- |
| **Target** | Agent under test + destination | Built-in sample voice agent; SIP URI; E.164 phone; Browser/WebRTC agent |
| **Tester** | Simulated caller + scenario behavior | Scenario policy, Pipecat tester acts |
| **Executor** | System that conducts the call | Built-in local audio loop; ACC browser / SIP / phone |
| **Evidence** | Where scored material comes from | Live capture; saved conversation replay; generated sample |

`cae_local_audio_loop`, `acc_sip`, `acc_phone`, and `acc_browser_webrtc` are **executors/transports**, not destinations.

- **ACC** owns live SIP, PSTN, FreeSWITCH, Verto, and production media.
- **CAE** owns scenarios, tester policy, adapters, evidence normalization, and ASSERT/benchmark.
- Saved conversation replay is an **Eval evidence mode**, not an Add Target destination.

Every execution run stores provenance:

- `target_kind`, `tester_kind`, `executor_kind`, `media_source`
- `live_external_connection`, `saved_evidence`, `synthetic_audio`
- Built-in sample voice honesty label: `Built-in sample agent · local audio loop · no phone or SIP call`

## Flow

1. Register **agent targets** under `/targets` (file-backed JSON in `artifacts/agents/`; legacy `/agents` redirects here).
2. Launch an evaluation from `/runs` (pick a **destination** target, then an **executor**).
3. Open `/runs` for the list, then `/runs/[executionRunId]` for analysis:
   - Provenance + honesty label
   - Metric summary tiles (interruption, latency, call resolution)
   - Latency detail + stub dual-track waveform
   - Transcript / turns

## Add Target destinations

Voice destinations:

- **Built-in sample agent** — creatable now; default executor is the CAE local audio loop
- **Browser/WebRTC**, **SIP**, **Phone** — enter the ACC base URL in Add Target and choose **Test connection**
- CAE probes ACC's official `/api/pipecat-media-engine/readiness` route and enables each destination only when that adapter reports ready
- A connected ACC can enable browser/SIP while Phone remains disabled when its PSTN trunk is not ready

SIP and phone are never the same field: SIP URI only vs E.164 only.

## API

- `GET/POST /api/agents`, `GET/PATCH/DELETE /api/agents/{id}` (registry still named agents in the API)
- `POST /api/execution/runs` accepts optional `agent_id` and `executor_kind`
- `GET /api/execution/acc-connection` — cached ACC readiness for live destinations
- `POST /api/execution/acc-connection/test` — test an ACC base URL against its official media-readiness route
- Conversations include `metrics_summary` and `timeline`
- Durable snapshot: `artifacts/execution-runs/{id}/run.json` (+ `inference_set.jsonl`) with `provenance`

## Notes

- Metrics and waveform may still be fixture/synthetic for parts of this MVP.
- Built-in sample voice runs use the local audio loop (Pipecat is an implementation detail).
- Custom targets are destinations toward systems under test; executors are chosen at launch.
