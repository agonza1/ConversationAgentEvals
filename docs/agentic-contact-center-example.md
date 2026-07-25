# Optional Agentic Contact Center Target

[Agentic Contact Center](https://github.com/agonza1/agentic-contact-center) is an optional
external target example. ConversationAgentEvals remains independently installable: its
benchmarks, saved runs, reports, exports, and ASSERT boundary do not require ACC.

## Ownership boundary

ConversationAgentEvals owns scenarios, tester policy, execution bounds, evidence normalization,
evaluation, persistence, and reporting. An external target owns its realtime session, media
adapters, ASR/TTS, model and tool behavior, interruption handling, and native proof.

```text
scenario + tester
  -> optional target adapter
  -> target runtime
  -> target proof bundle
  -> CAE normalization and evaluation
  -> report and comparison
```

ACC is one implementation of this target boundary, not a runtime dependency.

## Offline fixture

Generate normalized artifacts without starting ACC or the CAE API:

```bash
npm run setup
npm run example:acc:fixture
```

The command reads `docs/examples/agentic-contact-center-run-fixture.json` and writes raw,
normalized, benchmark, ASSERT, and summary JSON under
`artifacts/agentic-contact-center-example/`.

To evaluate the fixture through a running CAE API:

```bash
npm run dev
npm run example:acc:fixture:run
```

The registered scenario is:

```text
call-center-voice-ai/cancellation-rescue
```

Its contract requires cancellation intent and reason capture, a policy boundary before a
retention action, approval/escalation/handoff evidence, and a valid terminal state. Exact checks
use `action_trace` and `final_state`; transcript wording alone is insufficient.

## Optional ACC service

Start ACC separately, then run:

```bash
ACC_BASE_URL=http://127.0.0.1:8026 \
CONVERSATION_AGENT_EVALS_BASE_URL=http://127.0.0.1:8025 \
npm run example:acc
```

Useful variants:

```bash
npm run example:acc -- --skip-submit
npm run example:acc -- --also-submit-assert-wrapper
```

The adapter maps external target output to the reusable evidence model:

| CAE field | Target source |
| --- | --- |
| `transcript` | Speaker-prefixed call transcript |
| `conversation` | Structured turns and timestamps |
| `action_trace` | Ordered target events |
| `final_state` | Completion, outcome, operator, and fallback state |
| `assert_bundle` | Complete normalized target evidence |
| metadata | Latency, caveats, execution mode, and provenance |

The same fields can come from another adapter or a manually assembled request.

## Checked-in fixtures

- [Scenario contract](examples/agentic-contact-center-cancellation-rescue.json)
- [Successful response](examples/agentic-contact-center-run-fixture.json)
- [Fail-closed response](examples/agentic-contact-center-fail-closed-run-fixture.json)
- [Legacy prerecorded-audio plan](examples/agentic-contact-center-audio-plan.json)

The audio plan and `AccAudioFixtureScheduler` remain a replay path. The primary built-in voice
path instead runs separate Pipecat tester and target graphs; see
[execution-audio-webrtc.md](execution-audio-webrtc.md).

## Honesty boundary

The offline and HTTP examples prove adapter behavior, evidence normalization, and the registered
cancellation-rescue checks. They do not prove live ASR/TTS, full-duplex production media,
barge-in, browser WebRTC, SIP, or FreeSWITCH Verto.

The optional `/api/assert/runs` sidecar validates evidence ingestion through the local
ASSERT-compatible contract. It must not be presented as an external semantic ASSERT service.
