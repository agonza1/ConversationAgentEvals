# Agentic AI Benchmark MVP

## Mission

Make every AI agent behavior measurable, trustworthy, and improvable.

## Vision

Every important AI agent should be testable against the real tasks it is expected to complete.

## Beachhead

Domain-specific agent benchmarks for builders and agencies who need to prove that AI agents can complete real workflows across text, voice, and tools.

The first buyer is an AI agent builder, voice AI team, or agency that currently tests by hand: they pretend to be different users, ask for real tasks, check whether the agent took the right action, take notes, change something, and do it again.

Voice remains a beachhead because WebRTC.ventures can credibly own it, but the larger product is not limited to conversations. It evaluates whether the agent performed the requested action and reached the right final state.

vCon compatibility remains useful for voice/call artifacts, but it is not the first message. The first message is eliminating the annoying manual agent-testing loop.

## Differentiated Wedge

Do not compete as a generic voice AI QA dashboard or generic LLM eval dashboard.

The wedge is domain-specific agentic benchmarks plus layered failure diagnosis:

> Test whether your AI agent can actually do the job.

Classify failures by:

- task completion
- required action execution
- final state correctness
- scenario completion
- user behavior
- tool selection and tool arguments
- workflow ordering
- memory/context handling
- LLM instruction following
- policy/compliance boundaries
- judge/rubric uncertainty
- STT / transcription
- TTS timing
- barge-in / interruption handling
- latency

## Benchmark Families

Create WebRTC.ventures-branded benchmark suites that evaluate complete agentic workflows:

- Call Center Voice AI Benchmark: appointment booking, cancellation, transfer, refund, lead qualification, escalation, interruptions, multilingual callers.
- Telehealth Agent Benchmark: intake, symptom collection, appointment routing, medication refill boundaries, emergency escalation, privacy-safe handling.
- Online Teaching Agent Benchmark: tutoring, adaptive explanation, quiz generation, learner confusion, grading boundaries, unsafe advice avoidance.
- Fintech Support Benchmark: identity verification, transaction dispute, card freeze, fraud escalation, account boundaries, compliance refusal.

Each benchmark should include:

- user persona
- user goal
- agent-under-test interface
- allowed and forbidden actions
- required tool calls or state changes
- success criteria
- failure taxonomy
- evidence artifacts: transcript, tool trace, final state, call/vCon artifact when voice is involved

## MVP Slice

The current first slice proves the eval/report loop:

1. Paste a transcript, call JSON, or vCon-like JSON.
2. Enter plain-English eval criteria.
3. Run an evaluation.
4. Return a score, pass/fail result, evidence, risks, and suggested fixes.
5. Export the result as a vCon-compatible `analysis` object.

The next build slice should move from pasted evals to domain benchmark scenarios:

1. Define reusable benchmark scenarios with user goal, required actions, forbidden actions, and final-state expectations.
2. Run the scenario against a target agent endpoint or, initially, a text/mock conversation harness.
3. Capture the transcript, tool/action trace, and final observed state.
4. Evaluate task completion, action correctness, policy compliance, and user experience.
5. Save the run so users can rerun the same benchmark suite after prompt/model/tool changes.

Production-call monitoring comes later, after users prove they want repeatable benchmark suites and ask to run the same rubrics automatically on live calls.

## Runtime

Use Docker Compose as the default way to run the MVP and any supporting services.

```bash
npm run docker:up
```

Default local endpoints:

- Web: `http://localhost:3012`
- API: `http://localhost:8025`
- Pipecat: `http://localhost:8110`

If a port is already occupied during local iteration, override only the host port:

```bash
PORT=3013 API_PORT=8026 PIPECAT_PORT=8111 docker compose up --build -d
```

## What We Are Not Building Yet

- Full dashboards.
- Billing.
- Large team workflows.
- Production monitoring as the primary product.
- Deep telephony/contact-center integrations.

Live WebRTC or phone simulation is allowed only when it helps evaluate agentic behavior end-to-end. Deep voice platform integrations come after a lightweight text/tool benchmark runner proves useful.

## Learning Goals

- Do users see manual agent testing as painful enough to pay for automation?
- Which domain benchmark scenarios do they rerun after every change?
- Do users trust the eval output enough to decide whether a change improved or regressed task completion?
- Which eval criteria repeat across customers?
- Do users ask for synthetic voice calls, text simulation, batch runs, or production integrations first?
- Are they willing to pay for saved regression suites before production monitoring?

## First Paid Offer

Give us your AI agent and 10 must-pass domain scenarios. We will run the benchmark, produce QA reports, identify recurring task failures, and turn the scenarios into a reusable regression suite.

The SaaS product should grow from that service loop, not ahead of it.
