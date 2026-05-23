# Implementation Plan

This repo started as a live sales presenter prototype. The current MVP direction is an agentic AI benchmark runner: help builders test whether AI agents complete real domain tasks across text, voice, and tools.

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

## Phase 6: Voice AI QA report slice
- Add `/api/evals/run` for transcript, call JSON, or vCon-like input.
- Add plain-English criteria input.
- Return scorecard, evidence, risks, suggested fixes, and vCon-compatible analysis export.
- Keep deterministic heuristics for tests while preparing the service for LLM-backed judging.

## Phase 7: Scenario test runner slice
- Add saved scenario templates with a user persona, user goal, required actions, forbidden actions, expected final state, edge cases, and pass/fail rubric.
- Let users run a scenario against a sample transcript or mock/text harness first.
- Persist eval runs so a user can compare results across prompt/model/tool changes.
- Add rerun support for the same scenario suite.

## Phase 8: Agentic action evaluation
- Capture tool/action traces from the agent-under-test.
- Evaluate whether the agent selected the right tool, passed correct arguments, followed workflow order, and reached the expected final state.
- Return benchmark evidence: transcript, action trace, final state, failure category, and recommended fix.
- Keep voice-specific timing/interruption metrics optional until the text/tool benchmark loop is useful.

## Phase 9: Automated call execution
- Add a connector interface for target voice agents.
- Start with the lowest-friction target: HTTP/WebSocket/text harness if available.
- Add WebRTC/phone calling only after the scenario + QA loop is useful.
- Capture transcripts and metadata into a normalized call record, with vCon export as a compatibility layer.

## Phase 10: WebRTC.ventures benchmark suites
- Call Center Voice AI Benchmark.
- Telehealth Agent Benchmark.
- Online Teaching Agent Benchmark.
- Fintech Support Benchmark.
- Each benchmark should ship with public sample scenarios, scoring rubrics, failure taxonomy, and a demo report.

## Current MVP assumptions
- The immediate pain is manual agent testing, not production monitoring.
- Text/mock scenario runs are acceptable as the first automation step if they test real agentic behavior.
- vCon compatibility is a backend/export advantage for voice artifacts, not the homepage headline.
- The homepage promise is task completion: "Test whether your AI agent can actually do the job."
- Local disk + SQLite are acceptable for prototype speed.
