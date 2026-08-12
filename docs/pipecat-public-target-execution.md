# Public Pipecat target execution

## Purpose

ConversationAgentEvals (CAE) can evaluate the public demo at `https://www.pipecat.ai/` as a real external voice target. The executor does not automate a browser. It asks the public demo to start the selected bot, joins the returned ephemeral Daily room with Pipecat's server-side Daily transport, sends a newly synthesized caller utterance, and captures the target's current-run response.

This is distinct from the optional Playwright smoke in `scripts/pipecat_public_voice_smoke.mjs`. The smoke checks the public browser experience. The target executor supplies evidence to CAE's normal run, scoring, timeline, and vCon flow.

## User flow

1. Open **Targets**.
2. Select the seeded **Pipecat public demo** target, or create a voice target of type **Pipecat demo**. Its endpoint is fixed to `https://www.pipecat.ai/`.
3. Choose **Try it Out**.
4. Select a benchmark scenario and start **Run Evaluation**.
5. CAE submits the scenario's opening user turn as synthesized speech, waits for the public target's response, then displays the run using the same result surfaces as other executions.

The v1 executor runs one caller turn and one target response per scenario. Multi-exchange simulation, interruption testing, and configurable public bot selection are follow-up capabilities rather than implied behavior.

## Execution contract

The target has these launch defaults:

| Field | Value |
| --- | --- |
| target | `pipecat_public_demo` |
| mode | `pipecat_webrtc` |
| tester | `pipecat_tester` |
| executor | `pipecat_public_daily` |
| audio transport | `pipecat_daily_webrtc` |
| public bot | `10-gradium` |

The API rejects incompatible executor and transport combinations. The target endpoint is allowlisted and cannot contain credentials, query parameters, or fragments.

## Runtime sequence

```text
CAE API
  -> local Pipecat service: caller text + public bot id
      -> Kokoro: synthesize a new caller WAV
      -> pipecat.ai /api/start: request an ephemeral Daily room and token
      -> DailyTransport: join as "CAE Pipecat tester"
      -> RTVI client-ready
      -> public bot greeting completes
      -> publish caller PCM in real-time chunks
      <- target audio + RTVI transcription events
  <- caller/target WAV, transcript, connection state, and latency
  -> standard CAE evaluation, timeline, artifact, and vCon pipeline
```

The public room URL and token remain inside the Pipecat service call. They are not returned to the CAE API, written to artifacts, placed in vCon metadata, or included in the saved run snapshot.

## Evidence and provenance

A successful conversation contains:

- the public bot's final recognition of the caller utterance;
- the target's response transcript assembled from final RTVI fragments;
- the exact synthesized caller WAV used in the run;
- response-only target audio captured from the remote Daily participant;
- caller-audio-to-first-target-audio latency when observable;
- connection milestones: room connected, target joined, bot ready, and response complete;
- normal CAE evaluation findings, timeline, recording metadata, and vCon export.

The run is labeled as a live external connection using direct Daily WebRTC and current-run synthetic caller media. It is not labeled as browser evidence, a phone call, SIP, saved replay, or fixture-backed scoring.

## Configuration and readiness

The API and Pipecat service must share `REFERENCE_AGENT_INTERNAL_TOKEN`. The API uses `PIPECAT_SERVICE_URL` to reach the Pipecat service. The Pipecat service requires a reachable `KOKORO_BASE_URL` and its configured Kokoro model and tester voice.

The Pipecat image installs the `daily` transport extra. No Node.js, Playwright, Chromium, microphone device, or browser permission is required for this executor.

Because the destination is a public external demo, runs also require outbound access to `www.pipecat.ai` and Daily. Availability, agent identifiers, protocol details, capacity, and behavior remain controlled by those services.

## Failure behavior

The conversation fails without fabricated evidence when any required stage cannot complete, including:

- caller speech synthesis failure;
- failure to obtain a valid ephemeral room and token;
- room connection or remote-participant timeout;
- missing bot readiness;
- no final caller transcription;
- no final target transcript;
- no captured target response audio; or
- response timeout.

Public-service exceptions returned to the API are intentionally generic so room credentials cannot leak through error strings. The direct executor never falls back to a saved transcript or browser smoke artifact.

## Verification strategy

Automated tests cover target validation and defaults, executor/transport compatibility, Pipecat-service response parsing, artifact persistence, transcript conversion, and run provenance. A live opt-in check should also confirm that the current public service accepts a direct Daily participant and returns both a transcription and response audio.

The browser smoke remains useful as a separate compatibility check because it exercises the public site's own UI and browser transport. Its success is not used as evidence for a CAE target evaluation.

## Acceptance criteria

- The target appears under **Targets** as a public voice target.
- **Try it Out** opens the normal run configuration with the direct Daily defaults.
- **Run Evaluation** is enabled without checking for Node.js or Chromium.
- A successful run contains current-run caller and target transcript turns plus target response audio.
- The result exposes direct-WebRTC provenance and never claims browser, SIP, or phone execution.
- Ephemeral Daily credentials do not appear in API responses or saved artifacts.
- A missing external dependency produces a failed conversation, not replayed or synthetic target evidence.
