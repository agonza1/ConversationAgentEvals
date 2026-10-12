# Speech providers for built-in voice evaluations

In **Settings → Speech · STT / TTS**, choose STT and TTS independently,
enter model/voice IDs, then **Save speech settings**. Run agent displays a
read-only summary linking back to Settings; its dependency preflight checks
readiness before queueing. Saving a selection does not start a call or verify
provider access, credits or health. Choices apply deployment-wide to new
**built-in two-agent voice runs**, including the ASSERT case queue.

| Stage | Local default | Deepgram | Cartesia |
| --- | --- | --- | --- |
| STT | rtc-asr, service-discovered model | Nova (`nova-3`), Listen v1 | Ink (`ink-2`), manual STT WebSocket |
| TTS | Kokoro | Aura, model ID per voice | Sonic (`sonic-3.6`), account voice IDs |

All nine combinations are configurable. Caller and agent must use distinct
voices. Deepgram's voice **is** the Aura model ID; Cartesia voice IDs come from
your account's voice library. Pin supported model snapshots for repeatability.
The local rtc-asr model is managed by that service's deployment, not by this
console; its model field is read-only. Reload saved settings discards unsaved
form edits without making a write. Existing runs are not modified.

## Credentials and defaults

Set keys in the **Pipecat worker's server environment** and restart that
worker. The settings API/browser and run evidence contain only non-secret
provider/model/voice selections. For Deepgram STT + Cartesia TTS:

```dotenv
CAE_STT_PROVIDER=deepgram
DEEPGRAM_API_KEY=<server-secret>
DEEPGRAM_STT_MODEL=nova-3
CAE_TTS_PROVIDER=cartesia
CARTESIA_API_KEY=<server-secret>
CARTESIA_TTS_MODEL=sonic-3.6
CARTESIA_TESTER_VOICE=<caller-voice-id>
CARTESIA_TARGET_VOICE=<different-agent-voice-id>
CARTESIA_API_VERSION=2026-03-01
CAE_SPEECH_LANGUAGE=en
CAE_STT_FINAL_TIMEOUT_SECONDS=20
```

Compose's `env_file: .env` passes these defaults to the worker. Console
selections persist alongside the OAuth store in `speech-settings.json` and
take precedence over environment defaults. `GET/PUT
/api/execution/speech-settings` use the same selection. Unknown providers,
arbitrary endpoints, unknown fields and equal voices are rejected. Remove
the settings file to restore environment defaults.

Preflight checks cloud **credential presence**, not authentication, credits,
or access to voices/models. Real vendor requests check those during execution;
failures stop the run without silently selecting another provider.

## Streaming and timing

The existing Pipecat graph, paced 20 ms media bridge, recordings and measured
latency timestamps remain in use. STT receives actual 16 kHz mono PCM16 audio
as it arrives. Silero marks speech boundaries; clauses accumulate until CAE's
speech-end frame. Each detected utterance uses a fresh cloud STT socket:

- Deepgram `is_final` marks a segment. `CloseStream` drains buffered audio,
  and final `Metadata` releases the barrier. Endpointing is disabled because
  Silero owns boundaries. This adapter supports **Listen v1 / Nova**;
  Flux/Listen v2 uses a different protocol and is not supported.
- Cartesia's **manual** `/stt/websocket` endpoint uses the literal `finalize`
  command and waits for `flush_done`. Automatic turn detection is not used.

TTS aggregates the first clause and later sentences, streaming PCM16 WAV
responses at 24 kHz from Deepgram `/v1/speak` or Cartesia `/tts/bytes`.
This is HTTP response streaming per text chunk, not a persistent TTS WebSocket.
Graphs record provider, model, voice, protocol and measured timings. STT text
is recognized audio; LLM text is never substituted for a transcript.

## Scope

Public Daily, SignalWire, ACC/SIP and other remote targets keep their existing
media configuration. These options do not reconfigure remote voice agents.
Offline tests verify the vendor protocols and PCM routing, not cloud latency.

## Official protocol references

- [Deepgram streaming STT](https://developers.deepgram.com/reference/speech-to-text/listen-streaming)
- [Deepgram stream drain](https://developers.deepgram.com/docs/close-stream)
- [Deepgram TTS](https://developers.deepgram.com/reference/text-to-speech/speak-request)
- [Cartesia manual streaming STT](https://docs.cartesia.ai/api-reference/stt/websocket)
- [Cartesia streamed TTS](https://docs.cartesia.ai/api-reference/tts/bytes)
- [Cartesia versioning/authentication](https://docs.cartesia.ai/use-the-api/api-conventions)
