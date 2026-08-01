# Pipecat Public Voice Smoke

CAE includes an opt-in browser smoke for the public Pipecat demo at
`https://www.pipecat.ai/`. It is intentionally excluded from the offline CI suite because it
contacts the public Pipecat site, opens a Daily WebRTC room, and depends on the current
public demo configuration.

Run it after installing dependencies and Chromium:

```bash
npm run install:e2e
npm run test:pipecat-public-voice-smoke
```

The command drives Chromium with an explicit microphone permission grant and Chromium fake
media by default. To use the real browser microphone path, run:

```bash
PIPECAT_PUBLIC_HEADED=1 \
PIPECAT_PUBLIC_USE_REAL_MIC=1 \
npm run test:pipecat-public-voice-smoke -- --headed --use-real-mic
```

When using a real mic, the browser and operating system may ask for microphone access.
Approve that prompt manually. The script does not persist device IDs, secrets, room tokens,
or microphone identifiers.

## Results

Each run writes:

- `artifacts/pipecat-public-voice-smoke/latest.json`
- `artifacts/pipecat-public-voice-smoke/<run-id>/result.json`
- `artifacts/pipecat-public-voice-smoke/<run-id>/transcript.txt`

Successful results include:

- connection status for the public page, `/api/start`, and Daily room check;
- start and end timestamps;
- page-load, start-endpoint, room-check, transcript, and total latency metrics;
- the whole transcript visible in the public Pipecat transcript panel;
- target and tester provenance showing `real_external_public_target`, `fixture_backed:
  false`, `mock_execution: false`, and token redaction.

Blocked results are expected when the public site changes or when browser/media automation is
unavailable. The artifact uses explicit reason codes such as `no_public_voice_endpoint`,
`browser_permission_denied`, `connection_timeout`, `target_unreachable`,
`unsupported_media_path`, `target_changed`, or `transcript_unavailable`.

This smoke validates the public demo connection only. It does not assert CAE's local Pipecat
reference target, SIP, PSTN, or private ACC behavior.
