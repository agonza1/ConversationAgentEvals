# Execution-time audio: Pipecat small WebRTC (+ SIP Verto later)

ConversationAgentEvals can stream caller/target audio **during Execute** and emit
**vCon** recording + transcription evidence without requiring Agentic Contact Center,
FreeSWITCH, or live SIP in the default install.

Tracking: [#98](https://github.com/agonza1/ConversationAgentEvals/issues/98).

## What shipped (this PR)

| Piece | Status |
| --- | --- |
| Local Pipecat small WebRTC send/receive hooks | **Available** — in-process mockable transport; no browser/Pipecat process for CI |
| `POST /api/execution/runs` mode `pipecat_webrtc` | **Available** — drives `PipecatTesterAgentRunner` over local WebRTC hooks |
| Recording + transcription capture | **Available** — `AudioRecordingHandle` + dialog turns during the session |
| vCon export on conversation rows | **Available** — reuses `benchmark_service._vcon_export` shape (dialog + analysis + recording attachment) |
| `GET /api/execution/audio/capabilities` | **Available** — advertises transports, vCon capture, honesty boundary |
| FreeSWITCH Verto outbound SIP | **Deferred** — `FreeSwitchVertoSipTransport` extension stub only |

Default CI does **not** require FreeSWITCH, PSTN, or a live Pipecat service.

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

## FreeSWITCH Verto extension points

`FreeSwitchVertoSipTransport` implements the same `ExecutionAudioTransport` protocol and
raises `NotImplementedError` with guidance. Next slice should:

1. negotiate Verto WebSocket login/call against FreeSWITCH;
2. bridge SIP media into the same Pipecat small WebRTC send/receive hooks;
3. emit the same `AudioRecordingHandle`, `TranscriptionTurn`, and vCon shape.

Do not wire Verto into default CI.

## Honesty

This slice proves:

- execution-time local WebRTC-shaped audio send/receive hooks;
- recording URI/hash handles;
- transcription dialog capture;
- CAE-compatible vCon export on the execution path.

It does **not** prove:

- a browser peer connection against `apps/pipecat`;
- live ASR/TTS vendors;
- FreeSWITCH Verto / SIP / PSTN;
- that benchmark scoring itself came from live media (cancellation-rescue scoring still
  reuses the offline fixture evidence path until a live SUT proof bundle lands).
