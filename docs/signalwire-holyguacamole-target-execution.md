# Holy Guacamole SignalWire target execution

ConversationAgentEvals can evaluate the public Holy Guacamole drive-thru demo at
`https://holyguacamole.signalwire.me/` as an opt-in external voice target.

The SignalWire demo exposes a browser WebRTC SDK flow rather than a server-side
Daily room. CAE therefore uses the same target/tester/evidence contract added
for public voice targets, but the executor is a gated Chromium browser runner:

```text
CAE API
  -> scripts/signalwire_holyguacamole_smoke.mjs
      -> load https://holyguacamole.signalwire.me/
      -> request public guest token from /get_token
      -> inject current-run tester audio as the browser microphone
      -> dial the public SignalWire AI agent
      <- remote WebRTC audio stream and page/order events
  <- caller transcript, timing, result JSON, caller audio, target audio
  -> standard CAE run, timeline, recording metadata, and vCon pipeline
```

## Safety gate

Live public execution is disabled unless both gates are explicit:

- API runs require `CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE=1`.
- Standalone smoke runs require `SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC=1`.

The target URL is allowlisted to `https://holyguacamole.signalwire.me/`. Endpoint
URLs with credentials, ports, query strings, fragments, or alternate paths are
rejected. Guest tokens returned by the public page are never persisted in CAE
artifacts.

## Smoke command

```bash
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC=1 \
npm run test:signalwire-holyguacamole-smoke -- \
  --caller-text "I would like one chicken taco and a small drink."
```

The script writes `result.json`, `transcript.txt`, caller audio, and captured
target audio under `artifacts/signalwire-holyguacamole-smoke/`. Page status and
order text are retained as page events only; they are not labeled as agent
speech or used for scoring.

On macOS the script can synthesize caller audio with `say`. In other runtimes,
provide `--caller-audio <path>` or configure `KOKORO_BASE_URL`. The runner fails
preflight instead of injecting silence when real caller speech cannot be supplied.

## CAE run

Use the seeded `holyguacamole-signalwire-agent` target, or create a voice target
with `target=signalwire_holy_guacamole` and
`connection.endpoint_url=https://holyguacamole.signalwire.me/`.

Defaults:

| Field | Value |
| --- | --- |
| mode | `pipecat_webrtc` |
| tester | `pipecat_tester` |
| executor | `signalwire_public_browser` |
| audio transport | `signalwire_browser_webrtc` |

The run fails closed if the page, token endpoint, WebRTC connection, injected
caller media, or remote audio capture cannot complete. It does not fall back to
fixtures or saved transcripts.

This target currently runs one caller turn per browser call (`max_exchanges=1`).
Until remote speech ASR is wired for the captured WebM, CAE does not score or
vCon-label page status/order text as agent speech.
