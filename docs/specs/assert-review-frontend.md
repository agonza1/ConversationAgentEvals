# ASSERT review details in CAE

Status: proposed implementation spec. This PR defines the work; it does not implement the frontend.

## Purpose and boundaries

Extend CAE's existing conversation review panel with useful fields from saved ASSERT assessments. Reviewers should understand which behavior failed, what an ordinal score means, which assessment they are reading, and whether its evidence is current. Keep CAE's run navigation, audio, latency, and vCon experience. Do not embed or vendor the ASSERT viewer.

The portable HTML export in PR #151 remains a separate feature. This work may reuse its saved-review validation and input-fingerprint helpers after that change lands. This spec PR targets main and does not include #151's implementation commits.

## Review selection and context

Render saved ASSERT assessments from `conversation.judge_reviews` and their `judge_result.provenance`, including after a page reload. Identify each by `review_id`; let the reviewer choose among saved assessments without changing the applied evaluation. Default to the most recently recorded ASSERT assessment using its timestamp, with a stable fallback for missing/invalid dates. Reset selection when the run or conversation changes. Keep the selected assessment separate from the deterministic evaluation and currently applied adjudication.

Show a readable recorded timestamp, review status (pending confirmation, applied, or superseded), judge model, recorded ASSERT version, and evidence level. Never imply that a pending assessment is applied. Preserve the existing explicit apply-confirmation flow. Selecting a historical review, opening details, or checking freshness must not judge, spend credits, mutate evidence, or apply a verdict. If #151's export action is present, it must export the same selected review; avoid a second competing selector.

## Display fields

| Saved field | Presentation | Missing/unsupported behavior |
| --- | --- | --- |
| `dimensions`, `dimension_applicability`, `dimension_justifications` | Continue showing dimension outcome, applicability, and justification. Explicitly distinguish flagged, clear, not applicable, and unavailable. | Null or missing values are unavailable, never clear or zero. Explicit false applicability means not applicable. |
| `dimension_scales` | For a declared ordinal scale, show the label corresponding to the recorded value, retain that value, and offer the rubric in expandable details. | Match values by their recorded type; never confuse number `2` with string `"2"`. If there is no exact matching label, show the recorded value and indicate that its label is unavailable. Do not invent a favorable meaning for high/low scores. |
| `node_judgments` | Expandable behavior rows showing `node_name`, `relevant`, `violated`, recorded `confidence`, and `reasoning`. Order flagged relevant behaviors first, then clear relevant behaviors, then irrelevant/unknown entries; keep stable order within groups. | `relevant: false` is not relevant, not a pass. Unknown/null outcome is unavailable, not clear. Show confidence only when recorded, using its label without inventing a percentage. Never aggregate a new verdict from nodes. |
| `review_id`, review `status`, `created_at`, `model` | A compact selected-review header and history selector with readable status and timestamp. | Invalid dates or unknown status are labeled unavailable/unknown; retain identity for review selection. |
| `input_fingerprint`, `score_sha256`, `output_sha256`, ASSERT version/model | An expandable technical provenance panel. | Show allowlisted recorded identifiers only. Do not expose `artifacts` paths, raw judge output, credential-bearing metadata, or dump arbitrary JSON. |
| Recorded evidence references | Within behavior details, show any reliably resolved reference and allow navigation to existing transcript/tool evidence. | Unresolved references remain readable with an explicit unavailable/unresolved state and no fake link. |

Persisted ASSERT node contracts use `node_name`, `relevant`, `violated`, `confidence`, and `reasoning`; do not design against the simplified synthetic export fixture alone. Runtime guards must tolerate absent or malformed optional fields without breaking the panel. Use typed display models with explicit normalization rather than unchecked property casts.

## Evidence freshness

Freshness is independent of pending/applied/superseded status. Show **Current**, **Stale**, or **Cannot verify** for the selected saved review. While checking, show a loading state; a request failure must not display Current.

Compute freshness on the server with existing deterministic snapshots and ASSERT input-fingerprint logic. Include current conversation, target, scenario contract, saved model, and supported judge count inputs. Never compute a trusted freshness result from client-provided hashes or timestamp ordering. Legacy reviews with insufficient provenance are Cannot verify; a known mismatch is Stale. Expose only a safe reason code/message to the UI, without internal paths. Use the existing owner and exact project visibility checks before returning review metadata; wrong owner/inaccessible project must be non-disclosing.

Reuse a common server validation path with #151 rather than allowing frontend and export freshness rules to diverge. The API contract may be a narrow read-only review-status endpoint or additive server-computed metadata, selected during implementation and documented with tests. Fetch it when the selected review is opened/changed; avoid redundant work during run polling. Discard late responses for a previous review or conversation.

For a stale review, explain that evidence changed and link to the existing explicit review action. Do not start a new review automatically. Preserve the apply endpoint's independent stale-evidence enforcement. Do not enable application of a review that the server reports stale or unverifiable.

## Evidence references and layout

Render details beside, or with direct navigation to, CAE's existing transcript and tool evidence. A jump link needs an unambiguous mapping to a recorded message/tool identity. ASSERT judge turn numbers must not be assumed equal to CAE `turn_index`: tools, system messages, and normalized transcripts can use different indexing. Existing string citations without reliable anchors remain unresolved. Do not add a speculative citation resolver in this slice.

Use native disclosures or accessible controls with descriptive names, keyboard navigation, visible focus, and text labels alongside outcome colors. Keep the main panel concise; expand reasoning, rubrics, behavior details, and technical provenance on demand. On narrow screens, stack sections and wrap long labels without horizontal overflow. Retain audio and timeline controls in the existing conversation view.

## Acceptance and validation

- A saved review renders after reload; history selection changes every detail consistently without changing the applied verdict. If export is present, its request carries the selected `review_id`.
- Boolean, ordinal numeric/string, null, not-applicable, missing-scale, irrelevant-node, unknown-confidence, and legacy/malformed optional fields render honestly. Tests include actual native ASSERT node shapes.
- Freshness tests cover changed transcript, tool evidence, final state, target, scenario contract, insufficient provenance, wrong owner, revoked project access, failed requests, and rapid review/conversation switching. No judging, billing, or state mutation occurs.
- Evidence tests prove that valid anchors navigate to the correct existing item and unresolved/ambiguous references are not clickable. If no supported persisted anchor format exists, test and document the unresolved fallback; do not fabricate anchors to claim integration.
- Render malicious text as text. Technical details expose only allowlisted fields and omit artifact paths/credentials. Preserve values such as `0`, `false`, and fractional scores.
- Browser validation covers persisted reviews, selection, disclosures/keyboard operation, freshness loading/errors, and desktop/mobile layout. Use synthetic saved fixtures, plus a real local API-to-browser check for any added freshness API; no paid judging is required.
- Run relevant API/frontend tests, lint, production build/types, and required CI. Save and inspect screenshots of the behavior breakdown, ordinal labels, history, and stale/unverifiable states. Implementation is ready for review only when the defined behaviors pass and any remaining limitations are explicit in the PR.

## Out of scope

New scoring rules or confidence aggregation; suite-level dashboards/comparisons; new judges; raw-output persistence; audio playback redesign; new authentication; upstream viewer embedding; inferred citation links; automatically applying or rerunning assessments.
