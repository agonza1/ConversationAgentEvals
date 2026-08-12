# Holy Guacamole SignalWire target execution

ConversationAgentEvals can evaluate the public Holy Guacamole drive-thru demo at
`https://holyguacamole.signalwire.me/` as an opt-in external voice target.

This is a direct/no-headless-browser executor. CAE follows the public Pipecat
target pattern: the API asks the Pipecat service to run a target-specific
outbound adapter, the adapter discovers a fresh SignalWire guest token/address,
sends current-run Kokoro tester audio through the SignalWire SDK using Node
WebRTC primitives, and returns current-run media/provenance to the normal CAE
run, timeline, recording, and vCon pipeline.

```text
CAE API
  -> Pipecat service /signalwire-holyguacamole/duplex
      -> synthesize caller audio with Kokoro
      -> GET https://holyguacamole.signalwire.me/get_token?voice=...
      -> SignalWire SDK dial /public/holyguacamole?channel=audio
      -> send in-memory tester audio track
      <- capture remote SignalWire WebRTC audio with RTCAudioSink
  <- caller audio, target audio, timing, redacted provenance
  -> standard CAE run, timeline, recording metadata, and vCon pipeline
```

## Discovery

The public page loads `https://cdn.signalwire.com/@signalwire/js@4.0.0-rc.0`
and `/app.js?v=20`. Its first call fetches `/get_token?voice=<voice-id>`.
The response contains only:

- `token`: an ephemeral SignalWire guest token used in memory by the SDK
- `address`: currently `/public/holyguacamole?channel=audio`

CAE does not persist the token and does not include it in result JSON, logs, or
vCon artifacts.

## Safety gate

Live public execution is disabled unless these are configured:

- API and Pipecat service: `CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE=1`
- API and Pipecat service: matching `REFERENCE_AGENT_INTERNAL_TOKEN`
- Pipecat service: `KOKORO_BASE_URL`

The target URL is allowlisted to `https://holyguacamole.signalwire.me/`. Endpoint
URLs with credentials, ports, query strings, fragments, or alternate paths are
rejected by the direct runner.

## CAE run

Use the seeded `holyguacamole-signalwire-agent` target, or create a voice target
with `target=signalwire_holy_guacamole` and
`connection.endpoint_url=https://holyguacamole.signalwire.me/`.

Defaults:

| Field | Value |
| --- | --- |
| mode | `pipecat_webrtc` |
| tester | `pipecat_tester` |
| executor | `signalwire_public_direct` |
| audio transport | `signalwire_direct_webrtc` |

Example local run shape:

```bash
CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE=1 \
REFERENCE_AGENT_INTERNAL_TOKEN=dev-shared-token \
KOKORO_BASE_URL=http://localhost:8880 \
npm run dev
```

Then start an execution run for `holyguacamole-signalwire-agent` with
`max_exchanges=1`.

## Optional browser smoke

`scripts/signalwire_holyguacamole_smoke.mjs` remains an optional compatibility
smoke for the public website UI. It is not the canonical CAE executor and its
artifacts are not used as target-evaluation evidence.

```bash
PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers \
SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC=1 \
npm run test:signalwire-holyguacamole-smoke -- \
  --caller-text "I would like one chicken taco and a small drink."
```

## Limitations

This target currently runs one caller turn per direct call (`max_exchanges=1`).
Remote target audio is preserved as current-run WAV evidence. Until remote speech
ASR is wired for the captured SignalWire audio, CAE marks semantic scoring as
`needs_review` instead of labeling UI/status text as agent speech.
