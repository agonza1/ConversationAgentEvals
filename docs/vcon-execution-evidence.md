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
