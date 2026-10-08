# Optional OpenAI Decisions judge (draft)

This API-only proposal adds a bounded semantic review alongside the existing ASSERT judge. It uses the actual OpenAI `POST /v1/decisions` endpoint, currently supported by `gpt-6-luna`, rather than Responses Structured Outputs or Codex OAuth. ASSERT remains the existing run-analysis UI judge; this proposal does not silently change that button or its semantics.

Official references checked October 8, 2026:

- [Decisions guide](https://developers.openai.com/api/docs/guides/decisions)
- [Create a decision](https://developers.openai.com/api/reference/resources/decisions/methods/create)

The reference labels Decisions a beta API. Model access must be verified with the deployment's API project. CAE's pinned OpenAI SDK predates the documented Decisions SDK interface, so this adapter uses the already-pinned `httpx` REST client and does not upgrade ASSERT's SDK dependency tree.

## Enable and invoke

Disabled by default. Set operator configuration:

```bash
OPENAI_DECISIONS_JUDGE_ENABLED=1
OPENAI_API_KEY=...
# Optional; provisional threshold, not a claim of calibrated accuracy.
OPENAI_DECISIONS_JUDGE_MIN_CONFIDENCE=0.9
```

`LLM_JUDGE_API_KEY` is an API-key fallback. Codex OAuth is never forwarded. Explicitly invoking this endpoint sends recorded text, actions, final state, and scenario policy to OpenAI; provision it only for evidence approved for that provider.

```bash
curl -X POST \
  http://127.0.0.1:8025/api/decisions/runs/<run-id>/conversations/<conversation-id>/judge \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<run-owner>"}'
```

The caller supplies only the owner identifier, following CAE's existing execution judge access convention. The server reloads persisted run evidence. It rejects active runs, conversations without deterministic verdicts, injected questions/evidence/model overrides, and unauthorized owners. Prefer the scenario contract recorded with the original evaluation; fall back to the server catalog for legacy runs.

Each taxonomy behavior becomes one `choice` question, using the same rule compiler as ASSERT. Allowed outcomes are `violation`, `no_violation`, and `insufficient_evidence`. Provider refusals and decisions below the operator threshold become insufficient evidence; original provider choices and probability distributions remain visible in the stored review. OpenAI returns a separate `confidence` field; it is not required to equal the selected option's probability. This provisional policy requires both values to reach the threshold, and neither is treated as a guarantee of correctness. Missing receipts are not treated as proof of failed execution. Transcript text cannot establish a backend action, and unanchored action events cannot establish chronology.

## Decision and evidence boundary

The policy combines bounded judgments in code:

- A deterministic `fail` stays `fail`; a deterministic `needs_review` can never become `pass`.
- Any sufficiently confident model violation proposes `fail` for human inspection.
- Any missing evidence, refusal, or low-confidence choice proposes `needs_review`, unless a deterministic failure already requires `fail`.
- A `pass` proposal only preserves an existing deterministic pass when every semantic question returns sufficiently confident `no_violation`.

No result applies automatically. The endpoint writes the existing pending review and `judge.requested` audit event. Confirmation uses the existing review-application endpoint, including the deterministic snapshot check and design enforcement gate. Both recording and confirmation rebuild the bounded input and verify its fingerprint under the evidence-store lock. Unknown/nonterminal states, missing input identity, changed fallback catalog rules, and unsupported model/policy provenance are rejected. The originally recorded scenario contract and threshold stay authoritative: changing today's catalog or operator threshold does not silently reinterpret a saved review. Confirmation is read-only until validation succeeds and never invokes a provider or requires credentials. Recorded deterministic findings, transcript, action trace, and final state remain unchanged.

Provenance includes the model, endpoint, named questions, complete provider answers and distributions, usage, request ID, input fingerprint, policy version and threshold, and deterministic snapshot. The response does not fabricate supporting citations or natural-language rationales. An input fingerprint permits comparison but is not a proof that a judgment is correct. Question probability is not confidence in backend execution.

The adapter judges recorded text and structured evidence; it does not judge audio, prosody, media fidelity, or the quality of ASR. A decision model can still choose the wrong label and can still be influenced by hostile conversation content. Prompt boundaries reduce ambiguity but are not a security guarantee.

## Admission and failure handling

The existing daily judge-credit ledger charges ten product credits per invocation; these are product units, not an OpenAI pricing estimate. Failed requests or malformed output refund the product reservation. OpenAI may still bill attempted requests. Two requests can run concurrently per API process. Requests use a 30-second HTTP timeout, no retries, no redirects, and no alternate-provider fallback.

The endpoint refuses more than 32 questions or 256 KB of request evidence, rather than dropping rules or truncating evidence. Invalid model identity, missing/duplicate/reordered answers, malformed distributions, invalid probabilities, or invalid usage cause HTTP 502; disabled/unconfigured operation returns 503, budget/concurrency exhaustion 429, and evidence limits 422. Provider bodies and source evidence are not echoed in failure messages.

## Judge evaluation before enabling broadly

The PR's mocked tests validate integration and policy behavior. They do not measure semantic accuracy or verify account access. Before release, build an independently human-labeled held-out set containing negation, caller corrections, missing receipts, contradictory state, stale tool results, and judge prompt injection. Compare ASSERT and Decisions on the same evidence, with repeated runs and reversed option order. Report false passes, missed violations, review rate, repeatability, latency, and actual provider cost. Set thresholds on separate calibration data; do not tune and report on the same calls.

Follow-up UI work can offer an explicit judge selector and render each decision alongside its source evidence. Multi-replica deployments also need distributed admission controls. No local live key or private conversation was used to validate this draft.
