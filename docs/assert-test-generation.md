# ASSERT test generation with CAE voice execution

Evaluation design now offers **ASSERT test generation** (the default UI choice)
and the existing **CAE custom generation** as explicit alternatives. There is no
automatic fallback between them. Both use the draft-generation provider/model
selected in Console Settings, including CAE's existing Codex connection or API-key
path. The generation choice does not change the target agent or its model.

## Workflow

1. Enter requirements and permissible boundaries in Evaluation design. Author
   behaviors manually or use CAE's existing **Generate draft checks/scenarios**
   button, then review/approve those behaviors. That first button still uses the
   custom CAE authoring prompt; it is not ASSERT taxonomy generation.
2. Select reviewed behavior IDs, choose **ASSERT test generation**, and click
   **Generate runnable case drafts**. CAE compiles the frozen selected behaviors
   into `taxonomy.json`; pinned `assert-ai==0.3.0` runs its native `test_set` stage.
   The UI generates three caller prompts per selected behavior, covering fixed
   normal/boundary/adversarial dimensions. The API also supports 1–5 cases per
   behavior. ASSERT assigns behavior/variant dimensions in its jobs;
   the model does not invent CAE behavior identities.
3. Review/edit the opening requests and expected outcomes. Approve the draft,
   **Save version**, then **Publish saved cases to Scenarios**. Existing immutable
   versioning, workspace publication permissions and explicit review confirmation
   remain in force. Generation operates on the unsaved editor snapshot and reads
   no other user's saved designs; publication requires the existing owner/editor
   access check.
4. **Choose a target and run**. CAE's existing target runner and voice transports
   execute the caller prompts; compatible adaptive testers produce subsequent
   caller turns. ASSERT generation neither prewrites the target's answers nor
   substitutes its upstream inference runner for CAE's voice execution.
5. Inspect the recorded conversation and deterministic findings. **Review with LLM
   judge** still invokes CAE's existing ASSERT judge-only integration over the
   saved evidence. Generation does not start a judge or apply a verdict.

## Native artifacts and adapter boundaries

Each invocation retains `request.json` (taxonomy, behavior-ID map and generation
configuration), `taxonomy.json`, per-behavior taxonomy/test-set files, aggregated
`test_set.jsonl`, and a summary under `artifacts/assert-generation/<invocation>/`.
Generated case provenance stores engine, ASSERT/adapter versions, provider/model,
input/output hashes, artifact directory, upstream case/behavior IDs and the
expected-outcome source. It survives review/edit/save/publication and becomes part
of the scenario contract retained for a run. The artifact files are deployment
local; copy/archive them alongside exported designs if moving deployments.

This adapter imports native **`type=prompt`** cases: `seed.description` is the
standalone caller opening request. Expected outcomes are derived from the
reviewed CAE rule and complete policy, not attributed to an upstream field that
ASSERT does not emit. A generated `seed.system_prompt` is retained in the raw
artifact but **never applied to the live voice target**.

Native **`type=scenario`** roleplay descriptions require a separate adapter: they
are character instructions, not directly speakable openings. This integration
does not import them as utterances. It also does not run `systematize` over already
approved behaviors, avoiding taxonomy regeneration and policy drift. A future
requirements-to-ASSERT-taxonomy authoring mode can be added as a separate reviewed
draft step.

Malformed native envelopes, extra seed fields, wrong types, excess/missing batch
counts, unknown behavior IDs, duplicate case IDs, incomplete coverage and partial
ASSERT stage failures reject the whole import. No incomplete case set becomes a
reviewable or publishable draft.

## Bounds and validation

- Existing request limits: 1–5 cases per selected behavior, at most 20 cases.
- Stage model concurrency is one. Request slots default to one per API process,
  configurable with `ASSERT_GENERATION_MAX_CONCURRENT` (capped at two).
- `ASSERT_GENERATION_TIMEOUT_SECONDS` defaults to 300 and is capped at 600; the
  outer subprocess is killed on timeout. Provider failures do not silently fall
  back to another engine/model. Provider/model selections are checked before and
  after each call.
- A transport call limit is twice the requested case count. Generation reserves
  10 estimated credits per case against the existing shared
  `LLM_JUDGE_DAILY_CREDIT_LIMIT` ledger before starting the child. Reservations are
  retained after a started/failed invocation because a timeout does not prove
  zero provider usage. Credits are admission estimates, not provider dollars.
- The existing CAE draft transport does **not** enforce an output-token cap. These
  controls bound cases, calls and duration, not an exact monetary charge. The
  credit ledger and slot scope follow the current single API-process deployment;
  a shared distributed admission ledger is needed before multiple API workers.

Tests exercise the actual pinned ASSERT generation stage and strict artifact
adapter using fake model responses, including 1–5 samples, equal/slug-colliding
behavior labels, partial output and provider changes. No paid live generation or
voice calls are made by those tests. A small manually reviewed live case set is
needed to calibrate prompt quality with each deployment's chosen model.

## Bounded live validation

The local validation generated three caller cases through ASSERT's native
`test_set` stage using `gpt-6-luna`, reviewed and edited them, approved and saved
version 1, then published that version to Scenarios. CAE's built-in reference
voice target completed all three cases with real STT, LLM and TTS, with two
caller/target exchanges per case and 15 valid WAV recordings across the runs.
Failed attempts remain preserved alongside the completed runs.

One caller turn illustrates an evidence limit: its saved pre-TTS source text
contains `482`, while the ASR receipt contains `40082`. Neither observation alone
verifies what the caller audio actually said. Semantic judge proposals remain
unapplied; completed voice execution is not a claim that every judge passed or
every forbidden rule was covered. This check does not qualify Agentic Contact
Center integration, authoritative business-backend actions, browser/WebRTC or SIP
transport, interruptions, or real-time latency.

## Local voice testing

The Pipecat service can tune its rtc-asr Local STT v1 start message using deployment
environment settings. Both conversation participants use these settings. Defaults
preserve the existing streaming behavior; malformed or out-of-range values fail
when the ASR processor is constructed.

| Setting | Default | Accepted values |
| --- | --- | --- |
| `RTC_ASR_INTERIM_RESULTS` | `true` | `true` or `false` |
| `RTC_ASR_PARTIAL_INTERVAL_MS` | `100` | Integer, 100–5000 milliseconds |
| `RTC_ASR_PARTIAL_WINDOW_SECONDS` | `2.0` | Number, 0.5–20 seconds |
| `RTC_ASR_FINAL_TIMEOUT_SECONDS` | `20` | Number, 5–120 seconds |

For a local CPU ASR run focused on semantic evaluation, set
`RTC_ASR_INTERIM_RESULTS=false`, `RTC_ASR_PARTIAL_INTERVAL_MS=1000` and
`RTC_ASR_PARTIAL_WINDOW_SECONDS=5`. The conversation agent reacts to final
transcription frames, so interim text can be disabled without replacing the
streamed PCM, VAD boundaries or final transcript receipts. When interim results
are enabled, a slower cadence reduces repeated partial decoding; the longer
window provides more partial context and can increase each decode's work.

The final-transcript deadline remains 20 seconds by default. A local CPU backend
that needs more decoding time can set `RTC_ASR_FINAL_TIMEOUT_SECONDS=60` after
confirming decoder latency is the cause. This extends only the bounded wait for a
final result; the audio buffer stays 20 seconds and the overall voice session
remains bounded to at most 300 seconds. Such a run can validate conversation semantics, but
should not be presented as a real-time latency benchmark. Tune and validate the
rtc-asr backend separately; a partial cadence setting does not guarantee CPU
decoding meets the final deadline.
