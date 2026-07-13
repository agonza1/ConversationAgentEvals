# Agentic Contact Center External-Target Example

This example shows the ownership boundary between ConversationAgentEvals and [Agentic Contact Center](https://github.com/agonza1/agentic-contact-center) without claiming that the current example is a live full-duplex voice test.

Machine-readable scenario: [examples/agentic-contact-center-cancellation-rescue.json](examples/agentic-contact-center-cancellation-rescue.json)

Tracking issue: [#98](https://github.com/agonza1/ConversationAgentEvals/issues/98)

## Ownership

ConversationAgentEvals owns the testing layer:

- scenario and test-set definition;
- tester/caller orchestration;
- target lifecycle;
- evidence normalization;
- ASSERT request creation and result ingestion;
- saved-run comparison and regression reporting.

ACC remains the system under test and owns:

- persistent realtime voice sessions;
- browser, fixture, tester-agent, and SIP media adapters;
- VAD, interruption, and barge-in behavior;
- `rtc-asr` streaming STT;
- agent/model/tool/policy behavior;
- streaming TTS;
- operator controls, fallback, and native call proof.

The intended end state is:

```text
ConversationAgentEvals scenario/tester
  -> selected caller source
  -> ACC persistent realtime media session
  -> ACC agent runtime
  -> ACC proof bundle
  -> ConversationAgentEvals normalization
  -> ASSERT run
  -> baseline/candidate comparison
```

## What the Phase 1 example does

The checked-in script calls the running ACC control-plane route:

```text
POST /api/demo/run-end-to-end
```

It then writes:

- the raw ACC response;
- normalized transcript, conversation, action trace, final state, latency, and provenance;
- a validated `AssertRunCreateRequest`;
- the optional response from `POST /api/assert/runs`;
- a summary with explicit limitations.

The example is labeled:

```text
acc_http_scripted_fixture
```

It proves target connectivity and evidence handoff. It does **not** prove:

- live ASR;
- live TTS;
- full-duplex media;
- barge-in;
- WebRTC media;
- SIP / FreeSWITCH media;
- a complete semantic ASSERT judge run.

The current local ASSERT sidecar response is evidence-ingestion validation. Its green result must not be presented as a semantic task-completion verdict.

## Run it

Start ACC in one terminal:

```bash
cd ../agentic-contact-center
npm install
npm run build
PORT=8026 npm start
```

Start ConversationAgentEvals in another terminal:

```bash
cd ../ConversationAgentEvals
npm run setup
cp .env.example .env
npm run dev
```

Run the example:

```bash
npm run example:acc
```

Equivalent explicit command:

```bash
ACC_BASE_URL=http://127.0.0.1:8026 \
CONVERSATION_AGENT_EVALS_BASE_URL=http://127.0.0.1:8025 \
apps/api/.venv/bin/python scripts/agentic_contact_center_example.py
```

Collect target evidence without submitting it:

```bash
npm run example:acc -- --skip-submit
```

Artifacts are written under:

```text
artifacts/agentic-contact-center-example/acc-example-<timestamp>/
  acc-raw-response.json
  normalized-evidence.json
  assert-run-request.json
  assert-ingestion-response.json   # when submitted
  summary.json
```

## How the evidence maps to ASSERT

The example constructs the canonical request with:

| ASSERT evidence field | ACC source |
| --- | --- |
| `transcript` | ACC call transcript rendered as speaker-prefixed text |
| `conversation` | ACC transcript turns with speaker, text, and timestamp |
| `action_trace` | ACC event trail normalized to ordered observed actions |
| `final_state` | flow state, outcome, operator state, fallback state, and runtime labels |
| `assert_bundle` | complete normalized ACC evidence object |
| `additional_artifacts` | latency marks and runtime caveats |
| `provenance` | target repo, endpoint, call ID, and execution mode |

The spec reference is:

```text
agentic-contact-center/cancellation-rescue
```

The scenario contract requires cancellation intent, the renewal-increase reason, an operator/policy boundary before risky action, an approval/escalation/handoff record, and an explicit final disposition.

## Why this belongs in ConversationAgentEvals

ACC should not contain the tester's scenario policy or the evaluation runner. Conversely, ConversationAgentEvals should not reimplement ACC's voice session or agent runtime.

The existing `AgenticContactCenterAdapter` in `apps/api/app/services/voice_lab.py` currently shells into a sibling ACC checkout and consumes `npm run proof`. That remains useful as a deterministic baseline. This example adds the missing external-target handoff to a running ACC service and makes the evidence contract visible.

## Next realtime-audio phase

Once ACC exposes a stable persistent media session and audio-injection API, the same scenario should support:

```text
manual microphone
prerecorded audio
Pipecat tester agent
SIP / FreeSWITCH
```

All four sources must use the same ACC media pipeline. A realtime-audio test must not bypass ASR by posting the expected transcript.

Conceptual target contract:

```text
POST /api/voice/sessions
POST /api/voice/assets
POST /api/voice/sessions/:id/play
WS   /api/voice/sessions/:id/media/input
GET  /api/voice/sessions/:id/events
GET  /api/voice/sessions/:id/proof
```

ConversationAgentEvals will then:

1. create the scenario run;
2. choose the source and seed;
3. start the ACC target session;
4. schedule caller audio or tester acts;
5. collect transcripts, media pointers, events, tools, state, and latency;
6. submit the evidence to ASSERT;
7. compare baseline and candidate runs.

## Tester-agent rule

The tester agent, ACC agent, and ASSERT judge are separate actors:

```text
Tester agent: drives the caller scenario.
ACC agent: system under test.
ASSERT: evaluates the completed evidence.
```

The future tester must have fixed objectives, allowed caller acts, max turns, timeouts, terminal conditions, seed/model provenance, and no access to ACC tools or final-state mutation.
