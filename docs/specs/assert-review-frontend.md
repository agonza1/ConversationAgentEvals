# ASSERT review details in CAE

Status: implemented in PR #152; UI simplified to prioritize review decisions.

## Purpose and boundaries

Extend CAE's existing conversation review panel with useful fields from saved ASSERT assessments. Reviewers should understand which behavior failed, what an ordinal score means, which assessment they are reading, and whether its evidence is current. Keep CAE's run navigation, audio, latency, and vCon experience. Do not embed or vendor the ASSERT viewer.

PR #152 is temporarily stacked on the open portable HTML export PR #151 (`codex/assert-html-report-export`). It reuses that implementation’s selected export action and shared fingerprint logic. Review #152 against that base; retarget/rebase to main after #151 merges. Neither PR is merged by this task.

## Review selection and context

Render saved ASSERT assessments from `conversation.judge_reviews` and their `judge_result.provenance`, including after a page reload. Identify each by `review_id`; let the reviewer choose among saved assessments without changing the applied evaluation. Default to the most recently recorded ASSERT assessment using its timestamp, with a stable fallback for missing/invalid dates. Reset selection when the run or conversation changes. Keep the selected assessment separate from the deterministic evaluation and currently applied adjudication.

Equal or unavailable timestamps prefer the newest appended review (the highest
source-history index). Timestamp ordering does not establish evidence freshness.
All review applications enforce project visibility before selecting a review,
including nonexistent IDs and non-ASSERT proposals.

Show a readable recorded timestamp, review status (pending confirmation, applied, or superseded), judge model, recorded ASSERT version, and evidence level. Never imply that a pending assessment is applied. Preserve the existing explicit apply-confirmation flow. Selecting a historical review, opening details, or checking freshness must not judge, spend credits, mutate evidence, or apply a verdict. If #151's export action is present, it must export the same selected review; avoid a second competing selector.

## Display fields

| Saved field | Presentation | Missing/unsupported behavior |
| --- | --- | --- |
| `dimensions`, `dimension_applicability`, `dimension_justifications` | Continue showing dimension outcome, applicability, and justification. Explicitly distinguish flagged, clear, not applicable, and unavailable. | Null or missing values are unavailable, never clear or zero. Explicit false applicability means not applicable. |
| `dimension_scales` | For a declared ordinal scale, show the label corresponding to the recorded value, retain that value, and offer the rubric in expandable details. | Match values by their recorded type; never confuse number `2` with string `"2"`. If there is no exact matching label, show the recorded value and indicate that its label is unavailable. Do not invent a favorable meaning for high/low scores. |
| `node_judgments` | Expandable behavior rows showing `node_name`, an outcome derived from `relevant`/`violated`, recorded `confidence`, and `reasoning`; omit redundant raw booleans. Order flagged relevant behaviors first, then clear relevant behaviors, then irrelevant/unknown entries; keep stable order within groups. | `relevant: false` is not relevant, not a pass. Unknown/null outcome is unavailable, not clear. Show confidence only when recorded, using its label without inventing a percentage. Never aggregate a new verdict from nodes. |
| `review_id`, review `status`, `created_at`, `model` | A compact selected-review header and history selector with readable status and timestamp. Use numbered review labels instead of raw IDs; retain the exact ID internally for selection/export/apply. | Invalid dates or unknown status are labeled unavailable/unknown; retain identity for review selection. |
| ASSERT version/model | Compact readable context in the selected-review header. Technical hashes stay in the exported report, outside the review UI. | Do not expose artifact paths, raw judge output, credential-bearing metadata, or arbitrary JSON. |
| Recorded evidence references | Within behavior details, show any reliably resolved reference and allow navigation to existing transcript/tool evidence. | Unresolved references remain readable with an explicit unavailable/unresolved state and no fake link. |

Persisted ASSERT node contracts use `node_name`, `relevant`, `violated`, `confidence`, and `reasoning`; do not design against the simplified synthetic export fixture alone. Runtime guards must tolerate absent or malformed optional fields without breaking the panel. Use typed display models with explicit normalization rather than unchecked property casts.

## Evidence freshness

Freshness is independent of pending/applied/superseded status. Show **Current**, **Stale**, or **Cannot verify** for the selected saved review. While checking, show a loading state; a request failure must not display Current.

Compute freshness on the server with existing deterministic snapshots and ASSERT input-fingerprint logic. Include current conversation, target, scenario contract, saved model, and supported judge count inputs. Never compute a trusted freshness result from client-provided hashes or timestamp ordering. Legacy reviews with insufficient provenance are Cannot verify; a known mismatch is Stale. Expose only a safe reason code/message to the UI, without internal paths. Use the existing owner and exact project visibility checks before returning review metadata; wrong owner/inaccessible project must be non-disclosing.

