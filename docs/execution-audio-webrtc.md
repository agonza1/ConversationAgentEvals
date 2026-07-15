# Execution-time audio: Pipecat SmallWebRTC (+ SIP Verto later)

ConversationAgentEvals can stream caller and target audio **during Execute** without
requiring Agentic Contact Center, FreeSWITCH, or live SIP in the default install.

Tracking: [#98](https://github.com/agonza1/ConversationAgentEvals/issues/98) (realtime media phases).

## What shipped (this PR)

| Piece | Status |
| --- | --- |
| Local Pipecat SmallWebRTC-shaped duplex PCM hooks | **Available** — in-process loopback; no browser/Pipecat process required for CI |
| `POST /api/execution/runs` mode `voice_webrtc` | **Available** — reuses ACC audio-plan + fixture scoring path |
| `audio_transport=local_pipecat_webrtc` on `voice_fixture` | **Available** — opt-in media hooks on the existing fixture runner |
| `GET /api/execution/audio/capabilities` | **Available** — advertises transports and honesty boundary |
| FreeSWITCH Verto outbound SIP | **Deferred** — extension point only (`sip_verto`) |

Default CI does **not** require FreeSWITCH, PSTN, or a live Pipecat service.

## How it maps to ACC’s Pipecat + Verto + WebRTC pattern

ACC’s live media path (conceptual):

```text
Tester / fixture PCM
  -> Pipecat SmallWebRTC (duplex browser or server media)
  -> FreeSWITCH Verto (outbound SIP leg)
  -> SIP / PSTN target
```

CAE’s first slice:

```text
AccAudioFixtureScheduler (fixture order, pacing, provenance)
  -> WebRtcBackedVoiceTarget.inject_audio
  -> LocalPipecatSmallWebRtcSession
       send_pcm  (caller -> target)
       inject_remote_pcm + receive_pcm  (target -> caller hooks)
  -> conversation.audio_session proof on the execution run

Later:
  LocalPipecatSmallWebRtcSession
    <-> SipVertoOutboundExtension (FreeSWITCH Verto dial)
    <-> SIP destination
```

CAE still owns scheduling, seed, expected caller act, and evidence normalization.
The transport owns only media session shape and frame counters. Live Verto dialing
stays optional and out of band for default installs.

## API usage

Discover transports:

```bash
curl -s localhost:8025/api/execution/audio/capabilities | jq .
```

Run Execute with local WebRTC audio hooks (cancellation-rescue / audio plan):

```bash
curl -s -X POST localhost:8025/api/execution/runs \
  -H 'content-type: application/json' \
  -d '{
    "suite_id": "call-center-voice-ai",
    "scenario_ids": ["cancellation-rescue"],
    "mode": "voice_webrtc",
    "user_id": "exec-user",
    "project_id": "exec-project",
    "evaluate": true
  }'
```

Equivalent opt-in on the fixture mode:

```json
{
  "mode": "voice_fixture",
  "audio_transport": "local_pipecat_webrtc",
  "scenario_ids": ["cancellation-rescue"]
}
```

Completed conversations include `audio_session` with:

- negotiated local offer/answer stubs
- `frames_sent` / `frames_received` / byte counters
- per-step media proof from fixture injection
- `extension_points.sip_verto` describing the next SIP plug-in

## Code map

| Module | Role |
| --- | --- |
| `apps/api/app/services/execution_audio.py` | Session protocol, local SmallWebRTC loopback, SIP stub, WebRTC-backed target |
| `apps/api/app/services/execution_runner.py` | Wires transport into voice Execute |
| `apps/api/app/schemas/execution.py` | `voice_webrtc` mode + `audio_transport` field |
| `apps/api/app/routes/execution.py` | `/audio/capabilities` |
| `apps/pipecat/server.py` | Existing live SmallWebRTC presenter path (presentation demos) — not required for Execute CI |

Reusable contracts from the ACC / cancellation-rescue work:

- `AccAudioFixtureScheduler` / `AccAudioPlan`
- `AccRealtimeTargetAdapter` media input protocol
- Checked-in `docs/examples/agentic-contact-center-audio-plan.json`

## Next: FreeSWITCH Verto outbound SIP

When enabling SIP:

1. Keep `LocalPipecatSmallWebRtcSession` (or a live Pipecat SmallWebRTC peer) as the duplex PCM edge.
2. Implement `SipVertoOutboundExtension.create_session` to dial via FreeSWITCH Verto using env such as `FREESWITCH_VERTO_URL` + `SIP_DESTINATION`.
3. Bridge the same 16 kHz mono PCM16 frames used by `AccMediaInputStream` / execution audio.
4. Gate behind `audio_transport=sip_verto` and keep default CI on `local_pipecat_webrtc` or `none`.
5. Do not make FreeSWITCH a required install dependency of ConversationAgentEvals.

Until that lands, `audio_transport=sip_verto` is rejected with a clear deferred error.

## Honesty boundary

This slice proves:

- execution can open a duplex audio session shaped like Pipecat SmallWebRTC
- fixture injection paces caller PCM and receives target-side frames through that session
- proof is attached to Execute conversations

It does **not** yet prove:

- a browser RTCPeerConnection against `apps/pipecat`
- live ASR / TTS
- FreeSWITCH Verto registration or outbound SIP
- barge-in against a production media server
