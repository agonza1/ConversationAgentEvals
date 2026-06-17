# Product Strategy And Slide Outline

## Core Positioning

ConversationAgentEvals tests whether an AI agent actually did the job, not whether a transcript sounded good.

The painful customer problem: teams ship voice and conversation agents, then manually call them after every prompt or workflow change to see if they still book, cancel, escalate, verify identity, follow policy, and update systems correctly. Existing evals are often too generic, too transcript-only, or too expensive before the team knows what to test.

The product should start simple:

- Run scenario tests in the browser.
- Score real conversation evidence.
- Show exactly what passed, failed, and why.
- Keep commercial packaging separate from the core eval/evidence runner until the validated service loop proves which limits matter.

## Key Problems We Solve

1. Manual QA for AI agents is slow and inconsistent.
2. Transcript quality scores do not prove task completion.
3. Voice agents fail in ways text-only tests miss: interruptions, latency, turn-taking, transfers, noisy input, and caller corrections.
4. Teams need comparable benchmark results across agent versions, providers, prompts, voices, and tool stacks.
5. Regulated or high-stakes workflows need evidence: transcript, tool actions, final state, policy checks, and audit trail.

## Main Features

### Free Browser Eval

- Paste transcript or run a lightweight browser scenario.
- Choose a benchmark family: call center, telehealth, teaching, fintech support, custom.
- Use deterministic checks first: required actions, forbidden actions, final state, compliance constraints.
- Show a pass/fail report with evidence highlights and suggested fixes.

This is the acquisition loop: users should get value before signup.

### Auth And Project Workspace

- Firebase Auth for email, Google sign-in, and magic link.
- Firebase or Google Cloud-backed projects, teams, and run history.
- Saved benchmark suites, scenarios, reports, and comparison over time.

### Real Eval Engine

- No mock scoring for production paths.
- Scenario runner generates or accepts real evidence: transcript, agent messages, user messages, tool calls, final state.
- Deterministic evaluator for objective requirements.
- LLM judge layer for nuanced criteria: helpfulness, policy interpretation, instruction following, handoff quality, coaching feedback.
- Evidence-grounded judge prompts that cite exact conversation spans and action events.

### Voice And Group Call Evaluation

- Browser WebRTC voice test runner.
- Phone/SIP integration later for production call centers.
- Audio-derived metrics: latency, interruptions, silence, talk-over, escalation behavior.
- Group-call evidence model: speaker roles, decisions, commitments, action items, unresolved questions.
- vCon-compatible export/import for voice conversation artifacts.

### Integrations

- Agent endpoints: HTTP webhook, WebSocket/WebRTC, LiveKit/Pipecat, Twilio/SIP later.
- Tool trace ingestion: CRM, calendar, ticketing, EHR-style mock systems, payment/fraud workflows.
- CI/API integration so teams can run eval suites before deploying agent changes.

### Reporting

- Scorecards by scenario, benchmark, version, provider, and date.
- Failure taxonomy: task failure, unsafe action, missing verification, bad handoff, hallucinated tool action, policy violation, poor recovery.
- Exportable PDF/CSV/JSON reports.
- Team dashboards for trend and regression tracking.

## Google Cloud Architecture

- Firebase Auth: signup/login, identity providers, session management.
- Firebase Hosting or Cloud Run for the Next.js frontend.
- Cloud Run for FastAPI eval API and media orchestration services.
- Firestore for users, orgs, projects, scenario definitions, and run metadata.
- Cloud Storage for transcripts, audio, vCon files, and report artifacts.
- Pub/Sub + Cloud Tasks for async eval jobs.
- Vertex AI or provider-routed LLM judges with explicit cost controls.
- Secret Manager for provider API keys.
- Cloud Logging/Monitoring for reliability and cost observability.

## Packaging Direction

Commercial packaging belongs around the eval system, not inside the core architecture. The repo should keep the primitives clean: benchmark suites, evidence ingestion, deterministic checks, judge controls, saved runs, and reports.

The initial commercial motion should be service-led: run customer-specific benchmarks, produce QA reports, and convert successful scenarios into reusable regression suites. Only after that loop is clear should a separate product layer decide packaging and usage limits.

## Suggested Next Slides

1. Title: ConversationAgentEvals
   - "Test whether conversation agents actually complete the job."

2. The Problem
   - Manual agent QA is painful after every prompt, model, tool, or voice change.
   - Transcript ratings miss the failures that matter.

3. Why Now
   - Voice AI and agentic workflows are moving into real business operations.
   - Buyers need proof, not demos.

4. What We Test
   - Task completion.
   - Tool/action correctness.
   - Policy compliance.
   - Final state.
   - Voice and group-call behavior.

5. Product Demo Flow
   - Pick benchmark.
   - Run/paste conversation.
   - See evidence.
   - Get score and failure reasons.
   - Compare against previous versions.

6. Service-To-Product Loop
   - A lightweight browser eval gives immediate value.
   - A pilot produces reusable benchmark suites and QA reports.
   - Repeated pilot needs define future packaging limits.

7. Google Cloud Architecture
   - Firebase Auth, Cloud Run, Firestore, Cloud Storage, Pub/Sub/Tasks, Vertex/provider judges.

8. Wedge Markets
   - Call center voice AI first.
   - Then telehealth, online teaching, fintech support, and group-call workflows.

9. Packaging
   - Keep commercial packaging outside the core runner for now.
   - Let usage, judge cost, saved history, and integration needs emerge from pilots.

10. Roadmap
   - Real browser evals.
   - Firebase auth/projects.
   - Paid LLM judges.
   - Voice/WebRTC runner.
   - CI/API integrations.
   - vCon import/export and group-call reports.

11. E2E Validation
   - Playwright validates the full browser path: eval run, saved project flow, controlled judge/voice actions, and report rendering.

## Today's Build Goal

Ship the first end-to-end product skeleton today, with enough real behavior to prove the direction:

1. Free browser eval that can score pasted transcript evidence without signup.
2. Real deterministic eval path for task completion, required actions, forbidden actions, final state, and policy checks.
3. Call-center benchmark suite with realistic scenarios as the first wedge.
4. Firebase Auth-ready signup/login surface and project model.
5. Saved runs/projects flow behind authentication.
6. Judge and voice-run controls that make expensive paths explicit without packaging plumbing.
7. Usage metadata foundation for LLM judges, voice minutes, and API usage.
8. LLM judge abstraction with evidence citations and cost controls.
9. Voice/WebRTC integration path defined in the app, controlled until fully wired.
10. Google Cloud deployment architecture documented and reflected in env/config naming.
11. Playwright e2e validation covering the core eval-to-report journey.

## Immediate Build Priorities

1. Replace demo/mock paths with a real eval path for pasted transcripts and browser-run text scenarios.
2. Make the boundary between the eval runner and future product packaging explicit in docs and UI copy.
3. Add Firebase Auth scaffolding and project-scoped saved runs.
4. Add an LLM judge abstraction with cost controls and evidence citations.
5. Build one excellent call-center voice AI benchmark suite before expanding too broadly.
6. Add Playwright e2e coverage for the full browser validation path.
7. Keep commercial packaging work out of this repo until a separate layer is intentionally designed.
