# Spec Refinement Notes

## Decisions
- Pivot the MVP wedge from production-call monitoring to automating the manual voice-agent test-call workflow.
- Position the product as a repeatable scenario test runner first; eval reports are the output, not the whole product.
- Expand the product direction from voice conversation QA to agentic behavior benchmarks: did the agent complete the requested task, call the right tools, obey constraints, and reach the expected final state?
- Use WebRTC.ventures-branded domain benchmarks as the strategic wedge: call center voice AI, telehealth, online teaching, and fintech support.
- Keep vCon compatibility as an import/export and normalization layer, but avoid making it the primary buyer-facing promise.
- Keep the MVP strictly single-operator, single active visitor session per public token.
- Treat FastAPI as the only authority for deck/session/slide state; Pipecat reads and mutates through backend services/tools.
- Use REST plus short-interval polling for session/transcript updates in the first cut; postpone websockets/SSE until the state model is stable.
- Precompute slide summaries, talk tracks, FAQs, and lightweight retrieval artifacts during ingest so live Q&A stays cheap and predictable.
- Keep the speech stack behind provider interfaces so the first demo can ship with text-only Q&A if avatar speech wiring lags.

## Challenges/Risks
- Automated voice calling can become integration-heavy too early; prove the scenario/rubric loop with text or mock harnesses first.
- "All agentic systems" is broad; the MVP must stay narrow by shipping one benchmark family first, likely call center voice AI or telehealth.
- Evaluating final state requires either tool traces, mock tools, or explicit observed outcomes. Conversation-only scoring is not enough.
- Buyers may describe the pain as "testing takes too long" rather than "evals"; homepage and onboarding should use their current workflow language.
- The first product must avoid feeling like enterprise observability infrastructure.
- HeyGen session lifecycle and synchronization with backend slide transitions may be slower than the rest of the stack.
- Realtime interruption logic is the highest integration risk; the state machine must remain valid even if speech/avatar providers fail mid-turn.
- PDF decks with sparse text or image-heavy slides will degrade grounding quality unless OCR is added later.
- OpenAI Realtime and the Deepgram/OpenAI/Cartesia path should be modeled as pluggable transports, not embedded in core domain logic.

## Recommended Repo Structure
- `apps/web`: Next.js App Router SPA for upload, session launch, and presentation page.
- `apps/api`: FastAPI app with routes, services, SQLModel models, preprocessing, and Pipecat-facing tools.
- `packages/shared`: optional future home for TS API types/openapi-generated client.
- `storage/decks`: local filesystem deck artifacts for MVP.
- `docs`: refined plan, assumptions, and integration notes.

## API Notes
- Prefer resource-oriented routes under `/api` with explicit session transition endpoints for start/pause/resume/end.
- Add benchmark-oriented routes as the new product grows: scenario templates, benchmark suites, benchmark runs, action traces, and reports.
- Add a lightweight `GET /api/health` and `GET /api/sessions/{id}/state` shape for UI polling.
- Return both canonical session status and current slide index from mutation endpoints so the SPA can reconcile quickly.
- Keep `ask` synchronous for the first cut, but structure the service layer so async/background execution can replace it later.

## Assumptions
- No auth, no tenant isolation, no deck editing, and local storage only for MVP.
- Demo can begin with text-driven Q&A and mocked avatar/session wiring where external credentials are unavailable.
- SQLite is sufficient for the first demo as long as state transitions are centralized in service functions.

## Blockers
- Exact HeyGen integration mode and credentials are unknown.
- Pipecat package/runtime choices are not yet pinned in this repo.
- Embedding model/provider is still an implementation choice; current skeleton stores retrieval-ready text and leaves vector persistence swappable.