Reuse a common server validation path with #151 rather than allowing frontend and export freshness rules to diverge. The API contract may be a narrow read-only review-status endpoint or additive server-computed metadata, selected during implementation and documented with tests. Fetch it when the selected review is opened/changed; avoid redundant work during run polling. Discard late responses for a previous review or conversation.

For a stale review, explain that evidence changed and link to the existing explicit review action. Do not start a new review automatically. Preserve the apply endpoint's independent stale-evidence enforcement. Do not enable application of a review that the server reports stale or unverifiable.

## Evidence references and layout

Render details beside, or with direct navigation to, CAE's existing transcript and tool evidence. A jump link needs an unambiguous mapping to a recorded message/tool identity. ASSERT judge turn numbers must not be assumed equal to CAE `turn_index`: tools, system messages, and normalized transcripts can use different indexing. Existing string citations without reliable anchors remain unresolved. Do not add a speculative citation resolver in this slice.

Use native disclosures or accessible controls with descriptive names, keyboard navigation, visible focus, and text labels alongside outcome colors. Keep the main panel concise; expand reasoning, rubrics, and behavior details on demand. Omit raw review IDs, technical hash disclosures, duplicate status booleans, repeated caveats, and empty proposal lists. Unresolved references remain explicitly labeled, combined into one line per behavior. On narrow screens, stack sections and wrap long labels without horizontal overflow. Retain audio and timeline controls in the existing conversation view.

## Acceptance and validation

- A saved review renders after reload; history selection changes every detail consistently without changing the applied verdict. If export is present, its request carries the selected `review_id`.
- Boolean, ordinal numeric/string, null, not-applicable, missing-scale, irrelevant-node, unknown-confidence, and legacy/malformed optional fields render honestly. Tests include actual native ASSERT node shapes.
- Freshness tests cover changed transcript, tool evidence, final state, target, scenario contract, insufficient provenance, wrong owner, revoked project access, failed requests, and rapid review/conversation switching. No judging, billing, or state mutation occurs.
- Evidence tests prove that valid anchors navigate to the correct existing item and unresolved/ambiguous references are not clickable. If no supported persisted anchor format exists, test and document the unresolved fallback; do not fabricate anchors to claim integration.
- Render malicious text as text. Visible model/version context omits artifact paths/credentials; raw IDs and technical hashes are absent from the review UI. Preserve values such as `0`, `false`, and fractional scores.
- Browser validation covers persisted reviews, selection, disclosures/keyboard operation, freshness loading/errors, and desktop/mobile layout. Use synthetic saved fixtures, plus a real local API-to-browser check for any added freshness API; no paid judging is required.
- Run relevant API/frontend tests, lint, production build/types, and required CI. Save and inspect screenshots of the behavior breakdown, ordinal labels, history, and stale/unverifiable states. Implementation is ready for review only when the defined behaviors pass and any remaining limitations are explicit in the PR.

## Out of scope

New scoring rules or confidence aggregation; suite-level dashboards/comparisons; new judges; raw-output persistence; audio playback redesign; new authentication; upstream viewer embedding; inferred citation links; automatically applying or rerunning assessments.

## Implemented API contract

`GET /api/assert/runs/{run_id}/conversations/{conversation_id}/reviews/{review_id}/status?user_id={owner}` returns the exact three IDs plus `status` (`current`, `stale`, `cannot_verify`), a bounded `reason_code`, and a safe message. It never returns paths or raw review metadata. Owner and exact project visibility are checked before review lookup.

A common server service checks terminal state, deterministic evidence identity, saved ASSERT/model provenance and original input fingerprints across supported judge counts. Export uses the same service. ASSERT application checks it independently inside the execution-store lock, including legacy `assert-ai` reviews missing provenance; non-ASSERT proposal semantics remain unchanged. The client discards responses tagged for previous selections, disables apply until Current, and requires explicit confirmation.

There is no reliable persisted evidence-anchor format in current native nodes/string citations, so all evidence references remain explicitly unresolved text; no turn-index links are fabricated.

## Resolution Evidence simplification

Keep the effective resolution status and recorded evaluator verdict together in one summary. Retain the evaluation score and evaluation basis, including unavailable scores, and all recorded action/error/outcome evidence. Omit optional final-state, termination, and live-tool rows when unreported; recorded false values and zero scores remain explicit.

Consolidate the explanation and actionable gaps under “Why this outcome”. Keep score-versus-resolution-rate guidance in the existing keyboard-accessible evaluation-basis help rather than a repeated panel footnote. Remove the repeated LLM introduction; keep the explicit review action and applied-adjudication audit history. This presentation change must not alter verdicts, scores, judge routing, or apply/export behavior.
