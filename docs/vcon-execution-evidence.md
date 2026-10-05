# Portable execution evidence

ConVoice QA uses the unsigned [vCon core draft-04](https://datatracker.ietf.org/doc/html/draft-ietf-vcon-vcon-core-04) format, version `0.4.0`. This is a draft, not a finalized RFC. Conversation text/audio stays in `dialog`; raw action and state observations are ancillary JSON in standard `attachments`; computed scores are separate `analysis` reports. No custom top-level vCon fields or registered vCon extension are required.

## Profile v1

The attachment has purpose `CAE execution evidence`, media type `application/json`, encoding `json`, and a body with schema `cae-execution-evidence-v1`. Its distributing party is ConVoice QA, not the caller. Its optional dialog anchor identifies the conversation; individual events can carry finer dialog/turn references. See [the profile schema](schemas/cae-execution-evidence-v1.json). It is an application profile inside a standard container, not an IETF-defined tool-call schema.

| Body field | Meaning |
| --- | --- |
| `context` | Suite/scenario/run IDs and contract digests when known. |
| `synthetic` | Fixture/sample evidence; never label this as a live observation. |
| `tool_events` | Invocation lifecycle: stable event ID, call ID, positive capture sequence, name, arguments, result, status, source; optional timestamps, duration, turn/dialog, retry and trace/span IDs. |
| `observed_actions` | Explicit adapter-reported action labels, distinct from tool observations. |
| `state_snapshots` | Explicit before/after/final business state, observation/source metadata, stable snapshot IDs. At most one final snapshot; only final is scored. |
| `voice_events` | LLM output, ASR receipt, speech metadata, audio availability and measured latency. Generated text or an emitted audio segment does not prove browser playout. |
| `redactions` | Paths omitted from this attachment by the export policy. |

Adapters provide `action_trace`, `final_state`, optional `state_snapshots`, transcription turns, latency marks and live events. The common execution exporter captures these directly. It does not derive real tool execution from dialogue or convert CAE bookkeeping such as `reference_conversation_captured` into business state. Adapters without action/state telemetry remain transcript-only. The profile preserves request and result events for the timeline; reevaluation uses the latest explicitly sequenced observation per invocation. Retries need distinct invocation IDs.

## Intake and reevaluation

`POST /api/benchmarks/evidence/intake` returns normalized evaluation inputs and evidence counts. Uploading a complete vCon recovers its suite/scenario, actions and final state. A transcript-only record does not create missing business evidence: Task/Final remain n/a. An imported pass score cannot override a failed, pending, cancelled or timed-out action. Old scores are historical analysis only; Evaluate uses the current Assert 0.3 boundary and pinned scenario contract. A conflicting contract digest or conflicting explicit inputs is rejected instead of silently blended. Deterministic scoring round-trips; separately requested model judging can vary.

Profiles over 2 MB, collections over 10,000 entries, ambiguous final state, duplicate IDs, invalid dialog references, unknown profile versions and unsupported critical vCon extensions are rejected. Unknown non-critical attachments and analysis are preserved when reevaluating/exporting. Intake does not fetch remote recordings or telemetry. This profile requires no provider secret or remote API credentials.

Import is **source-reported, not independently authenticated**. A successful tool result and confirmed business state are separate observations; neither is cryptographic proof. No signing or signature verification is claimed. Credential/runtime keys are removed recursively from the owned evidence attachment. This is not general PII redaction: free text, arbitrary values, transcript/audio and third-party attachments may contain sensitive data. Producers must sanitize those before sharing. Redaction can remove contract-required evidence, and reevaluation should then reflect that loss.

## Product flows

- Transcript sample: explicitly synthetic transcript only.
- Full sample: `GET /api/benchmarks/evidence/sample-vcon`; the shared exporter produces transcript + action trace + final state as a single synthetic vCon.
- Benchmark/export: portable vCon with raw evidence and a separate deterministic evaluation report; saved runs and suite child exports use this format. Suite summary records remain CAE summaries, not fake conversations.
- Run analysis: evidence timeline shows conversation, tool outcomes, voice observations and business state. Without complete timestamps it groups by type and does not invent chronological order.
- Execution download: `GET /api/execution/runs/{run}/conversations/{conversation}/vcon?user_id=...`. Optional `include_audio=true` embeds a local recording with base64url encoding and SHA-512 hash, bounded at 20 MB, after the existing run-owner check. Paths must resolve inside that execution run; no arbitrary file or remote fetch. Default export omits local paths and retains only portable HTTPS recordings with hashes. Old CAE-specific exports remain readable during this feature's rollout; there is one evidence-profile implementation.

## Acceptance checks

The API suite covers complete export/import score equality, conflicting evidence, failures/cancellations/timeouts, historical false success, unknown content, redaction, synthetic labelling and actual adapter observations. The browser upload test loads both samples, evaluates, downloads a full vCon, reuploads it and reproduces Task/Final scores, then confirms edited transcript cannot retain the sample's structured evidence.

Direct action traces and imported vCons now use the same invocation reducer before scoring. A request followed by a success is not a permanent failure; a statusless action remains unknown, not a claimed completed execution. Contract-relevant action IDs and event labels are preserved in the exported profile.
Explicit `status: observed` application events (for example ACC policy-hold events) retain `event_type: action.observed`; they are reported action observations, not successful tool executions. Their contract semantics remain unchanged.

## Provider telemetry alignment (researched after the initial PR push)

There is no universal native voice-agent event format. [Pipecat uses conversation/turn/service OpenTelemetry spans](https://docs.pipecat.ai/api-reference/server/utilities/opentelemetry) and a [FunctionCallEvent lifecycle](https://docs.pipecat.ai/api-reference/server/utilities/observers/function-call-observer). [LiveKit supports OpenTelemetry with `gen_ai.*` and `lk.*` attributes](https://docs.livekit.io/testing/observability/tracing/) and [local SessionReport exports](https://docs.livekit.io/testing/observability/data/). [Vapi stores messages, tool calls/results and derived analysis separately](https://docs.vapi.ai/observability/logs/call-logs). The shared direction is to retain native detail and correlate it, not flatten every platform into invented business events.

`POST /api/benchmarks/evidence/telemetry-vcon` accepts `format`, `data`, optional `transcript`, `suite_id`, `scenario_id`, `final_state` and `synthetic`. It returns a portable vCon ready for the existing upload/Evaluate flow. The native data is preserved as a standard JSON attachment with purpose `Source telemetry (<format>)`, after credential/path redaction. The scoring projection is a separate profile attachment. No provider SDK, collector, webhook endpoint registration or remote fetch is installed by this feature.
Runtime adapters can also return `source_telemetry: {format, data}`; the common execution exporter adds that native attachment and projected voice observations. Native tool events fill an absent action trace but never blend with or double-count an explicit adapter trace. Existing live targets without native telemetry are not automatically instrumented by this ingestion seam.

| Input format | Projection and constraints |
| --- | --- |
| `otlp-json-v1` | Standard JSON ExportTraceServiceRequest (`resourceSpans/scopeSpans/spans`). Preserve resource/scope/schema URLs, trace/span/parent IDs and native attributes. Project `execute_tool` with `gen_ai.tool.name`, `gen_ai.tool.call.id`, arguments/result; also Pipecat's `llm_tool_call/llm_tool_result` with `tool.*`. Explicit tool result status or span OK plus an observed result is needed for success; unset status is unknown. Related lifecycle spans are ordered by reported observation nanoseconds, never OTLP batch order. `metrics.ttfb` is seconds; duration is explicitly milliseconds. |
| `pipecat-function-events-v1` | `{events: [FunctionCallEvent...]}` in callback capture order. Preserve `tool_call_id`, group/blocking metadata and arguments across started/in-progress/completed/failed/timed-out/cancelled moments. Unix timestamps are seconds. Completed can omit result when the observer did not capture it; this is handler completion, not business-state verification. |
| `livekit-session-report-v1` | Python `SessionReport.to_dict()` with `chat_history.items`. Preserve SDK/job/session metadata and native events. Correlate function_call and function_call_output by call_id; require explicit is_error. Preserve per-turn metrics with explicit duration units (seconds). Do not treat generated assistant text or the SDK's playback_latency as proof the human heard it. Node's different camelCase/millisecond report is not silently interpreted as Python. |
| `vapi-call-v1` | Vapi Call object with artifact messages/transcript. Preserve native tool/message/artifact/analysis fields but do not promote successEvaluation or model-extracted structured data to observed actions or final business state. Tool result normalization needs an explicit integration contract; opaque messages remain inspectable voice observations. |

The OpenTelemetry GenAI tool attributes are a developing convention, not a final voice-agent standard: see [the official attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/). Vendor-native attachments therefore remain the loss-aware source of detail. Unknown versions/unsupported shapes fail validation; import/export does not rewrite them into pretend native OTLP spans. The converter is bounded at 2 MB and 10,000 observed items.

Credentials in OTLP KeyValue arrays and serialized JSON arguments are covered by the same loss-reporting policy. This is still not automatic PII redaction: LiveKit `lk.pii.*` content and any native payload containing transcripts/person identifiers require the producer's configured privacy policy. Strip/redact upstream as appropriate; do not claim complete telemetry when content capture is disabled.

Example request (native data is not a fabricated successful state):

```json
{
  "format": "pipecat-function-events-v1",
  "suite_id": "call-center-voice-ai",
  "scenario_id": "billing-address-change",
  "transcript": "Caller: Please update my address.\nAgent: Let me check.",
  "data": {"events": [{"kind": "function_call_started", "function_name": "update_address", "tool_call_id": "call-1", "timestamp": 1790784000, "arguments": {"account_id": "example"}}]}
}
```

## Reading conversation and action evidence

The evaluation view orders observations by timestamp only when all displayed rows
have usable timestamps. Otherwise, it associates events with their explicit
zero-based `dialog` reference (or a one-based `turn_index`) and shows them beneath
that transcript turn. This is a turn association, not a claim about exact execution
timing. Original vCon dialog indices are preserved, including recordings.

Events without a usable transcript link appear in a separate **Unlinked evidence ·
timing unknown** section. Its placement does not imply they occurred after the
conversation. Final state remains a separate observation unless it has timing or
a turn reference. Synthetic full samples include authored turn associations; custom
sample pairs and imported evidence never gain guessed references. Conversational
actions are labeled separately from tool calls and do not imply a real tool ran.
