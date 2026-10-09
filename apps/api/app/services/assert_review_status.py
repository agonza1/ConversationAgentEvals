"""One fail-closed freshness check for view, HTML export, and confirmed application."""
from __future__ import annotations

import re
from typing import Any
from app.services.evaluation_contract import recorded_contract
from app.services.execution_run_store import deterministic_evaluation_snapshot
from app.services.upstream_assert_judge import assert_judge_input_fingerprint, FINGERPRINT_VERSION, judge_configuration

TERMINAL = {'completed', 'needs_review', 'failed', 'cancelled', 'canceled'}


def is_assert_review(review: dict[str, Any]) -> bool:
    judge = review.get('judge_result')
    provenance = judge.get('provenance') if isinstance(judge, dict) else None
    return review.get('provider') == 'assert-ai' or (isinstance(provenance, dict) and provenance.get('engine') == 'assert')


def saved_assert_review_freshness(run: dict[str, Any], conversation: dict[str, Any],
                                 review: dict[str, Any], scenario_contract: dict[str, Any] | None = None) -> dict[str, str]:
    # The legacy positional argument remains for callers; the recorded snapshot is authoritative.
    def result(status: str, code: str, message: str) -> dict[str, str]:
        return {'status': status, 'reason_code': code, 'message': message}
    try:
        if run.get('status') not in TERMINAL or conversation.get('status') not in TERMINAL:
            return result('cannot_verify', 'not_terminal', 'The run and conversation must be terminal before checking this review.')
        current_snapshot = deterministic_evaluation_snapshot(conversation)
    except (ValueError, TypeError, AttributeError, KeyError):
        return result('cannot_verify', 'malformed_input', 'Recorded evidence cannot be verified.')
    if current_snapshot.get('verdict') not in {'pass', 'needs_review', 'fail', 'failed'}:
        return result('cannot_verify', 'missing_verdict', 'The conversation has no saved deterministic verdict.')
    snapshot = review.get('deterministic_snapshot')
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('evidence_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', snapshot['evidence_sha256']) or 'score' not in snapshot or 'verdict' not in snapshot:
        return result('cannot_verify', 'missing_snapshot', 'Saved evidence identity is unavailable for this legacy review.')
    if snapshot != current_snapshot:
        return result('stale', 'evidence_changed', 'Conversation evidence changed after this review. Run a new review explicitly.')
    judge = review.get('judge_result')
    provenance = judge.get('provenance') if isinstance(judge, dict) else None
    if not isinstance(provenance, dict) or provenance.get('engine') != 'assert' or provenance.get('judge_status') != 'ok':
        return result('cannot_verify', 'missing_provenance', 'Completed ASSERT input provenance is unavailable.')
    model, fingerprint, n = review.get('model'), provenance.get('input_fingerprint'), provenance.get('judge_n')
    if (provenance.get('fingerprint_version') != FINGERPRINT_VERSION or not isinstance(model, str) or not model
            or not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint)
            or type(n) is not int or not 1 <= n <= 3):
        return result('cannot_verify', 'missing_fingerprint', 'A complete versioned ASSERT input identity is required. Request a new review.')
    try:
        contract = recorded_contract(conversation)
        configuration = judge_configuration(model, n)
        if provenance.get('configuration') != configuration:
            return result('stale', 'grader_changed', 'The ASSERT runtime, rubric, or model settings changed. Explicitly request a new review.')
        fingerprint_now = assert_judge_input_fingerprint(run=run, conversation=conversation,
            scenario_contract=contract, model=model, judge_n=n)
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError):
        return result('cannot_verify', 'malformed_input', 'The retained contract or grading configuration cannot be verified. Explicitly re-evaluate the original evidence.')
    if fingerprint_now != fingerprint:
        return result('stale', 'judging_inputs_changed', 'Target or retained contract inputs changed after this review. Run a new review explicitly.')
    return result('current', 'inputs_match', 'Recorded evidence, frozen contract, target and grader settings match this saved review.')
