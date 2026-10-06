# Spec-driven scenario authoring

CAE now connects Evaluation design to the runnable scenario catalog. It uses the
existing immutable ASSERT-compatible design store, not a second policy store.

## Workflow

1. Open `/specs/new` (also linked from `/scenarios`). Enter the agent role,
   objective, product requirements, and an explicit permissible boundary.
2. Optionally select a behavior preset and application-context preset from the
   installed `assert-ai==0.3.0` library. CAE does not copy the library. An ASSERT
   scenario preset supplies application context; it is not a runnable CAE case.
3. Generate proposed checks or write them manually. Review each atomic behavior,
   its definition, and any source quote. Quotes are validated against the supplied
   requirements/objective; they do not constitute proof that a generated rule is
   correct. Approve generated behavior drafts before case generation.
4. Select behavior IDs and generate three proposed cases per behavior: normal,
   boundary, and adversarial. Generation replaces the case list and requires fresh
   approval. Requests are limited to 20 cases. Review the coverage list: publishing
   partial coverage is allowed and does not imply every behavior has been tested.
5. Edit the case's focus behavior, variant, caller instructions and expected
   outcome. The first caller instruction is the concrete opening utterance;
   later instructions guide the adaptive text/voice tester, never the target.
6. Approve drafts, save a version, then explicitly confirm publication to the
   **shared local catalog**. Unsaved edits disable publishing. Publication does
   not queue a run, invoke a target, or start a paid semantic evaluation.
7. Select the published suite in Scenarios, Runs or Eval. Its rules are derived
   from the exact saved design version. Saving v2 never changes a v1 suite.

Quick-create on `/scenarios` also accepts explicit required/forbidden behaviors.
It does not parse the expected-outcome paragraph into rules. Older API clients
that omit rule fields retain the historical generic defaults; an explicitly empty
required list is rejected. Quick-created scenarios are not versioned designs.

## API and persistence

- `GET /api/specs/assert-library/scenarios`: native ASSERT application contexts.
- `POST /api/specs/generate`: accepts requirements, permissible boundary and preset
  names in addition to title/role/objective. Returned content is always a draft.
- `POST /api/specs/generate-cases`: `{spec, behavior_ids, samples_per_behavior}`.
  Checks IDs/counts/variants/openers/outcomes and returns reviewable drafts with
  provider/model provenance, without modifying the saved policy.
- `GET /api/specs/{id}?user_id=...&project_id=...&version=N`: exact saved version.
- `POST /api/specs/{id}/publish-scenarios`:
  `{user_id, project_id, version, confirm: true}`. Requires visible design and
  workspace editor/owner access; validates approval, rules and executable cases.
  Returns the stable suite ID and per-behavior case counts. Repeating the request
  is idempotent. The UI can reopen a version with `/specs/new?spec_id=...&project_id=...&version=N`.

`published_assert_scenario_sets` stores only a unique pointer to an immutable
`editable_assert_spec_versions` row. Catalog entries are reconstructed from that
row on reads, including after a restart or another worker publishes. Explicit
publication exposes the design's cases/rules in CAE's existing shared local
catalog; this is not a private multi-tenant publishing system.

The scenario contract (and its digest, recorded run and exported vCon analysis)
retains the spec/version reference, complete behavior definitions, focus ID,
variant, caller instructions, permissible boundary and generation provenance.
Each case's action checklist contains only its focus behavior, with its ID,
kind, label and definition. Displayed action names include `[behavior-id]` so
equal labels are not conflated. The complete policy remains context, not a
claim that this case exercises every rule in the design.
No duplicate ASSERT runtime or legacy-version implementation is introduced.

## Evaluation and boundaries

Draft generation uses CAE's existing configured LLM, including its supported
connected Codex-account path. Provenance says `cae_configured_llm`; it does **not**
claim that ASSERT's `systematize`, `test_set` or `inference` stages executed.
Native ASSERT YAML preview/export remains available and validated by ASSERT 0.3.

CAE's automatic rule-based evaluation uses existing action/evidence heuristics.
Arbitrary natural-language policies are not semantically proved by matching an
action label. Published designs therefore remain `needs_review` under that
evaluator, even if its measured action score is 100. No keyword rubric is invented.
For published designs, rule-action observations require an explicit trace
`behavior_id`, an action name equal to that ID, or the full displayed action name
including `[behavior-id]`, plus a successful/observed status. Bare labels or
spoken claims do not establish which authored rule was exercised. These are
source-reported associations, not independently verified semantic judgments.
A forbidden-focus case has required actions n/a, not missing unrelated rules.
Absence of ID-linked forbidden evidence is also n/a, not a proved 100% pass.
The optional upstream ASSERT semantic judge receives full definitions, boundary,
source requirements and library context, and reports its independent judgment.
It uses the existing provider/credential/budget configuration; a Codex login alone
does not guarantee credentials for an ASSERT/LiteLLM model endpoint.
Saved judge controls configure the exported ASSERT YAML. Publishing cases does
not change CAE's global semantic-judge model, dimensions or budget settings.

Programmatic-check/evidence guidance remains metadata, not newly enforced logic.
Do not use a rule-only score as a safety certification or resolution percentage.
WebMCP and an external ASSERT skill are intentionally later access layers over
these same authoring APIs, not separate rule-generation or scoring engines.
