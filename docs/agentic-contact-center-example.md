# Optional Agentic Contact Center Target Example

ConversationAgentEvals is a standalone product. This example demonstrates how one optional external target—[Agentic Contact Center](https://github.com/agonza1/agentic-contact-center)—can supply evidence to the native ConversationAgentEvals benchmark and ASSERT surfaces.

Nothing in the normal benchmark runner, API tests, saved-run workflows, reports, exports, or ASSERT boundary requires ACC.

Related files:

- Native scenario contract: [examples/agentic-contact-center-cancellation-rescue.json](examples/agentic-contact-center-cancellation-rescue.json)
- Checked-in offline target response: [examples/agentic-contact-center-run-fixture.json](examples/agentic-contact-center-run-fixture.json)
- Phase 2C audio plan: [examples/agentic-contact-center-audio-plan.json](examples/agentic-contact-center-audio-plan.json)
- Tracking issue: [#98](https://github.com/agonza1/ConversationAgentEvals/issues/98)

## Ownership

ConversationAgentEvals owns the reusable testing product:

- benchmark and scenario definitions;
- tester/caller policy;
- target-adapter lifecycle;
- audio fixture selection and scenario-step scheduling;
- pacing mode, seed, and provenance;
- evidence normalization;
- benchmark and ASSERT request construction;
- run persistence, reports, comparison, and regression analysis.

An external target owns its system-under-test runtime:

- persistent realtime voice sessions;
- browser, fixture, tester-agent, and SIP media adapters;
- audio decoding and injection into its production media path;
- VAD, interruption, and barge-in;
- streaming STT and TTS;
- agent/model/tool/policy behavior;
- operator controls, fallback, and native call proof.

The generic end state is:

```text
ConversationAgentEvals scenario/tester
  -> selected caller source
  -> optional target adapter
  -> target persistent realtime media session
  -> target agent runtime
  -> target proof bundle
  -> ConversationAgentEvals normalization
  -> ASSERT/benchmark run
  -> baseline/candidate comparison
```

ACC is one implementation of the optional target boundary, not a dependency of ConversationAgentEvals.

# Standalone use without ACC

## Normal product demo

```bash
npm run setup
cp .env.example .env
npm run dev
```

Open the printed web URL and visit `/benchmarks`. The built-in benchmark suites and reports work independently.

## Offline cancellation-rescue artifact generation

The optional target example can also run with no ACC service and no ConversationAgentEvals API process:

```bash
npm run example:acc:fixture
```

This reads the checked-in response fixture and writes:

```text
artifacts/agentic-contact-center-example/acc-example-<timestamp>/
  acc-raw-response.json
  normalized-evidence.json
  benchmark-run-request.json
  assert-run-request.json
  summary.json
```

The command is primarily an adapter and artifact-generation test. It proves that the optional target format can be normalized without cloning or starting ACC.

## Evaluate the offline fixture through the native benchmark API

Start ConversationAgentEvals only:

```bash
npm run dev
```

Then run:

```bash
apps/api/.venv/bin/python scripts/agentic_contact_center_example.py \
  --input docs/examples/agentic-contact-center-run-fixture.json
```

The script sends the generated request to:

```text
POST /api/benchmarks/run
```

and writes:

```text
benchmark-evaluation-response.json
```

The cancellation-rescue scenario is registered natively under:

```text
call-center-voice-ai/cancellation-rescue
```

# Optional run against ACC

Start ACC separately:

```bash
cd ../agentic-contact-center
npm install
npm run build
PORT=8026 npm start
```

Start ConversationAgentEvals:

```bash
cd ../ConversationAgentEvals
npm run setup
cp .env.example .env
npm run dev
```

Run the external-target example:

```bash
npm run example:acc
```

Equivalent explicit command:

```bash
ACC_BASE_URL=http://127.0.0.1:8026 \
CONVERSATION_AGENT_EVALS_BASE_URL=http://127.0.0.1:8025 \
apps/api/.venv/bin/python scripts/agentic_contact_center_example.py
```

Collect and normalize target evidence without submitting it:

```bash
npm run example:acc -- --skip-submit
```

Submit both the native benchmark request and the lower-level ASSERT wrapper request:

```bash
npm run example:acc -- --also-submit-assert-wrapper
```

Full output shape:

```text
artifacts/agentic-contact-center-example/acc-example-<timestamp>/
  acc-raw-response.json
  normalized-evidence.json
  benchmark-run-request.json
  assert-run-request.json
  benchmark-evaluation-response.json  # normal submitted path
  assert-ingestion-response.json      # only with --also-submit-assert-wrapper
  summary.json
```

# Phase 2A — native cancellation-rescue benchmark

The scenario is now part of the `call-center-voice-ai` catalog.

## Required-action vocabulary

1. Detect cancellation intent.
2. Capture the renewal-increase reason.
3. Enter policy hold before a retention action.
4. Record operator approval, escalation, or handoff.
5. Record a final disposition.

## Forbidden behavior

- Make an unapproved retention offer.
- Ignore the cancellation request.
- Continue after a tool timeout without a human handoff.

## Final-state expectations

The run must be complete and end in one of the allowed outcomes:

```text
scripted_wrap_complete
approved_retention_follow_up
cancellation_completed
fail_closed_handoff
human_handoff
```

## Deterministic checks

The evaluator adds exact event and state checks on top of the normal transcript/rubric scoring:

- `cancellation_intent_detected` is present;
- `renewal_increase_reason_captured` is present;
- `policy_hold_entered` or `operator_steer_requested` is present;
- the policy boundary occurs before approval, handoff, or wrap;
- approval, escalation, transfer, or handoff is recorded;
- a terminal event is recorded;
- `final_state.complete == true`;
- the outcome is in the allowed set;
- a tool/runtime failure is followed by handoff or transfer;
- forbidden event types are absent.

These checks use the action/event trace and final state rather than trusting transcript wording alone.

## Evidence requirements

Required:

```text
transcript
action_trace
final_state
```

Recommended for voice runs:

```text
latency_evidence
call_media
runtime_provenance
```

The native scenario contract is available through the existing catalog APIs, including:

```text
GET /api/benchmarks/suites/call-center-voice-ai
GET /api/benchmarks/suites/call-center-voice-ai/scenarios/cancellation-rescue/contract
```

# Evidence mapping

The optional ACC adapter maps target output into the reusable benchmark request:

| Benchmark/ASSERT field | Optional ACC source |
| --- | --- |
| `transcript` | Call transcript rendered as speaker-prefixed text |
| `conversation` | Structured speaker/text/timestamp turns |
| `action_trace` | Ordered target event trail |
| `final_state` | Flow state, outcome, operator state, fallback state, runtime labels |
| `assert_bundle` | Complete normalized target evidence |
| metadata | Latency marks, caveats, execution mode, and provenance |

The same benchmark can receive equivalent evidence from another target adapter or a manually assembled request.

# Phase 2B — target-session adapter contract

The checked-in `AccRealtimeTargetAdapter` defines the optional target boundary:

```python
class AccRealtimeTargetAdapter:
    async def create_session(...)
    async def inject_audio(...)
    async def stream_audio(...)
    async def observe_events(...)
    async def interrupt(...)
    async def close_session(...)
    async def collect_proof(...)
```

The adapter defaults to the proposed ACC endpoints:

```text
POST /api/voice/sessions
POST /api/voice/sessions/:id/play
WS   /api/voice/sessions/:id/media/input
GET  /api/voice/sessions/:id/events
POST /api/voice/sessions/:id/control
POST /api/voice/sessions/:id/close
GET  /api/voice/sessions/:id/proof
```

The class is independently testable with an injected fake HTTP/media transport. Importing or testing it does not contact ACC.

The current ACC implementation does not yet guarantee all of these endpoints. Until that contract exists, this is an executable adapter boundary and test scaffold rather than a claim of completed live integration.

# Phase 2C — prerecorded audio orchestration

ConversationAgentEvals owns:

- which fixture represents each caller act;
- step order;
- event gates;
- realtime or accelerated pacing selection;
- barge-in intent;
- seed;
- target/run provenance;
- expected caller act.

The target owns:

- retrieving or accepting the referenced audio;
- decoding it;
- resampling it;
- pacing/injecting media into the same realtime session used by microphone or SIP callers;
- recording actual media and timing evidence.

The checked-in audio plan demonstrates the contract:

```text
docs/examples/agentic-contact-center-audio-plan.json
```

`AccAudioFixtureScheduler` resolves the selected fixtures, waits for optional target events, and calls the target adapter with the selected pacing and provenance. It never posts the expected transcript as a shortcut.

# Phase 3 — Pipecat tester-agent contract

The tester architecture remains three separate actors:

```text
Tester agent: chooses and verbalizes caller behavior.
Target agent: system under test.
ASSERT: evaluates the resulting evidence.
```

The checked-in `DeterministicTesterController` owns the bounded caller plan:

```text
deterministic scenario controller
  -> optional LLM wording renderer
  -> caller TTS renderer
  -> target realtime media session
  -> semantic or acoustic observation
  -> next caller act
```

Required configuration is explicit:

- scenario goal;
- allowed caller acts;
- ordered default acts;
- maximum turns;
- total timeout;
- terminal event types and final states;
- seed;
- observation mode;
- model version;
- prompt version.

The optional LLM may word a selected caller act, but it may not change the scenario objective, invent an unapproved act, mutate target state, or serve as the evaluator.

# Honesty and current limitations

The checked-in offline and HTTP examples are labeled:

```text
acc_http_scripted_fixture
```

They do not prove:

- live ASR;
- live TTS;
- full-duplex media;
- barge-in;
- WebRTC media;
- SIP / FreeSWITCH media.

The native benchmark run does execute the registered cancellation-rescue checks. The optional `/api/assert/runs` local sidecar remains evidence-ingestion validation and must not be represented as a complete semantic ASSERT judge result.
