# Implementation Plan

This repo started as a live sales presenter prototype. The current refactor direction is managed QA and regression testing for voice and conversation agents, with ASSERT treated as optional infrastructure rather than a product lane to compete with.

## Strategic refactor guardrails

- Do not keep positioning the repo as a generic eval harness or generic natural-language requirements to evals product.
- Use ASSERT where it saves time on taxonomy, synthetic scenario generation, trace-aware judging, and local-first artifacts.
- Differentiate on voice-native evidence, external-state task proof, layered failure diagnosis, and client-ready QA reporting.
- If a planned feature still looks generic without voice or conversation evidence, treat it as lower priority.

## Phase 1: Existing repo skeleton
- Scaffold Next.js SPA in `apps/web`
- Create FastAPI app in `apps/api`
- Add local storage path, health check, and run docs

## Phase 2: Existing backend domain
- Define SQLModel entities for decks, slides, sessions, transcript events, presentation events
- Add SQLite session setup
- Implement deterministic session service methods and REST routes

## Phase 3: Existing deck ingestion skeleton
- Accept PDF uploads
- Persist original file
- Stub preprocessing pipeline with PyMuPDF-based page iteration and PNG rendering hooks
- Return deck status and slide metadata

## Phase 4: Existing presentation UI skeleton
- Landing/upload page
- Public presentation route by token
- Slide stage, transcript panel, controls, and question box
- Poll session state from backend

## Phase 5: Existing realtime integration seam
- Add Pipecat tool/service layer contracts
- Add speech/avatar provider interfaces and placeholder adapters

## Phase 6: Evidence ingestion and QA report slice
- Accept transcript, call JSON, action trace, final state, and vCon-like input through the benchmark APIs.
- Normalize those artifacts into one evidence bundle with timestamps, citations, and export readiness.
- Return a QA scorecard, evidence, risks, suggested fixes, and audit-artifact bundle.
- Keep deterministic heuristics for tests while preparing the service for ASSERT-assisted or provider-routed judging.

## Phase 7: ASSERT adapter and compatibility layer
- Add ASSERT import/export support instead of rebuilding a parallel requirements taxonomy and synthetic-eval pipeline.
- Map ASSERT-style traces, behaviors, and artifacts onto the local benchmark run model.
- Keep ASSERT optional so the product still works with direct transcript, vCon, or tool-log ingestion.
- Preserve local-first exports so saved runs can be inspected outside the product.

## Phase 8: Vertical hard checks and layered failure diagnosis
- Pick one vertical first, preferably appointment scheduling or contact-center escalation.
- Define 10-20 hard task-completion checks, not soft transcript-quality heuristics.
- Expand scoring so runs can attribute failure to STT, LLM, tool/API, orchestration, TTS, transfer, or latency layers when evidence supports it.
- Return benchmark evidence as transcript spans, action trace entries, timestamps, final state, failure category, and recommended fix.

## Phase 9: Production failure to regression loop
- Ingest failed production call evidence and convert it into saved scenarios.
- Let operators rerun the same scenario after prompt, model, voice, tool, or orchestration changes.
- Store the regression linkage between the original failure artifact and later reruns.
- Keep voice-specific timing, interruption, and transfer signals first-class in this workflow.

## Phase 10: Client-ready QA operations surface
- Turn benchmark runs into client-facing QA reports with evidence citations, audit trail, and exportable artifacts.
- Add suite-level readiness views for agencies, implementation leads, and acceptance testing.
- Keep benchmark families focused on consequential workflows: call center, telehealth, teaching, fintech.
- Only add broader generic-eval features when they clearly strengthen the voice/conversation QA wedge.

## Current MVP assumptions
- The immediate pain is manual QA and regression testing for production-bound agents, not generic eval authoring.
- Text/mock scenario runs are acceptable as the first automation step only if they lead directly toward voice/conversation evidence workflows.
- vCon compatibility is useful as infrastructure and interoperability, not the core positioning.
- ASSERT should accelerate the generic eval layer; it should not define the product identity.
- Local disk + SQLite are acceptable for prototype speed while evidence models and QA workflows stabilize.
