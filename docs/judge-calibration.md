# Calibrating the semantic judge

The offline harness compares independent normalized labels with saved judge predictions. It does not invoke an LLM, generate its own reference answers or prove production accuracy from fixtures.

## Input contract

Labels are a JSON array of records:

```json
[{
  "case_id":"refund-001",
  "conversation_group":"original-conversation-001",
  "split":"held_out",
  "label_source":"human",
  "reviewer":"Reviewer A",
  "label_version":"refund-policy-v3-labels-v1",
  "labels":{"explanation":"pass","refund_execution":"not_observable"}
}]
```

Predictions are a separate JSON array:

```json
[{"case_id":"refund-001","outcomes":{"explanation":"pass","refund_execution":"not_observable"}}]
```

Both use stable check IDs. Label states are `pass`, `fail`, `not_observable`, and `not_applicable`; predictions additionally support `error` for evaluator failures. Missing predictions count as unresolved coverage, not agent failure. Map the saved ASSERT `judge_result.check_results` IDs to the same reviewed rubric IDs before comparison; do not infer operational success from a transcript.

```bash
apps/api/.venv/bin/python scripts/calibrate-judge.py \
  --labels reviewed-labels.json --predictions saved-predictions.json \
  --split held_out --require-human
```

The report includes per-check, pooled-check and overall-case false-pass, false-failure, false-verification, unresolved and decisive-agreement measures, together with counts and denominators. False-pass rate is passes among human-labelled failures; false-failure rate is failures among human-labelled passes. False verification counts claimed passes where the reference says the result was unobservable. Overall aggregation never averages away a failing check. A missing denominator is `null`, not a perfect score. Inspect coverage alongside agreement so abstention cannot inflate quality unnoticed.

## Human validation procedure

Select representative consented/redacted recordings from the intended product, including good outcomes and real failures. Freeze the policy, evidence and check definitions. Have knowledgeable reviewers label cases independently, reconcile disagreements and record reviewer/version provenance. Keep correlated turns, replays and paraphrases of the same original conversation in a single `conversation_group` and split; the harness rejects a group present in both calibration and held-out sets.

Tune prompts/models using only the calibration split. Freeze the chosen judge configuration, generate predictions independently, and measure the held-out split. Inspect false passes separately for safety-critical policies and publish uncertainty/coverage with any result. Re-run after policy, model, adapter or evidence changes. Avoid tuning against the held-out cases and then describing them as independent validation.

`docs/examples/judge-calibration-synthetic.json` contains **synthetic, provisional** examples of good/bad conversations, paraphrases, negation, legitimate fallback, missing/partial traces, explicit tool failure, unverified claims, prompt-injection-like text, and evaluator outages. These validate harness behavior only. `--require-human` rejects them. No expert-labelled production dataset or real-model accuracy result is supplied by this PR.
