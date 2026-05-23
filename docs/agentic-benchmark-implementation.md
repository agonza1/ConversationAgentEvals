# Agentic Benchmark MVP Implementation

## Product Frame

Build a benchmark runner that tests whether AI agents can actually complete real-world tasks across text, voice, and tools.

The first product should not feel like a generic eval dashboard. It should feel like a regression test suite for agentic behavior:

> Test whether your AI agent can actually do the job.

## Core Flow

1. Choose a benchmark suite.
2. Select a scenario with a user persona, goal, constraints, required actions, forbidden actions, and expected final state.
3. Run the scenario against a text/mock agent endpoint first.
4. Capture transcript, action/tool trace, and final observed state.
5. Score task completion, action correctness, policy compliance, and final-state correctness.
6. Produce a benchmark report with evidence, failure categories, suggested fixes, and rerun/regression state.
7. Later, run the same scenarios through voice/WebRTC/phone integrations.

## First Benchmark Families

### WebRTC.ventures Call Center Voice AI Benchmark

Start here because it maps best to the current voice AI work and WebRTC.ventures positioning.

Scenarios:

- appointment booking
- cancellation or reschedule
- lead qualification
- refund request
- transfer to human
- angry caller escalation
- multilingual caller
- interruption and correction handling

### Telehealth Agent Benchmark

Scenarios:

- patient intake
- symptom collection
- appointment routing
- medication refill boundary
- emergency escalation
- privacy-safe handling

### Online Teaching Agent Benchmark

Scenarios:

- adaptive tutoring
- quiz generation
- learner confusion
- grading boundary
- unsafe advice avoidance
- progression to next topic

### Fintech Support Benchmark

Scenarios:

- identity verification
- transaction dispute
- card freeze
- fraud escalation
- account-info boundary
- compliance refusal

## Benchmark Scenario Shape

Each scenario should include:

- `id`
- `suite_id`
- `title`
- `domain`
- `user_persona`
- `user_goal`
- `constraints`
- `required_actions`
- `forbidden_actions`
- `expected_final_state`
- `rubric`
- `sample_transcript`
- `sample_action_trace`
- `sample_final_state`

## Report Shape

Each benchmark run should return:

- overall pass/fail
- score
- task completion score
- required action score
- forbidden action score
- final state score
- evidence spans
- missing actions
- forbidden actions observed
- failure categories
- suggested fixes
- transcript
- action trace
- final state
- future voice/vCon artifact when applicable

## Differentiation

Competitors to study hardest:

- Coval: developer-native simulation/evals for chat and voice agents.
- Hamming: strongest voice-specific QA and monitoring competitor.
- Cekura: close to the manual-testing automation pain.
- Roark: voice-specific testing, synthetic callers, production replay.
- LangWatch: open-source chat-agent scenario testing.

Our wedge should be:

- agentic task completion, not just conversation quality
- domain-specific branded benchmarks
- text-first runner that can graduate to voice/WebRTC
- action/tool trace and final-state evaluation
- WebRTC.ventures credibility for voice benchmarks
- vCon-compatible artifacts for call records when voice is involved

## First Paid Offer

Give us your agent and 10 must-pass domain scenarios. We will run the benchmark, identify recurring task failures, and turn those scenarios into a reusable regression suite.

## Implementation Priority

1. Add benchmark suite/scenario API.
2. Seed the Call Center Voice AI Benchmark.
3. Add deterministic text/action/final-state evaluator.
4. Build a UI that runs one scenario and shows a report.
5. Add persistence and rerun history.
6. Add real LLM synthetic user simulation.
7. Add voice/WebRTC/phone connectors.
