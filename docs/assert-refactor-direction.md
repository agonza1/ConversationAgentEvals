# ASSERT Refactor Direction

## Strategic decision

ConversationAgentEvals should not keep moving toward a generic eval harness or a generic natural-language requirements-to-evals product. ASSERT already occupies that lane with requirement-driven eval generation, multi-turn execution, trace-aware judging, and portable local artifacts.

The right move is to treat ASSERT as optional infrastructure and compatibility, while pushing this repo toward managed QA and regression testing for production voice and conversation agents.

## What ASSERT likely commoditizes

- Spec-to-eval generation from plain-language requirements
- Synthetic scenario generation and scoring rubrics
- Multi-turn agent eval orchestration
- Trace-aware judging over tool calls and intermediate actions
- CI and local artifact output
- Generic did-the-agent-follow-the-requirement LLM judging

## Where this repo should differentiate

- Voice-native evidence: audio, vCon, WebRTC or phone metadata, STT errors, silence gaps, latency, TTS failures, DTMF, transfers
- Task completion proof tied to external state such as appointments booked, tickets escalated, CRM changes, consent captured
- Production failure to saved regression scenario flow
- Layered failure diagnosis across STT, LLM, tool/API, orchestration, TTS, and latency
- Client-ready QA reports for agencies, SIs, and implementation leads
- Vertical benchmark packs for appointment scheduling, contact-center escalation, healthcare intake, and sales qualification

## How the current codebase maps to this refactor

The repo already has useful starting seams:

- `apps/api/app/services/benchmark_service.py` contains benchmark suites, simulation flows, and report assembly.
- `apps/api/app/services/assert_trace.py` preserves only ASSERT v2 action-trace normalization needed for evidence citations and platform report assembly.
- `apps/api/app/schemas/benchmarks.py` already accepts transcript, vCon, action trace, and final-state evidence.
- `apps/web/components/BenchmarkRunner.tsx` already exposes audit artifacts, evidence panels, and suite-run flows.

That means the product shift does not require a restart. It needs a sharper evidence model, adapter layer, and report story.

## Near-term implementation plan

1. Add an ASSERT adapter boundary
- Create an adapter service that can import ASSERT-style artifacts and export compatible run bundles.
- Keep it optional so direct transcript, vCon, and tool-log ingestion remain first-class.

2. Deepen one artifact path
- Pick one real evidence bundle first: vCon or transcript plus metadata plus tool/action log.
- Normalize timestamps, participant roles, tool calls, final state, and export readiness.

3. Pick one vertical and write hard checks
- Prefer appointment scheduling or contact-center escalation.
- Define 10-20 hard pass/fail checks tied to outcomes, not just transcript quality.

4. Upgrade reports into QA evidence
- Cite transcript spans, tool calls, timestamps, and final-state assertions.
- Attribute failures to the right layer when the evidence supports it.

5. Add production failure to regression
- Let operators capture a failed production artifact and save it as a rerunnable benchmark scenario.
- Preserve linkage between the source failure and later reruns.

## Kill criteria

- If the product still looks like a generic eval runner, stop and tighten the wedge.
- If synthetic transcripts are the only evidence, differentiation is weak.
- If ASSERT plus a thin adapter can reproduce most of the value, the product is not differentiated enough.
- If the reports do not change go or no-go release decisions, the product value is not real enough yet.
