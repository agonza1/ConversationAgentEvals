# Product Strategy And Slide Outline

## Core Positioning

ConversationAgentEvals tests whether an AI agent actually did the job, not whether a transcript sounded good.

The painful customer problem: teams ship voice and conversation agents, then manually call them after every prompt or workflow change to see if they still book, cancel, escalate, verify identity, follow policy, and update systems correctly. Existing evals are often too generic, too transcript-only, or too expensive before the team knows what to test.

The product should start simple:

- Run scenario tests in the browser.
- Score real conversation evidence.
- Show exactly what passed, failed, and why.
- Gate signup/payment when the user wants stronger LLM judges, saved runs, integrations, voice calls, or team reporting.

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
- Vertex AI or provider-routed LLM judges for paid tiers.
- Secret Manager for provider API keys.
- Cloud Logging/Monitoring for reliability and cost observability.

## Pricing Direction

### Free

- Browser transcript evals.
- Limited scenario runs per month.
- Deterministic checks.
- Public/sample benchmark suites.
- No saved team history beyond recent local/browser state.

Goal: prove value instantly.

### Starter: $29-$49/month

- Saved projects and run history.
- Custom benchmark suites.
- More runs.
- Basic LLM judge credits.
- Export reports.

Best for solo builders and early voice-agent teams.

### Team: $149-$299/month

- Team seats.
- Higher LLM judge limits.
- CI/API access.
- Version comparisons.
- Shared reports and audit history.
- Basic voice/WebRTC test runs.

Best for agencies and teams shipping agents for customers.

### Business: $799+/month

- High-volume evals.
- Phone/SIP or deeper voice integrations.
- Custom benchmarks.
- Compliance-oriented audit exports.
- Dedicated judge/model routing.
- Priority support.

Best for call centers, healthcare, fintech, education, and regulated workflows.

Pricing metric should be a blend of seats, saved projects, eval runs, voice minutes, and LLM judge credits. Keep deterministic/browser evals cheap; charge for expensive judges, persistence, team workflows, API/CI, and voice minutes.

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

6. Free-To-Paid Loop
   - Free browser eval gives immediate value.
   - Signup saves projects and run history.
   - Paid unlocks LLM judges, integrations, voice runs, and team reporting.

7. Google Cloud Architecture
   - Firebase Auth, Cloud Run, Firestore, Cloud Storage, Pub/Sub/Tasks, Vertex/provider judges.

8. Wedge Markets
   - Call center voice AI first.
   - Then telehealth, online teaching, fintech support, and group-call workflows.

9. Pricing
   - Free, Starter, Team, Business.
   - Charge for run volume, LLM judging, voice minutes, integrations, and team/audit features.

10. Roadmap
   - Real browser evals.
   - Firebase auth/projects.
   - Paid LLM judges.
   - Voice/WebRTC runner.
   - CI/API integrations.
   - vCon import/export and group-call reports.

## Immediate Build Priorities

1. Replace demo/mock paths with a real eval path for pasted transcripts and browser-run text scenarios.
2. Add Firebase Auth and project-scoped saved runs.
3. Add a paid LLM judge abstraction with cost controls and evidence citations.
4. Build one excellent call-center voice AI benchmark suite before expanding too broadly.
5. Add Stripe only after signup, saved projects, and paid judge limits are clear.
