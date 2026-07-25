# Execution-time audio: two-agent Pipecat local duplex (+ SIP Verto later)

ConversationAgentEvals can stream caller/target audio **during Execute** and emit
**vCon** recording + transcription evidence without requiring Agentic Contact Center,
FreeSWITCH, or live SIP in the default install.

Tracking: [#98](https://github.com/agonza1/ConversationAgentEvals/issues/98), [#103](https://github.com/agonza1/ConversationAgentEvals/issues/103).

## Execution model

Every run snapshots four independent roles:

- **Target**: the agent under test and its destination, such as an HTTP endpoint, built-in
  agent, browser/WebRTC agent, SIP URI, or phone number.
- **Tester**: the simulated user or caller that drives the scenario.
- **Executor**: the local text runner, CAE audio loop, saved-evidence replay, or future live
  media adapter that conducts the test.
- **Evidence**: the responses, media capture, transcript, actions, and state that get scored.

A transport is not a target, and replayed evidence is not a new agent target. Targets are
registered under `/targets`; runs launch from `/runs` and persist snapshots under
`artifacts/execution-runs/{id}/`.

## Public API (unified naming)

| Field | Value | Notes |
| --- | --- | --- |
| Execution mode | `pipecat_webrtc` | Runs independent tester and target Pipecat graphs over an in-process duplex frame bus |
| Local transport | `pipecat_small_webrtc` | Default when mode is `pipecat_webrtc` |
| Deferred SIP transport | `freeswitch_verto_sip` | Rejected until `FreeSwitchVertoSipTransport` is implemented |

Legacy names from earlier drafts (`voice_webrtc`, `local_pipecat_webrtc`, `sip_verto`) are **not** accepted on the API.

## What shipped

| Piece | Status |
| --- | --- |
| Local Pipecat reference execution | **Available when configured** — separate tester and target participants; no browser peer |
| `POST /api/execution/runs` mode `pipecat_webrtc` | **Available when configured** — selected catalog scenario through tester rtc-asr → LLM → Kokoro and target rtc-asr → LLM → Kokoro |
| Recording + transcription capture | **Available** — current-run WAV, rtc-asr transcript, timings, and dialog turns |
| Live run feedback | **Available** — polling exposes directional evidence; an optional token-scoped browser receives live run audio over server-send-only WebRTC |
| vCon export on conversation rows | **Available** — reuses `benchmark_service._vcon_export` shape; Launch UI shows summary |
| Launch UI mode label | **Honest** — current-run local duplex media; browser/SIP/PSTN not proven |
| First-class **`/voice`** page | **Available** — dedicated Voice eval launch + conversation/vCon/recording results |
| `GET /api/execution/audio/capabilities` | **Available** — advertises transports, vCon capture, honesty boundary |
| FreeSWITCH Verto outbound SIP | **Deferred** — `FreeSwitchVertoSipTransport` extension stub only |

Default CI uses fakes around the provider boundaries and does **not** require FreeSWITCH or PSTN.
`npm run test:reference-voice-smoke` is the opt-in real-service proof.

## How it maps to ACC’s Pipecat + Verto + WebRTC pattern

ACC’s live media path (conceptual):

```text
External caller PCM
  -> Pipecat small WebRTC (duplex media)
  -> FreeSWITCH Verto (outbound SIP leg)
  -> SIP / PSTN target
```

CAE’s local two-agent path:

```text
scenario + rubric -> Pipecat tester graph (rtc-asr -> LLM -> Kokoro)
                         |
                         | raw OutputAudioRawFrame
                         v
                  in-process duplex frame bus
                         |
                         | raw InputAudioRawFrame
                         v
                    Pipecat target graph (rtc-asr -> LLM -> Kokoro)
                         |
                         +---- raw frames back to tester rtc-asr
  -> streamed directional evidence copies (NDJSON, not the media transport)
  -> execution_vcon.build_execution_vcon
       parties / dialog / analysis[execution_audio_capture]
       attachments[type=recording]
  -> conversation row on the execution run (inference_set.jsonl)

Later:
  LocalPipecatSmallWebRtcTransport
    <-> FreeSwitchVertoSipTransport (same Protocol)
    <-> SIP destination
```

CAE supplies the scenario/rubric, enforces the turn/time bound, and normalizes evidence.
The tester LLM chooses each caller turn from the rubric and observed target audio; it does
not use `AccAudioFixtureScheduler`, a scripted transcript, or one-shot caller WAV requests.
Pipecat owns both graph executions and the local media hop. The API request contains session
control only; evidence WAV copies stream back after each exchange for live playback, recording,
and vCon capture.
The tester and target use distinct Kokoro voices (`KOKORO_TESTER_VOICE` and
`KOKORO_TARGET_VOICE`) so their recorded turns are audibly distinguishable.
Live Verto dialing stays optional and out of band for default installs.

## vCon wiring

During `pipecat_webrtc` execution:

1. The tester graph emits Kokoro `OutputAudioRawFrame` data into the local duplex bus.
2. The target graph receives the same bytes as `InputAudioRawFrame`, then returns its Kokoro frames through the reverse direction.
3. Each direction retains LLM output, opposite-side rtc-asr receipt, timing, and frame metadata in `TranscriptionTurn` rows.
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

`POST /api/execution/runs` accepts an optional `agent_id` plus explicit `tester_id` and
`executor_id`. The legacy `/api/agents` registry routes remain the underlying API for targets.
Completed conversations include metric summaries and timelines, while
`artifacts/execution-runs/{id}/inference_set.jsonl` stores completed rows.

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
live exchange**. An active run can also issue a short-lived owner-scoped listener token;
the listener negotiates a receive-only browser audio transceiver and never requests a mic.

## Code map

| Module | Role |
| --- | --- |
| `apps/api/app/services/execution_audio.py` | Transport protocol, local SmallWebRTC loopback, Verto stub, target adapter |
| `apps/api/app/services/execution_vcon.py` | vCon export from recording + transcription turns |
| `apps/api/app/services/execution_runner.py` | Wires transport into `pipecat_webrtc` Execute |
| `apps/api/app/services/reference_generalist_agent.py` | Consumes streamed session evidence and persists live events, recordings, and directional receipts |
| `apps/api/app/schemas/execution.py` | `pipecat_webrtc` mode + `audio_transport` field |
| `apps/api/app/routes/execution.py` | Dependency preflight, owner-scoped listener tokens, and confined WebRTC signaling proxy |
| `apps/pipecat/server.py` | Runs both independent Pipecat graphs, the duplex frame bus, and server-send-only listener peers |

The fixture scheduler and checked-in ACC audio plan remain legacy replay paths; they are not
used by the `builtin_sample_voice` primary execution path.

For external HTTP targets, CAE sends the message, OpenAI-style history, and scenario metadata,
then reads reply text from a configured response path. Credentials are referenced by an opaque
`secret_ref` and resolved only from the `CAE_HTTP_TARGET_SECRET_*` namespace; raw secrets are
never stored in target definitions.

## Opt-in real-service smoke

The default CI suite is fully offline. It replaces provider boundaries with local fakes while
executing both Pipecat `Pipeline` graphs, both frame directions, evidence streaming, recording,
and vCon normalization. CI never contacts OpenAI, SIP, FreeSWITCH, a softphone, or a browser peer.

To validate the configured real path explicitly:

```bash
API_BASE_URL=http://127.0.0.1:8025 \
RTC_ASR_BASE_URL=http://127.0.0.1:8080 \
PIPECAT_SERVICE_URL=http://127.0.0.1:8110 \
KOKORO_BASE_URL=http://127.0.0.1:8880 \
REFERENCE_AGENT_INTERNAL_TOKEN='<shared-local-token>' \
npm run test:reference-voice-smoke
```

The API and Pipecat processes must share the token. Configure either `OPENAI_API_KEY` or the
OpenAI/Codex OAuth connection before launch. This smoke makes real model calls for both agents
and can incur provider cost; it is intentionally excluded from CI. A browser listener is
optional and has no effect on run completion or evidence capture.

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

- two independent Pipecat graphs exchanging raw audio through an in-process duplex frame bus;
- recording URI/hash handles;
- transcription dialog capture;
- CAE-compatible vCon export on the execution path;
- duplex frame/byte/timing proof on `conversation.audio_session`;
- live rtc-asr, configured LLM, and Kokoro provider boundaries when the opt-in smoke is run;
- observed text turns plus an optional owner-scoped receive-only browser WebRTC audio subscriber.

It does **not** prove:

- a browser microphone, browser target, or evaluation dependency on the listener;
- FreeSWITCH Verto / SIP / PSTN;
- barge-in against a production media server;
- production network conditions or an externally deployed target.
