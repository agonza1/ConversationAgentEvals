# Execution-time audio: Pipecat small WebRTC (+ SIP Verto later)

ConversationAgentEvals can stream caller/target audio **during Execute** and emit
**vCon** recording + transcription evidence without requiring Agentic Contact Center,
FreeSWITCH, or live SIP in the default install.

Tracking: [#98](https://github.com/agonza1/ConversationAgentEvals/issues/98), [#103](https://github.com/agonza1/ConversationAgentEvals/issues/103).

## Public API (unified naming)

| Field | Value | Notes |
| --- | --- | --- |
| Execution mode | `pipecat_webrtc` | Drives `PipecatTesterAgentRunner` over local WebRTC hooks |
| Local transport | `pipecat_small_webrtc` | Default when mode is `pipecat_webrtc` |
| Deferred SIP transport | `freeswitch_verto_sip` | Rejected until `FreeSwitchVertoSipTransport` is implemented |

Legacy names from earlier drafts (`voice_webrtc`, `local_pipecat_webrtc`, `sip_verto`) are **not** accepted on the API.

## What shipped

| Piece | Status |
| --- | --- |
| Local Pipecat reference execution | **Available when configured** — separate tester and target participants; no browser peer |
| `POST /api/execution/runs` mode `pipecat_webrtc` | **Available when configured** — Pipecat tester → rtc-asr → LLM → Kokoro target pipeline |
| Recording + transcription capture | **Available** — current-run WAV, rtc-asr transcript, timings, and dialog turns |
| Live run feedback | **Available** — polling exposes observed turns while the run is active; UI can reveal text or play authenticated current-run WAV segments |
| vCon export on conversation rows | **Available** — reuses `benchmark_service._vcon_export` shape; Launch UI shows summary |
| Launch UI mode label | **Honest** — local synthetic media and current-run evidence; browser/SIP/PSTN not proven |
| First-class **`/voice`** page | **Available** — dedicated Voice eval launch + conversation/vCon/recording results |
| `GET /api/execution/audio/capabilities` | **Available** — advertises transports, vCon capture, honesty boundary |
| FreeSWITCH Verto outbound SIP | **Deferred** — `FreeSwitchVertoSipTransport` extension stub only |

Default CI uses fakes around the provider boundaries and does **not** require FreeSWITCH or PSTN.
`npm run test:reference-voice-smoke` is the opt-in real-service proof.

## How it maps to ACC’s Pipecat + Verto + WebRTC pattern

ACC’s live media path (conceptual):

```text
Tester / fixture PCM
  -> Pipecat small WebRTC (duplex media)
  -> FreeSWITCH Verto (outbound SIP leg)
  -> SIP / PSTN target
```

CAE’s first slice:

```text
PipecatTesterAgentRunner
  -> ExecutionAudioTargetAdapter
  -> LocalPipecatSmallWebRtcTransport
       send_audio   (caller -> target)
       receive_audio (target -> caller)
       stop_recording + transcription turns
  -> execution_vcon.build_execution_vcon
       parties / dialog / analysis[execution_audio_capture]
       attachments[type=recording]
  -> conversation row on the execution run (inference_set.jsonl)

Later:
  LocalPipecatSmallWebRtcTransport
    <-> FreeSwitchVertoSipTransport (same Protocol)
    <-> SIP destination
```

CAE still owns scheduling, seed, expected caller act, and evidence normalization.
The transport owns media session shape, frame counters, and recording handles.
Live Verto dialing stays optional and out of band for default installs.

## vCon wiring

During `pipecat_webrtc` execution:

1. Caller TTS fixtures are sent through `send_audio` (outbound WebRTC hook).
2. Target/agent frames are pulled through `receive_audio` (inbound WebRTC hook).
3. Both directions append `TranscriptionTurn` rows (Caller / Agent).
4. Session close finalizes an `AudioRecordingHandle` (`uri`, `sha256`, `mime_type`, `duration_ms`).
5. `build_execution_vcon(...)` builds a payload with `conversation.dialog` + `call.recording_*` and
   calls the same `_vcon_export` helper used by benchmark/product flows.
6. The conversation record stores `vcon_export`, `vcon_export_summary`, and `recording`.

Analysis record type: `execution_audio_capture`.

Source format label: `pipecat_execution`.

## API usage

Discover transports:

```bash
curl -s localhost:8000/api/execution/audio/capabilities | jq .
```

Run Execute with local WebRTC + vCon capture (cancellation-rescue):

```bash
curl -s -X POST localhost:8000/api/execution/runs \
  -H 'content-type: application/json' \
  -d '{
    "suite_id": "call-center-voice-ai",
    "scenario_ids": ["cancellation-rescue"],
    "mode": "pipecat_webrtc",
    "user_id": "exec-user",
    "project_id": "exec-project",
    "evaluate": true
  }'
```

Completed conversations include `audio_session` with:

- negotiated local offer/answer stubs (`webrtc.offer_type` / `webrtc.answer_type`)
- `frames_sent` / `frames_received` / byte counters
- tester proof (`proof.recording`, `proof.transcription_turns`)
- `extension_points.freeswitch_verto_sip` describing the next SIP plug-in

While a run is active, each conversation's `live_events` array grows as the tester and
target turns are observed. Text executions emit the actual request/reply text. Reference
voice executions emit rtc-asr/LLM text plus one authenticated current-run WAV URL per
speaker turn. The Launch and Voice pages keep this hidden until the user chooses **Show
live exchange**; voice runs also expose **Unmute live conversation**.

## Code map

| Module | Role |
| --- | --- |
| `apps/api/app/services/execution_audio.py` | Transport protocol, local SmallWebRTC loopback, Verto stub, target adapter |
| `apps/api/app/services/execution_vcon.py` | vCon export from recording + transcription turns |
| `apps/api/app/services/execution_runner.py` | Wires transport into `pipecat_webrtc` Execute |
| `apps/api/app/schemas/execution.py` | `pipecat_webrtc` mode + `audio_transport` field |
| `apps/api/app/routes/execution.py` | `/audio/capabilities` and owner-scoped live WAV segments |
| `apps/pipecat/server.py` | Existing live SmallWebRTC presenter path (presentation demos) — not required for Execute CI |

Reusable contracts from the ACC / cancellation-rescue work:

- `AccAudioFixtureScheduler` / `AccAudioPlan`
- `AccRealtimeTargetAdapter` media input protocol
- Checked-in `docs/examples/agentic-contact-center-audio-plan.json`

## FreeSWITCH Verto extension points

`FreeSwitchVertoSipTransport` implements the same `ExecutionAudioTransport` protocol and
raises `NotImplementedError` with guidance. Next slice should:

1. negotiate Verto WebSocket login/call against FreeSWITCH (env: `FREESWITCH_VERTO_URL`, `SIP_DESTINATION`);
2. bridge SIP media into the same Pipecat small WebRTC send/receive hooks;
3. emit the same `AudioRecordingHandle`, `TranscriptionTurn`, and vCon shape.

Gate behind `audio_transport=freeswitch_verto_sip` and keep default CI on `pipecat_small_webrtc` or `none`.
Do not wire Verto into default CI.

## Honesty boundary

The built-in reference-agent slice proves:

- execution-time local WebRTC-shaped audio send/receive hooks;
- recording URI/hash handles;
- transcription dialog capture;
- CAE-compatible vCon export on the execution path;
- duplex frame/byte proof on `conversation.audio_session`.
- live rtc-asr, configured LLM, and Kokoro provider boundaries when the opt-in smoke is run;
- observed text turns and owner-scoped current-run WAV segments before terminal run status.

It does **not** prove:

- a browser peer connection against `apps/pipecat`;
- FreeSWITCH Verto / SIP / PSTN;
- barge-in against a production media server;
- production network conditions or an externally deployed target.
