# Waylo targets and evidence

Waylo is one reusable **Targets** adapter. Mike is a saved Waylo agent configuration
and a catalog-aware notes contract, not a new target type. Existing Waylo transcript
examples are unchanged and remain transcript-only.

## Configure

Add a voice target, select **Waylo agent (LiveKit)**, and provide:

- API base URL, including any deployment API prefix (HTTPS except local development).
- Workspace UUID and Waylo agent UUID.
- Opaque credential reference, e.g. `waylo-staging`. Administrators provision the
  bearer key in `CAE_HTTP_TARGET_SECRET_WAYLO_STAGING`; do not paste keys into forms.

Alternatively select **Temporary local sign-in** and **Connect Waylo** in Targets.
CAE displays the operator-approved Waylo API origin before asking for consent.
The password is forwarded once to Waylo's `/auth/login`, never saved, and cleared
from the form. CAE verifies `/auth/me` and the selected agent, then fills its workspace
UUID automatically. A platform administrator must explicitly supply the owning
tenant UUID; its target uses Waylo's `/admin/tenants/{tenantId}` API mount.

The Waylo access JWT stays only in one API process's memory, until its actual expiry
or 15 minutes, whichever comes first. No refresh cookie is retained or replayed.
The browser holds an opaque control proof only in JavaScript memory: never in a
cookie, localStorage, sessionStorage, a URL, or a saved target. A full reload requires
reconnecting. Saved targets contain only public identifiers and
`auth_type=waylo_browser_session`, not a credential reference. There is no silent
fallback to an environment key. Imported capture, readiness and run queueing require
the originating browser's proof and local same-origin control headers. Credentials
are rechecked on HTTP retries and during RTC; disconnect, expiry and successful
reconnect cancel active calls. Queueing reserves call/cleanup/capture time for each
selected scenario and rejects runs longer than the available token lifetime. Start
with one controlled Mike case, not the entire suite.

Temporary sign-in is a **local, single-API-worker preview**, not general-purpose
multi-user OAuth. `CAE_WAYLO_API_BASE_URL` must match an operator-provisioned
`CAE_WAYLO_ALLOWED_API_ORIGINS` HTTPS origin. UI origins must be explicitly ported
loopback URLs in `CAE_WAYLO_ALLOWED_ORIGINS`. Open CAE through its same-origin API
proxy, not an external `api_base` override. A restart loses all connections; a tab
crash/reload loses its proof, while an already-started call remains bounded by its
timeout and server token expiry. Use **Disconnect Waylo** before leaving to revoke
an active call immediately. A sign-in does not launch a call or submit evaluation.
Dashboard authority is revalidated by Waylo on each operation: being able to read
an agent does not prove permission to start calls or read native events. Missing
permissions/evidence remain explicit errors or unknown coverage.

Use `sessions:create` for web calls, `sessions:read` and `transcripts:read` for capture,
with access to the session's workspace. Native `/events` requires workspace admin
access. Historical `/agents/{id}/versions/{version}` additionally needs `agents:read`.
Capture remains usable with partial permissions; missing optional data is reported,
not replaced by fabricated tool success or business state. API redirects are not followed.

The normal Run agent flow starts `POST /sessions/web` with a deterministic
`Idempotency-Key`, then joins the **returned LiveKit room** using the pinned Python
RTC SDK. Relay-only TURN information is honored. There is no text-call fallback.
Caller speech uses CAE's existing tester/TTS; fixed reference cases are not paraphrased.
Free-form scenarios use the existing adaptive tester for subsequent utterances.
Tester audio accepted by AudioSource and target audio received by AudioStream are
retained as separate WAV artifacts, including accepted samples on a failed send.
The existing live-turn HTTP playback/capture flow is reused. This adapter does not
publish a Pipecat listener WebRTC bus; do not confuse listener transport with the
actual Waylo call's LiveKit connection.

**Calls capture only. Evaluation is always manual**, even if a client sends
`evaluate=true`. Use the existing vCon download and Eval intake / saved ASSERT review.
No benchmark evaluation is queued by creating a Waylo call or capturing a session.

For an existing human call, enter its session UUID in the saved target's inline
capture control. This reads metadata, **all cursor-paginated transcript pages**,
native events and `/items`, and downloads a portable vCon for manual upload in Eval.
The capture endpoint is `POST /api/agents/{target_id}/capture` with `{"session_id":"UUID"}`.
This is explicitly an operator-declared human-call import, not an AI tester reference.
Automated executions instead record `source_call_kind=cae_ai_tester` with real sent audio.

## Coverage, mapping and honest limits

Each export contains the standard `cae-execution-evidence-v1` profile and a sanitized
ancillary native-source attachment. Raw source is not automatically scoreable.

| Area | Mapped observations | Unknown unless actually captured/proven |
| --- | --- | --- |
| Conversation experience | Actual accepted/received audio, local send timing, transport lifecycle | Target playback acknowledgements, target interruption handling, exact continuity/silence semantics, provider metric units |
| Speech boundary | Reference utterance, actual sent WAV hash/samples, local queue drainage, available RTC partial/final segments, complete durable user transcripts | Peer delivery acknowledgement, authoritative cross-clock alignment, whether a discrepancy is ASR error or peer packet loss |
| Agent execution | `payload.tool_invocation_id` correlation, transcript parent keys, explicit args/results/status/code, reported retry/turn/policy fields | Omitted/truncated payloads, absent associations/policy decisions; accepting a request is not durable success |
| Business outcome | Read-only backend request-list before/after/final snapshots, controlled expected-list diagnostic | Semantic catalog resolution, spoken confirmation alignment, durable catalog-product association |

