# Evaluate existing conversation evidence

Changing scenario criteria keeps supplied conversation evidence and clears its result/review; it does not replace the conversation with a scenario sample, including on a `sample=1` deep link after manual input. Existing source-bound contract validation still applies.

The `/eval` flow does not initiate a target call: import/paste evidence, explicitly evaluate against a scenario, optionally request an ASSERT LLM review. Importing, typing, selecting a scenario and deterministic evaluation never implicitly request a model judgment.

The primary result describes evidence coverage and verification limits. A transcript-only 100 remains an unverified operational outcome. Built-in phrase/action and keyword/rubric scores remain available under **Diagnostic score and check matches**, with original numeric fields unchanged for compatibility. Phrase matches are not completed backend actions; unmatched phrases are not proof an action failed. High scores never override a needs-review verdict in the operator plan.

New reports and derived vCon analyses contain `evaluation_scope` (`cae-evaluation-scope-v1`): input source, artifact presence, explicit synthetic marker when supplied by the recognized profile, trust boundary, diagnostic-score meaning and semantic-review requirement. A newly generated vCon export does not establish original vCon intake. Historical imported analyses/scores are not raw execution evidence. Unknown legacy coverage remains “Not recorded,” not zero or success.

Tool/action traces and resulting state are labelled **supplied**, not independently authenticated. A request is not a successful result. LLM judgment is displayed separately and cannot manufacture a receipt or promote a deterministic verdict merely by agreeing. Missing operational evidence is unverified, not automatically failure. Purely conversational behavior can still be judged from the transcript.

Editing supplied evidence invalidates its current result/review display. A late review response cannot attach to a different report. The source review may finish remotely; cancellation of browser waiting is not cancellation of provider work. Existing saved results remain readable with conservative fallbacks.

Regression fixtures are synthetic; no vendor/voice/model calls are made by validation. This PR does not deploy or restart the local preview and is separate from the speaker-parser/ASSERT detailed-report changes in #175.

The execution-evidence profile's `synthetic` marker accepts `true`, `false`, or explicit `null` (unknown). Existing Boolean markers remain valid; an unknown marker stays unknown through export/re-import rather than becoming a non-synthetic assertion. Exact server-side matches to canonical catalog transcript fixtures are labelled synthetic with the basis `exact_catalog_transcript_match`, even when checked against different criteria. That content match does not authenticate an external source.
