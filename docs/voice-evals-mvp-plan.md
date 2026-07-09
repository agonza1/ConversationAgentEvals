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

ASSERT changes the framing of this plan: do not continue treating the generic eval harness layer as the product moat. ASSERT already covers requirement-driven eval generation, trace-aware judging, and portable local artifacts well enough that the smarter move is to integrate or stay compatible.

## Differentiated Wedge

Do not compete as a generic voice AI QA dashboard or generic LLM eval dashboard.

Do not compete as a generic plain-language requirements to evals product either.

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

ASSERT can help with the generic parts of the stack:

- spec-to-eval generation from natural-language requirements
- synthetic scenario generation and scoring rubrics
- multi-turn eval orchestration
- trace-aware judging over tool calls and intermediate actions
- local and CI artifact handling

ConversationAgentEvals should differentiate on the parts ASSERT does not make specific enough for voice and conversation deployments:

- voice-native evidence: audio, vCon, WebRTC/phone metadata, STT errors, silence gaps, latency, TTS failures, DTMF, transfers
- task completion proof tied to external system state
- production failure to saved regression flow
- operator- and client-ready QA reporting
- benchmark packs for real deployed workflows

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

The next build slice after that should move from generic scenarios to managed QA operations:

1. Import or export ASSERT-compatible artifacts where useful instead of rebuilding ASSERT taxonomy.
2. Ingest one real artifact type deeply: vCon or transcript plus metadata plus tool/action log.
3. Define 10-20 hard task-completion checks for one vertical.
4. Produce pass/fail QA reports citing transcript spans, tool calls, timestamps, and final state.
5. Convert production failures into rerunnable saved regression scenarios.

Production-call monitoring comes later, after users prove they want repeatable benchmark suites and ask to run the same rubrics automatically on live calls.

## Runtime

Use Docker Compose as the default way to run the minimal benchmark demo. The default profile starts only the web and API containers.

```bash
npm run docker:up
```

Default local endpoints:

- Web: `http://localhost:3012`
- API: `http://localhost:8025`

Optional supporting services stay behind named profiles:

- Pipecat voice/WebRTC path: `docker compose --profile voice up --build -d`
- Postgres, seed, and worker: `COMPOSE_DATABASE_URL=postgresql://cae:cae_local_password@db:5432/conversation_agent_evals docker compose --profile persistence up --build -d`

If a port is already occupied during local iteration, override only the host port:

```bash
PORT=3013 API_PORT=8026 docker compose up --build -d
PORT=3013 API_PORT=8026 PIPECAT_PORT=8111 docker compose --profile voice up --build -d
```

## What We Are Not Building Yet

- Full dashboards.
- Commercial packaging.
- Large team workflows.
- Production monitoring as the primary product.
- Deep telephony/contact-center integrations.
- A generic eval generation platform that competes head-on with ASSERT.

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

## Kill Criteria

- If the product still looks like a generic eval runner, stop and tighten the wedge.
- If synthetic transcripts are the only evidence, differentiation is weak.
- If ASSERT plus a thin adapter can reproduce most of the value, the product is not differentiated enough.
- If the reports do not change release or client-acceptance decisions, the value is not real enough yet.