Native `occurredAt` uses Waylo worker UTC, `createdAt` is API storage UTC. Tester
timing is explicitly milliseconds since transport creation, with a UTC anchor.
RTC transcription timestamps are preserved with **unknown native units/clock**:
the SDK shape alone does not establish the worker's convention. Native turn metric
payloads remain unscored except worker-reported `interrupted` and `e2e_latency`
(seconds mapped to milliseconds), whose semantics were verified in worker source. Independent
worker/client clocks are never subtracted to manufacture latency.

Tool requests are `requested`; results are successful only with an explicit
`payload.status=success` and result data. `UPSTREAM_ACCEPTED` and
`DELIVERY_UNCONFIRMED` stay unknown. Orphan results are preserved natively but not
assigned invented invocation IDs. The worker emits call `payload.args`, result
`payload.result`, and `payload.tool_invocation_id`; `parentIdempotencyKey` links the
active user transcript, not a tool request. Size-limited argument/result previews
remain explicitly incomplete. Both modern `lk.transcription` text streams and the
legacy RTC transcript event are supported; neither proves remote audio delivery.

Order-aligned one-to-one WER and product/quantity/unit comparisons are diagnostics,
not grades. Split/merged ASR utterances stay unaligned/unknown. Local clipping is
distinct from unknown remote loss; downstream interpretation requires tool/backend
comparison. Partial coverage is shown separately, and missing interruption evidence
is shown as **unknown**, never a measured zero. No verified resolution is claimed.

Export identity is deterministic by CAE target + provider session. Native IDs and
tool parent keys preserve turn/invocation correlation where present. Refresh rereads
the entire paginated transcript (never detail's 500-message preview). Late flushes
update the revision of the same vCon UUID, without append-only duplicate exports.
The adapter samples after disconnect, but a stable response does not prove all future
updates have arrived; refresh again for late writes. Size/sequence errors reject
truncated capture rather than asserting completeness.

Credentials, participant tokens, TURN data, auth headers, signed URLs and opaque
strings containing known credentials are redacted/excluded. Bootstrap credentials
remain memory-only. Large local recordings may not fit the bounded portable export:
the actual local WAVs remain in CAE; only bounded inline caller audio is exported.
No local filesystem path is treated as portable vCon media.

## Mike fixtures and contract

The `waylo-mike-notes` suite includes five-bags/pack-size, ambiguity, missing/discontinued,
lookup failure, quantity/unit correction, interrupted list, order boundary and confusable
entity cases. Additional confusable variants apply seeded white noise at 20 dB requested
SNR or an operator-selected synthetic voice via `WAYLO_TEST_ACCENT_VOICE`.
Only those conditions are tested; clean TTS is not evidence of general accent/noise robustness.

Fixture catalog data is **synthetic setup**, not fetched production catalog evidence.
Configure an isolated notes-only test agent and equivalent catalog before live tests;
inject lookup failure in that fixture environment. The adapter does not alter Waylo
catalogs, presets, credentials, or orders. A controlled `audio_plan_path` can instead
reference existing local `AccAudioPlan` fixtures under CAE's approved artifact/example
roots; each fixture needs `metadata.reference_text` and can pin `sha256`.
There is no silent TTS substitution when recorded fixtures are exhausted.

The contract requires focused `search_documents`, catalog-supported matches,
clarification before ambiguous capture, exclusion of unavailable/discontinued items,
canonical name confirmation while preserving caller quantity/unit, brief paced
acknowledgements, note corrections and a backend-based final read-back. Creating,
submitting or modifying orders is forbidden. Five **bags** stays five bags regardless
of a 10 kg catalog pack size. Success reconciles requests, lookup trace, notes and speech.

Check the **effective configuration before judging**. The session API hides its
internal configuration snapshot. Capture retrieves its exact historical agent version
when authorized, never substitutes today's mutable agent configuration. Versioned
persona alone cannot prove worker baseline/preset rules. Known `item_capture_assistant`
no-catalog rules override the persona and conflict with Mike. Mike calls are blocked
for that preset and `catalog_order_assistant`; a generic Waylo target is not limited
to Mike. Unknown effective rules remain needs review, not compliance failure.

Waylo items contain position, raw text, quantity and unit; positions are mutable list
coordinates, not product IDs. Preserve catalog lookup results separately in the trace.
Never claim backend canonical-product association or an order transaction from notes.

Source contracts inspected at Waylo application commit
`a3848b558188f77523d99324c811ac1cb0a709b2`:
[sessions](https://github.com/conversational-wayloai/application/blob/a3848b558188f77523d99324c811ac1cb0a709b2/packages/api/src/modules/sessions/sessions.controller.ts),
[transcripts](https://github.com/conversational-wayloai/application/blob/a3848b558188f77523d99324c811ac1cb0a709b2/packages/api/src/modules/transcripts/transcripts.controller.ts),
[items](https://github.com/conversational-wayloai/application/blob/a3848b558188f77523d99324c811ac1cb0a709b2/packages/api/src/common/dto/session-item.response.ts),
[Item Capture preset](https://github.com/conversational-wayloai/application/blob/a3848b558188f77523d99324c811ac1cb0a709b2/packages/api/src/modules/presets/item-capture-assistant.ts).
Worker telemetry inspected at commit `615d2b78c4e4cf6fb48b930fe42ddf1508dc321c`:
[handlers](https://github.com/conversational-wayloai/agent/blob/615d2b78c4e4cf6fb48b930fe42ddf1508dc321c/src/waylo_agent/events/handlers.py).
Transport: [LiveKit raw tracks](https://docs.livekit.io/transport/media/raw-tracks/),
[text streams](https://docs.livekit.io/agents/multimodality/text/).
