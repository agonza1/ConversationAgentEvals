# Minimal-typing test authoring

`/specs/new` and `/scenarios?create=1` share the assisted builder. Describe a test once or choose a starter. Existing scenario detail offers **Make a variation**, with its explicitly displayed source context. No model call occurs on page load, typing, starter selection, or choosing a variation.

**Draft** explicitly invokes the configured CAE generation provider/model through the existing connection path. `/api/specs/assist` returns proposed rules, caller instructions, expected outcomes, an assistance boundary, evidence requirements and visible assumptions. It never saves, publishes, calls a target or invokes a judge. It does not invent operational evidence or execute generated expressions/tools.

A quick case has one scenario; a test set starts with three variants and can have at most nine cases. Critical missing permissions/policy can produce at most two short questions with clickable options. Their answers require another explicit draft request. Further model-generated questions after answers are rejected rather than creating an open-ended agent loop. Generated business rules remain suggestions for human review; model instructions are not proof against hallucination or prompt injection.

Every clarification carries its full question text with its answer, including short free-form answers such as “yes” and opaque IDs such as `q1`. The stateless endpoint rejects answers without matching question context. Both are retained in the approved source requirements. Inferred agent roles and caller profiles are visible before approval and editable; role changes also appear in refinement summaries. No hidden persona is prepended to execution instructions without being shown for review.

Refinement includes the current edited draft, displays changed/added/removed rules and cases, and retains the previous draft for Undo. Changing the request disables approval until the draft is updated. Errors preserve existing work. Cancellation stops waiting and rejects late responses; a remote model may continue processing. A nonblocking per-process generation slot rejects overlapping requests with 429 (no automatic retries). This is a local resource bound, not a multi-worker quota or billing guarantee.

The UI keeps title/persona inference and descriptions out of the initial form. Rules and caller cases are editable in review disclosures. Required evidence and the assistance boundary stay visible. Missing tool/state observations do not prove that an operational action failed. Dedicated case counts distinguish targeted rules from the rest of the policy context.

Only **Approve and create case** persists a quick case. It retains caller instructions, explicit rules, evidence requirements and source/boundary context across catalog reloads. **Approve test set and choose agent** uses the existing immutable save and confirmed publication workflow, including retrying the same saved version. It opens target selection without starting execution. Nothing is silently approved just because generation finished.

The manual editors remain available through **Use detailed editor**; existing saved spec links open the detailed editor, and `editor=manual` explicitly selects it. Advanced ASSERT presets, YAML, programmatic checks and publication controls remain available. Starting a fresh quick case resets previous context. Existing legacy quick cases without the new optional evidence/context fields keep their prior contract.

All regression fixtures use synthetic data and mocked generation. This change does not deploy/restart local services or include Waylo PR #163. Policy-file ingestion, a Results-to-regression shortcut and importing external agent instructions are follow-up work, not hidden background retrieval.
