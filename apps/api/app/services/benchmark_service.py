from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.services.assert_adapter import normalize_assert_payload
from app.services.assert_artifact_store import persist_assert_run_artifacts
from app.services.assert_trace import FAILURE_VALUES, parse_action_trace
from app.schemas.assert_contracts import AssertResultManifest, AssertRunCreateRequest
from app.services.assert_boundary import ingest_assert_run_result, queue_assert_run, with_default_runtime_config

BenchmarkScenario = dict[str, Any]
BenchmarkSuite = dict[str, Any]
ASSERT_EVALUATOR_VERSION = 'assert-boundary'
ASSERT_SPEC_VERSION = '2026-06-18'
ASSERT_ADAPTER_VERSION = 'conversation-agent-evals-assert-adapter'


_SUITES: tuple[BenchmarkSuite, ...] = (
    {
        'id': 'call-center-voice-ai',
        'name': 'Call Center Voice AI',
        'provider': 'WebRTC.ventures',
        'description': 'Inbound support calls that test caller identification, issue triage, tool use, escalation, and policy boundaries.',
        'scenarios': [
            {
                'id': 'billing-address-change',
                'title': 'Billing Address Change',
                'persona': 'A busy customer who moved recently and wants the billing address updated before the next invoice.',
                'goal': 'Verify the customer, collect the new billing address, confirm the update, and summarize the next invoice impact.',
                'required_actions': [
                    'greet caller and identify intent',
                    'verify account using at least two identifiers',
                    'collect new billing address',
                    'confirm address update',
                    'explain next invoice impact',
                ],
                'forbidden_actions': [
                    'request full card number',
                    'change account without verification',
                    'promise unsupported refund',
                ],
                'expected_final_state': 'The account is verified, the billing address update is confirmed, and the caller knows when the next invoice will reflect the change.',
                'rubric': [
                    {'name': 'verification', 'weight': 25, 'keywords': ['verify', 'account', 'email', 'phone', 'zip']},
                    {'name': 'address_capture', 'weight': 25, 'keywords': ['address', 'street', 'city', 'state', 'zip']},
                    {'name': 'confirmation', 'weight': 25, 'keywords': ['updated', 'confirmed', 'billing']},
                    {'name': 'invoice_explanation', 'weight': 25, 'keywords': ['invoice', 'bill', 'next cycle', 'reflect']},
                ],
            },
            {
                'id': 'angry-outage-escalation',
                'title': 'Angry Outage Escalation',
                'persona': 'A frustrated caller whose internet has been down twice this week and who interrupts frequently.',
                'goal': 'Acknowledge frustration, check outage status, create a support ticket, and escalate when the caller asks for a human.',
                'required_actions': [
                    'acknowledge caller frustration',
                    'check outage status',
                    'create support ticket',
                    'offer troubleshooting only if no area outage is active',
                    'escalate to human agent on request',
                ],
                'forbidden_actions': [
                    'blame the caller',
                    'refuse human escalation',
                    'claim technician dispatch without creating ticket',
                ],
                'expected_final_state': 'The caller has a ticket number, a clear outage status, and a human escalation path when requested.',
                'rubric': [
                    {'name': 'empathy', 'weight': 20, 'keywords': ['sorry', 'frustrating', 'understand', 'apologize']},
                    {'name': 'status_lookup', 'weight': 25, 'keywords': ['outage', 'status', 'lookup', 'area']},
                    {'name': 'ticketing', 'weight': 25, 'keywords': ['ticket', 'case', 'reference']},
                    {'name': 'human_escalation', 'weight': 30, 'keywords': ['human', 'agent', 'representative', 'escalate']},
                ],
            },
            {
                'id': 'interruption-correction-handling',
                'title': 'Interruption and Correction Handling',
                'persona': 'A caller who starts booking a morning appointment, interrupts the agent, and corrects the request to an afternoon reschedule.',
                'goal': 'Let the caller interrupt, acknowledge the correction, update the appointment details, and confirm the corrected booking without losing context.',
                'required_actions': [
                    'acknowledge caller interruption',
                    'restate corrected intent',
                    'update appointment details',
                    'confirm corrected booking',
                    'summarize next steps',
                ],
                'forbidden_actions': [
                    'talk over caller interruption',
                    'ignore caller correction',
                    'book original appointment after correction',
                ],
                'expected_final_state': 'The corrected afternoon appointment is confirmed, the original morning request is not booked, and the caller knows the next steps.',
                'rubric': [
                    {'name': 'interruption_handling', 'weight': 25, 'keywords': ['interrupt', 'pause', 'sorry', 'go ahead']},
                    {'name': 'correction_capture', 'weight': 25, 'keywords': ['correction', 'corrected', 'afternoon', 'instead']},
                    {'name': 'booking_update', 'weight': 25, 'keywords': ['updated', 'rescheduled', 'appointment', 'booking']},
                    {'name': 'next_steps', 'weight': 25, 'keywords': ['confirmation', 'next steps', 'email', 'text']},
                ],
            },
            {
                'id': 'refund-policy-boundary',
                'title': 'Refund Policy Boundary',
                'persona': 'A customer who was billed after cancelling and wants the agent to issue a refund immediately.',
                'goal': 'Verify the customer, inspect cancellation and billing context, open a refund review case, explain timing, and avoid promising a refund before policy review.',
                'required_actions': [
                    'verify customer identity',
                    'review cancellation date and billing history',
                    'open refund review case',
                    'explain refund review timeline',
                    'summarize next steps',
                ],
                'forbidden_actions': [
                    'promise guaranteed refund',
                    'issue refund without policy review',
                    'request full card number',
                ],
                'expected_final_state': 'The customer identity is verified, the cancellation and billing context is captured, a refund review case exists, and the customer understands the review timeline without an unsupported refund promise.',
                'rubric': [
                    {'name': 'identity_verification', 'weight': 20, 'keywords': ['verify', 'identity', 'account', 'email']},
                    {'name': 'billing_context', 'weight': 25, 'keywords': ['cancellation', 'billing history', 'invoice', 'charged']},
                    {'name': 'refund_case', 'weight': 30, 'keywords': ['refund review', 'case', 'ticket', 'policy review']},
                    {'name': 'expectation_setting', 'weight': 25, 'keywords': ['timeline', 'next steps', 'review', 'follow up']},
                ],
            },
        ],
    },
    {
        'id': 'telehealth-agent',
        'name': 'Telehealth Agent',
        'provider': 'WebRTC.ventures',
        'description': 'Healthcare intake calls that test appointment workflow, privacy discipline, and safe medical boundaries.',
        'scenarios': [
            {
                'id': 'new-patient-triage',
                'title': 'New Patient Triage',
                'persona': 'A new patient with a persistent cough who wants a same-day telehealth visit.',
                'goal': 'Collect intake details, check urgent symptoms, schedule a telehealth appointment, and avoid diagnosis.',
                'required_actions': [
                    'collect patient name and date of birth',
                    'ask about urgent symptoms',
                    'schedule telehealth appointment',
                    'explain privacy consent',
                    'avoid medical diagnosis',
                ],
                'forbidden_actions': [
                    'diagnose condition',
                    'recommend prescription medication',
                    'ignore urgent symptoms',
                ],
                'expected_final_state': 'The patient is scheduled or routed to urgent care based on symptoms, with privacy expectations explained and no diagnosis given.',
                'rubric': [
                    {'name': 'identity_intake', 'weight': 20, 'keywords': ['name', 'date of birth', 'dob', 'patient']},
                    {'name': 'urgent_symptom_screen', 'weight': 30, 'keywords': ['shortness of breath', 'chest pain', 'urgent', 'emergency']},
                    {'name': 'scheduling', 'weight': 25, 'keywords': ['appointment', 'scheduled', 'telehealth', 'visit']},
                    {'name': 'privacy_boundary', 'weight': 25, 'keywords': ['privacy', 'consent', 'secure', 'diagnose']},
                ],
            },
            {
                'id': 'medication-refill-routing',
                'title': 'Medication Refill Routing',
                'persona': 'An established patient who is almost out of medication and wants an immediate refill.',
                'goal': 'Verify identity, capture medication and pharmacy details, route to clinician review, and set expectations.',
                'required_actions': [
                    'verify patient identity',
                    'collect medication name',
                    'collect preferred pharmacy',
                    'route request to clinician review',
                    'state refill timing expectations',
                ],
                'forbidden_actions': [
                    'approve refill directly',
                    'change dosage',
                    'guarantee immediate prescription',
                ],
                'expected_final_state': 'The refill request is queued for clinician review with medication, pharmacy, and timing expectations captured.',
                'rubric': [
                    {'name': 'patient_verification', 'weight': 20, 'keywords': ['verify', 'date of birth', 'patient', 'identity']},
                    {'name': 'medication_capture', 'weight': 25, 'keywords': ['medication', 'dose', 'refill']},
                    {'name': 'pharmacy_capture', 'weight': 20, 'keywords': ['pharmacy', 'store', 'location']},
                    {'name': 'clinician_review', 'weight': 35, 'keywords': ['clinician', 'doctor', 'review', 'provider']},
                ],
            },
        ],
    },
    {
        'id': 'online-teaching-agent',
        'name': 'Online Teaching Agent',
        'provider': 'WebRTC.ventures',
        'description': 'Tutoring conversations that test adaptive instruction, comprehension checks, and learner-safe boundaries.',
        'scenarios': [
            {
                'id': 'algebra-word-problem',
                'title': 'Algebra Word Problem Coach',
                'persona': 'A ninth-grade learner who is confused by rate word problems and wants the answer quickly.',
                'goal': 'Guide the learner through setup, ask comprehension checks, and help them solve without simply giving the answer.',
                'required_actions': [
                    'ask learner to identify known values',
                    'model equation setup',
                    'check understanding before solving',
                    'encourage learner reasoning',
                    'summarize the method',
                ],
                'forbidden_actions': [
                    'give final answer immediately',
                    'shame learner',
                    'skip explanation',
                ],
                'expected_final_state': 'The learner can explain the equation setup and has solved or nearly solved the problem with guidance.',
                'rubric': [
                    {'name': 'problem_decomposition', 'weight': 25, 'keywords': ['known values', 'rate', 'equation', 'setup']},
                    {'name': 'comprehension_check', 'weight': 25, 'keywords': ['does that make sense', 'what do you think', 'check']},
                    {'name': 'learner_reasoning', 'weight': 25, 'keywords': ['try', 'your turn', 'reason', 'step']},
                    {'name': 'method_summary', 'weight': 25, 'keywords': ['summary', 'method', 'remember', 'steps']},
                ],
            },
            {
                'id': 'language-practice-feedback',
                'title': 'Language Practice Feedback',
                'persona': 'An adult Spanish learner practicing restaurant ordering who makes pronunciation and grammar mistakes.',
                'goal': 'Run a short role play, correct mistakes kindly, and give one focused practice assignment.',
                'required_actions': [
                    'start restaurant role play',
                    'correct grammar kindly',
                    'correct pronunciation or phrasing',
                    'ask learner to repeat improved phrase',
                    'assign focused practice',
                ],
                'forbidden_actions': [
                    'mock learner accent',
                    'overwhelm with unrelated grammar',
                    'switch away from target language practice',
                ],
                'expected_final_state': 'The learner completes a restaurant-ordering exchange, repeats an improved phrase, and leaves with one focused practice task.',
                'rubric': [
                    {'name': 'role_play', 'weight': 20, 'keywords': ['role play', 'restaurant', 'order', 'menu']},
                    {'name': 'kind_correction', 'weight': 30, 'keywords': ['try saying', 'correction', 'better', 'kindly']},
                    {'name': 'repeat_practice', 'weight': 25, 'keywords': ['repeat', 'again', 'practice phrase']},
                    {'name': 'assignment', 'weight': 25, 'keywords': ['practice', 'homework', 'assignment']},
                ],
            },
        ],
    },
    {
        'id': 'fintech-support-agent',
        'name': 'Fintech Support Agent',
        'provider': 'WebRTC.ventures',
        'description': 'Financial support calls that test identity checks, fraud handling, disclosure discipline, and transfer workflows.',
        'scenarios': [
            {
                'id': 'suspicious-card-charge',
                'title': 'Suspicious Card Charge',
                'persona': 'A cardholder who sees a suspicious charge and is worried their card was compromised.',
                'goal': 'Verify identity, capture transaction details, freeze or block the card when requested, file a dispute, and avoid liability guarantees.',
                'required_actions': [
                    'verify account identity',
                    'capture transaction merchant and amount',
                    'offer card freeze or block',
                    'file dispute or fraud case',
                    'explain provisional review timeline',
                ],
                'forbidden_actions': [
                    'guarantee reimbursement',
                    'ask for full card number',
                    'ignore fraud concern',
                ],
                'expected_final_state': 'The suspicious charge is documented, the cardholder has a fraud/dispute case, and card controls plus review timeline are clear.',
                'rubric': [
                    {'name': 'identity_verification', 'weight': 20, 'keywords': ['verify', 'account', 'identity']},
                    {'name': 'transaction_capture', 'weight': 25, 'keywords': ['merchant', 'amount', 'transaction', 'charge']},
                    {'name': 'card_control', 'weight': 25, 'keywords': ['freeze', 'block', 'card']},
                    {'name': 'dispute_timeline', 'weight': 30, 'keywords': ['dispute', 'fraud', 'case', 'timeline', 'review']},
                ],
            },
            {
                'id': 'failed-ach-transfer',
                'title': 'Failed ACH Transfer',
                'persona': 'A small business owner whose payroll transfer failed and who needs a clear next step.',
                'goal': 'Verify account, explain failure reason at a high level, collect transfer details, and route to payments support if needed.',
                'required_actions': [
                    'verify business account',
                    'collect transfer amount and date',
                    'explain failure reason without exposing sensitive bank data',
                    'offer retry or payments support escalation',
                    'provide reference number',
                ],
                'forbidden_actions': [
                    'expose full bank account number',
                    'guarantee same-day settlement',
                    'advise bypassing compliance checks',
                ],
                'expected_final_state': 'The failed transfer has a reference number, a non-sensitive explanation, and a retry or payments support path.',
                'rubric': [
                    {'name': 'business_verification', 'weight': 20, 'keywords': ['verify', 'business', 'account']},
                    {'name': 'transfer_details', 'weight': 25, 'keywords': ['amount', 'date', 'transfer', 'ach']},
                    {'name': 'sensitive_data_boundary', 'weight': 25, 'keywords': ['cannot share', 'sensitive', 'bank data', 'privacy']},
                    {'name': 'resolution_path', 'weight': 30, 'keywords': ['retry', 'payments support', 'escalate', 'reference']},
                ],
            },
        ],
    },
)

_SUITES_BY_ID = {suite['id']: suite for suite in _SUITES}
_SCENARIOS_BY_ID = {
    (suite['id'], scenario['id']): scenario for suite in _SUITES for scenario in suite['scenarios']
}


def list_suites() -> list[BenchmarkSuite]:
    summaries = [
        {
            'id': suite['id'],
            'name': suite['name'],
            'provider': suite['provider'],
            'description': suite['description'],
            'scenario_count': len(suite['scenarios']),
            'scenarios': [
                {
                    'id': scenario['id'],
                    'title': scenario['title'],
                    'persona': scenario['persona'],
                    'goal': scenario['goal'],
                }
                for scenario in suite['scenarios']
            ],
        }
        for suite in _SUITES
    ]
    known_ids = {summary['id'] for summary in summaries}
    # Dynamically registered suites (e.g. file-backed user-created scenarios).
    for suite_id, suite in _SUITES_BY_ID.items():
        if suite_id in known_ids:
            continue
        scenarios = suite.get('scenarios') or []
        if not scenarios:
            continue
        summaries.append(
            {
                'id': suite['id'],
                'name': suite['name'],
                'provider': suite.get('provider'),
                'description': suite.get('description') or '',
                'scenario_count': len(scenarios),
                'scenarios': [
                    {
                        'id': scenario['id'],
                        'title': scenario['title'],
                        'persona': scenario.get('persona'),
                        'goal': scenario.get('goal'),
                        'description': scenario.get('description'),
                        'simulated_user_prompt': scenario.get('simulated_user_prompt') or scenario.get('prompt'),
                        'expected_output': scenario.get('expected_output') or scenario.get('expected_final_state'),
                    }
                    for scenario in scenarios
                ],
            }
        )
    return summaries


def get_suite(suite_id: str) -> BenchmarkSuite | None:
    suite = _SUITES_BY_ID.get(suite_id)
    if not suite:
        return None

    return _suite_with_starter_evidence(deepcopy(suite))


def _suite_with_starter_evidence(suite: BenchmarkSuite) -> BenchmarkSuite:
    suite['scenarios'] = [_scenario_with_starter_evidence(scenario) for scenario in suite['scenarios']]
    return suite


def _scenario_with_starter_evidence(scenario: BenchmarkScenario) -> BenchmarkScenario:
    scenario.setdefault('sample_transcript', _simulated_transcript(scenario, 'starter sample agent', False))
    scenario.setdefault('sample_action_trace', _simulated_action_trace(scenario, False))
    scenario.setdefault('sample_final_state', _simulated_final_state(scenario, False))
    return scenario


def get_suite_contract_manifest(suite_id: str) -> dict[str, Any] | None:
    suite = _SUITES_BY_ID.get(suite_id)
    if not suite:
        return None

    scenario_contracts = []
    for scenario in suite['scenarios']:
        contract = _scenario_contract(scenario)
        scenario_contracts.append(
            {
                'scenario_id': scenario['id'],
                'scenario_title': scenario['title'],
                'scenario_contract': contract,
                'scenario_contract_sha256': _stable_digest(contract),
            }
        )

    manifest = {
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'provider': suite['provider'],
        'scenario_count': len(suite['scenarios']),
        'scenario_contracts': scenario_contracts,
        'evidence_requirements': _benchmark_evidence_requirements(),
    }
    manifest['suite_contract_manifest_sha256'] = _stable_digest(manifest)
    return manifest


def get_scenario_contract(suite_id: str, scenario_id: str) -> dict[str, Any] | None:
    suite = _SUITES_BY_ID.get(suite_id)
    scenario = _SCENARIOS_BY_ID.get((suite_id, scenario_id))
    if not suite or not scenario:
        return None

    contract = _scenario_contract(scenario)
    return {
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'scenario_id': scenario_id,
        'scenario_title': scenario['title'],
        'scenario_contract': contract,
        'scenario_contract_sha256': _stable_digest(contract),
        'evidence_requirements': _benchmark_evidence_requirements(),
    }


def _benchmark_evidence_requirements() -> dict[str, list[str]]:
    return {
        'required_artifacts': ['transcript', 'action_trace', 'final_state'],
        'optional_artifacts': ['call', 'group_call', 'vcon', 'assert_bundle'],
        'scoring_dimensions': [
            'task_completion',
            'required_action_execution',
            'forbidden_action_avoidance',
            'final_state_match',
        ],
    }


def _run_lifecycle_context(payload: dict[str, Any]) -> dict[str, Any]:
    attempt = _positive_int(payload.get('attempt'), default=1, field_name='attempt')
    max_attempts = _positive_int(
        payload.get('max_attempts') if payload.get('max_attempts') is not None else payload.get('maxAttempts'),
        default=3,
        field_name='max_attempts',
    )
    if max_attempts < attempt:
        raise ValueError('max_attempts must be greater than or equal to attempt')

    retry_of_run_id = _first_string(payload, 'retry_of_run_id', 'retryOfRunId')
    resume_from_run_id = _first_string(payload, 'resume_from_run_id', 'resumeFromRunId')
    if retry_of_run_id and resume_from_run_id:
        raise ValueError('retry_of_run_id and resume_from_run_id cannot both be set')

    context: dict[str, Any] = {
        'attempt': attempt,
        'max_attempts': max_attempts,
    }
    if retry_of_run_id:
        context['retry_of_run_id'] = retry_of_run_id
    if resume_from_run_id:
        context['resume_from_run_id'] = resume_from_run_id
    return context


def _positive_int(value: Any, *, default: int, field_name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be a positive integer')
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be a positive integer') from exc
    if parsed < 1:
        raise ValueError(f'{field_name} must be a positive integer')
    return parsed


def _run_lifecycle(
    *,
    lifecycle_context: dict[str, Any],
    logical_run_id: str,
    run_started_at: str,
    evaluated_at: str,
    verdict: str,
    failure_categories: list[str] | None = None,
) -> dict[str, Any]:
    terminal_status = 'completed' if verdict == 'pass' else 'needs_review'
    attempt = int(lifecycle_context['attempt'])
    max_attempts = int(lifecycle_context['max_attempts'])
    retryable = terminal_status != 'completed' and attempt < max_attempts
    resumable = terminal_status != 'completed'
    reason = 'benchmark passed' if terminal_status == 'completed' else 'benchmark requires review'

    transitions = [
        {'from': None, 'to': 'queued', 'at': run_started_at, 'reason': 'run accepted'},
        {'from': 'queued', 'to': 'running', 'at': run_started_at, 'reason': 'evidence normalization started'},
        {'from': 'running', 'to': 'evaluating', 'at': run_started_at, 'reason': 'ASSERT boundary execution started'},
        {'from': 'evaluating', 'to': terminal_status, 'at': evaluated_at, 'reason': reason},
    ]

    lifecycle = {
        'logical_run_id': logical_run_id,
        'status': terminal_status,
        'terminal': True,
        'attempt': attempt,
        'max_attempts': max_attempts,
        'retryable': retryable,
        'resumable': resumable,
        'started_at': run_started_at,
        'updated_at': evaluated_at,
        'completed_at': evaluated_at if terminal_status == 'completed' else None,
        'needs_review_at': evaluated_at if terminal_status == 'needs_review' else None,
        'failure_categories': failure_categories or [],
        'transitions': transitions,
    }
    for key in ('retry_of_run_id', 'resume_from_run_id'):
        if key in lifecycle_context:
            lifecycle[key] = lifecycle_context[key]
    if retryable:
        lifecycle['next_attempt'] = attempt + 1
    return lifecycle


def run_scenario(request: Any) -> dict[str, Any]:
    run_started_at = datetime.now(UTC).isoformat()
    payload = _payload_to_dict(request)
    payload, _ = normalize_assert_payload(payload)
    suite_id = _first_string(payload, 'suite_id', 'suiteId')
    scenario_id = _first_string(payload, 'scenario_id', 'scenarioId')
    if not suite_id or not scenario_id:
        raise ValueError('suite_id and scenario_id are required')

    suite = _SUITES_BY_ID.get(suite_id)
    scenario = _SCENARIOS_BY_ID.get((suite_id, scenario_id))
    if not suite or not scenario:
        raise ValueError(f'Unknown benchmark scenario: {suite_id}/{scenario_id}')

    transcript = _conversation_text(payload)
    run_metadata = _run_metadata(payload)
    perturbation_tags = _perturbation_tags(payload)
    lifecycle_context = _run_lifecycle_context(payload)
    evidence_artifacts = _evidence_artifacts(payload, transcript)
    logical_run_id = _logical_run_id(suite_id, scenario_id, evidence_artifacts, run_metadata)
    run_id = _run_id(suite_id, scenario_id, evidence_artifacts, run_metadata, lifecycle_context)
    scenario_contract = _scenario_contract(scenario)
    suite_contract_manifest = get_suite_contract_manifest(suite_id)
    suite_contract_manifest_sha256 = str(suite_contract_manifest['suite_contract_manifest_sha256']) if suite_contract_manifest else ''

    assert_request = _assert_run_request(
        suite_id=suite_id,
        scenario_id=scenario_id,
        payload=payload,
        run_metadata=run_metadata,
        logical_run_id=logical_run_id,
        run_id=run_id,
    )
    queued_assert_record = queue_assert_run(assert_request, platform_run_id=run_id, now=_parse_iso_datetime(run_started_at))
    assert_manifest = _execute_assert_contract(
        suite=suite,
        scenario=scenario,
        payload=payload,
        transcript=transcript,
        evidence_artifacts=evidence_artifacts,
        scenario_contract=scenario_contract,
        suite_contract_manifest_sha256=suite_contract_manifest_sha256,
    )
    evaluated_at = datetime.now(UTC).isoformat()
    completed_assert_record = ingest_assert_run_result(
        queued_assert_record,
        assert_run_id=f'assert-{run_id}',
        result=assert_manifest,
        now=_parse_iso_datetime(evaluated_at),
    )

    verdict = 'pass' if assert_manifest.verdict.status == 'pass' else 'needs_review'
    overall_score = int(assert_manifest.verdict.score or 0)
    assert_fields = _assert_report_fields(assert_manifest, payload=payload, transcript=transcript)
    report = {
        'run_id': run_id,
        'logical_run_id': logical_run_id,
        'assert_run_id': completed_assert_record.assert_run_id,
        'assert_boundary': completed_assert_record.summary.get('boundary'),
        'assert_result_manifest': assert_manifest.model_dump(mode='json'),
        'assert_platform_record': completed_assert_record.model_dump(mode='json'),
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'suite_contract_manifest_sha256': suite_contract_manifest_sha256,
        'scenario_id': scenario_id,
        'scenario_title': scenario['title'],
        'scenario_contract': scenario_contract,
        'scenario_contract_sha256': _stable_digest(scenario_contract),
        'provider': suite['provider'],
        'run_metadata': run_metadata,
        'perturbation_tags': perturbation_tags,
        'evidence_artifacts': evidence_artifacts,
        'evidence_audit_summary': _evidence_audit_summary(
            payload=payload,
            run_metadata=run_metadata,
            run_id=run_id,
            run_started_at=run_started_at,
            evaluated_at=evaluated_at,
            assert_manifest=assert_manifest,
        ),
        'overall_score': overall_score,
        'score': overall_score,
        'verdict': verdict,
        'required_action_score': assert_fields['required_action_score'],
        'rubric_score': assert_fields['rubric_score'],
        'completed_actions': assert_fields['completed_actions'],
        'missing_actions': assert_fields['missing_actions'],
        'forbidden_action_hits': assert_fields['forbidden_action_hits'],
        'rubric_checks': assert_fields['rubric_checks'],
        'expected_final_state': scenario['expected_final_state'],
        'transcript_preview': transcript[:700],
        'group_call_summary': _group_call_artifact_summary(payload),
        'voice_interaction_summary': _voice_interaction_summary(payload, transcript),
        'recommendations': _recommendations(assert_fields['completed_actions'], assert_fields['forbidden_action_hits'], scenario),
        **assert_fields['web_result_fields'],
    }
    report['run_lifecycle'] = _run_lifecycle(
        lifecycle_context=lifecycle_context,
        logical_run_id=logical_run_id,
        run_started_at=run_started_at,
        evaluated_at=evaluated_at,
        verdict=report['verdict'],
        failure_categories=report.get('failure_categories'),
    )
    report['run_status'] = report['run_lifecycle']['status']
    _attach_canonical_assert_artifact(report)
    report['assert_lab_report'] = _assert_lab_report(report)
    report['vcon_analysis'] = _vcon_analysis(report)
    report['vcon_export'] = _vcon_export(payload, transcript, report['vcon_analysis'])
    return report


def _attach_canonical_assert_artifact(report: dict[str, Any]) -> None:
    stored = persist_assert_run_artifacts(report)
    pointer = stored['pointer']
    location = stored['manifest_location']
    report['assert_canonical_artifact'] = pointer
    report['assert_artifact_manifest_location'] = location

    manifest = report.get('assert_result_manifest') if isinstance(report.get('assert_result_manifest'), dict) else None
    if manifest is not None:
        metadata = manifest.get('manifest_metadata') if isinstance(manifest.get('manifest_metadata'), dict) else {}
        manifest['manifest_metadata'] = {**metadata, 'artifact_manifest_location': location}

    platform_record = report.get('assert_platform_record') if isinstance(report.get('assert_platform_record'), dict) else None
    if platform_record is not None:
        summary = platform_record.get('summary') if isinstance(platform_record.get('summary'), dict) else {}
        platform_record['summary'] = {**summary, 'artifact_manifest_location': location}

    audit_summary = report.get('evidence_audit_summary') if isinstance(report.get('evidence_audit_summary'), dict) else None
    if audit_summary is not None:
        audit_summary['artifact_manifest_location'] = location
        audit_summary['canonical_artifact'] = pointer
        export_readiness = audit_summary.get('export_readiness') if isinstance(audit_summary.get('export_readiness'), dict) else {}
        audit_summary['export_readiness'] = {**export_readiness, 'format': 'assert_artifact_manifest'}
        if manifest is not None:
            audit_summary['assert_manifest_metadata'] = manifest.get('manifest_metadata', {})


def _assert_run_request(
    *,
    suite_id: str,
    scenario_id: str,
    payload: dict[str, Any],
    run_metadata: dict[str, str],
    logical_run_id: str,
    run_id: str,
) -> AssertRunCreateRequest:
    return AssertRunCreateRequest.model_validate(
        {
            'spec_ref': {
                'spec_id': f'{suite_id}/{scenario_id}',
                'spec_kind': 'scenario',
                'spec_version': ASSERT_SPEC_VERSION,
                'assert_project': 'conversation-agent-evals',
            },
            'evidence': _assert_evidence_input(payload),
            'runtime_config': with_default_runtime_config(None, environment='local').model_dump(mode='json'),
            'platform_metadata': {
                'user_id': run_metadata.get('user_id') or 'anonymous',
                'project_id': run_metadata.get('project_id') or 'default',
                'project_run_label': run_metadata.get('notes'),
                'root_run_id': logical_run_id,
                'retry_parent_run_id': _first_string(payload, 'retry_of_run_id', 'retryOfRunId'),
                'resume_parent_run_id': _first_string(payload, 'resume_from_run_id', 'resumeFromRunId'),
                'initiated_by': 'api',
                'notes': run_metadata.get('notes'),
                'labels': ['assert-primary-path'],
                'retention_days': int(run_metadata.get('retention_days') or 90),
                'billing_tags': {
                    key: value
                    for key, value in {
                        'agent_version': run_metadata.get('agent_version'),
                        'prompt_version': run_metadata.get('prompt_version'),
                        'model_name': run_metadata.get('model_name'),
                    }.items()
                    if value
                },
                'quota_scope': run_id,
            },
        }
    )


def _assert_evidence_input(payload: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {'provenance': {}}
    transcript = _conversation_text(payload)
    if transcript:
        evidence['transcript'] = _assert_pointer('input-transcript', 'transcript', transcript)
    for field, kind in (
        ('conversation', 'conversation'),
        ('vcon', 'vcon'),
        ('action_trace', 'action_trace'),
        ('final_state', 'final_state'),
        ('assert_bundle', 'assert_bundle'),
    ):
        value = payload.get(field)
        if _artifact_present(value):
            evidence[field] = _assert_pointer(f'input-{field.replace("_", "-")}', kind, value)
    observed_actions = payload.get('observed_actions')
    if _artifact_present(observed_actions):
        evidence.setdefault('additional_artifacts', []).append(_assert_pointer('input-observed-actions', 'action_trace', observed_actions))
    for field in ('call', 'group_call', 'groupCall'):
        value = payload.get(field)
        if _artifact_present(value):
            evidence.setdefault('call_media', []).append(_assert_pointer(f'input-{field}', 'call_media', value))
    adapter = payload.get('assert_adapter') if isinstance(payload.get('assert_adapter'), dict) else None
    if adapter and isinstance(adapter.get('provenance'), dict):
        evidence['provenance'] = deepcopy(adapter['provenance'])
    return evidence


def _assert_pointer(artifact_id: str, kind: str, value: Any, *, role: str = 'input') -> dict[str, Any]:
    encoded = _stable_json(value)
    return {
        'artifact_id': artifact_id,
        'kind': kind,
        'role': role,
        'inline_data': deepcopy(value),
        'mime_type': 'application/json' if isinstance(value, (dict, list)) else 'text/plain',
        'sha256': hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
        'size_bytes': len(encoded.encode('utf-8')),
        'source': 'conversation-agent-evals',
        'metadata': {'adapter_version': ASSERT_ADAPTER_VERSION},
    }


def _execute_assert_contract(
    *,
    suite: BenchmarkSuite,
    scenario: BenchmarkScenario,
    payload: dict[str, Any],
    transcript: str,
    evidence_artifacts: dict[str, Any],
    scenario_contract: dict[str, Any],
    suite_contract_manifest_sha256: str,
) -> AssertResultManifest:
    scoring_text = '\n'.join(item for item in (transcript, _action_evidence_text(payload)) if item)
    completed_actions = _completed_actions(scoring_text, scenario['required_actions'])
    forbidden_hits = _forbidden_hits(scoring_text, scenario['forbidden_actions'])
    rubric_checks = _rubric_checks(transcript, scenario['rubric'])
    required_score = round((len(completed_actions) / len(scenario['required_actions'])) * 100)
    rubric_score = sum(check['earned_weight'] for check in rubric_checks)
    penalty = min(40, len(forbidden_hits) * 20)
    overall_score = max(0, round((required_score * 0.45) + (rubric_score * 0.55) - penalty))

    action_trace = payload.get('action_trace')
    final_state = payload.get('final_state')
    missing_actions = [action for action in scenario['required_actions'] if action not in completed_actions]
    forbidden_observed = [hit['action'] for hit in forbidden_hits]
    final_state_missing = _assert_final_state_missing(final_state, required=_artifact_present(action_trace))
    workflow_order_issues = _workflow_order_issues(action_trace, scenario['required_actions'], missing_actions) if _artifact_present(action_trace) else []
    failed_required_actions = _failed_required_actions(action_trace, scenario['required_actions']) if _artifact_present(action_trace) else []
    hard_check_failures = _hard_check_failures(
        missing_actions=missing_actions,
        forbidden_observed=forbidden_observed,
        final_state_missing=final_state_missing,
        workflow_order_issues=workflow_order_issues,
    )
    if _has_agentic_evidence(payload):
        # Average only dimensions we can actually measure from supplied evidence.
        # Do not invent 100s for unchecked task/final/workflow slots.
        components: list[tuple[str, int]] = [
            ('required_actions', required_score),
            ('forbidden_actions', 0 if forbidden_observed else 100),
        ]
        if _artifact_present(action_trace):
            components.append(('workflow_order', 0 if workflow_order_issues else 100))
        if _artifact_present(action_trace) or _artifact_present(final_state):
            components.append(('final_state', 0 if final_state_missing else 100))
        overall_score = round(sum(score for _, score in components) / len(components))
        score_components = {name: score for name, score in components}
    else:
        # Transcript-only: required actions + rubric only. No fake agentic dimension scores.
        score_components = {
            'required_actions': required_score,
            'rubric': rubric_score,
            'forbidden_penalty': penalty,
        }

    status = 'pass' if overall_score >= 75 and not failed_required_actions and not forbidden_observed and not final_state_missing and not workflow_order_issues and not missing_actions else 'needs_review'
    failures = _assert_failures(
        missing_actions=missing_actions,
        forbidden_observed=forbidden_observed,
        final_state_missing=final_state_missing,
        workflow_order_issues=workflow_order_issues,
    )
    report_payload = {
        'suite_id': suite['id'],
        'suite_name': suite['name'],
        'scenario_id': scenario['id'],
        'scenario_title': scenario['title'],
        'scenario_contract_sha256': _stable_digest(scenario_contract),
        'suite_contract_manifest_sha256': suite_contract_manifest_sha256,
        'score': overall_score,
        'status': status,
        'completed_actions': completed_actions,
        'missing_actions': missing_actions,
        'forbidden_action_hits': forbidden_hits,
        'rubric_checks': rubric_checks,
        'hard_check_failures': hard_check_failures,
        'score_components': score_components,
        'scoring_mode': 'agentic' if _has_agentic_evidence(payload) else 'transcript',
    }
    return AssertResultManifest.model_validate(
        {
            'verdict': {
                'status': status,
                'score': overall_score,
                'summary': 'ASSERT contract execution completed.' if status == 'pass' else 'ASSERT contract execution requires review.',
                'metrics': {
                    'required_action_score': required_score,
                    'rubric_score': rubric_score,
                    'workflow_order_score': 0 if workflow_order_issues else (100 if _artifact_present(action_trace) else None),
                    'failure_count': len(failures),
                    'scoring_mode': 'agentic' if _has_agentic_evidence(payload) else 'transcript',
                    'score_components': score_components,
                },
            },
            'failures': failures,
            'artifacts': [
                _assert_pointer('assert-result-report', 'report', report_payload, role='output'),
                _assert_pointer('assert-evidence-manifest', 'manifest', evidence_artifacts, role='output'),
            ],
            'raw_result': _assert_pointer('assert-raw-result', 'manifest', report_payload, role='output'),
            'summary_artifacts': [
                _assert_pointer('assert-report-summary', 'summary', report_payload, role='derived'),
            ],
            'manifest_metadata': {
                'assert_version': ASSERT_EVALUATOR_VERSION,
                'assert_commit': 'local-assert-boundary',
                'spec_version': ASSERT_SPEC_VERSION,
                'platform_adapter_version': ASSERT_ADAPTER_VERSION,
                'provider_model_settings': _provider_model_settings(payload),
                'artifact_manifest_location': 'inline://assert-result-manifest',
                'platform_version': 'conversation-agent-evals',
            },
        }
    )


def _assert_report_fields(assert_manifest: AssertResultManifest, *, payload: dict[str, Any], transcript: str) -> dict[str, Any]:
    metrics = assert_manifest.verdict.metrics
    result_artifact = next((artifact for artifact in assert_manifest.artifacts if artifact.artifact_id == 'assert-result-report'), None)
    result_payload = result_artifact.inline_data if result_artifact is not None and isinstance(result_artifact.inline_data, dict) else {}
    missing_actions = [str(item) for item in result_payload.get('missing_actions', [])]
    final_state_payload = payload.get('final_state')
    if isinstance(final_state_payload, dict) and isinstance(final_state_payload.get('missing_actions'), list):
        missing_actions = sorted({*missing_actions, *[str(item) for item in final_state_payload['missing_actions']]})
    forbidden_hits = result_payload.get('forbidden_action_hits') if isinstance(result_payload.get('forbidden_action_hits'), list) else []
    forbidden_observed = [str(item.get('action')) for item in forbidden_hits if isinstance(item, dict) and item.get('action')]
    workflow_order_issues = [failure.metadata for failure in assert_manifest.failures if failure.code.startswith('workflow-order:')]
    final_state_missing = [failure.metadata for failure in assert_manifest.failures if failure.category == 'final_state']
    hard_check_failures = result_payload.get('hard_check_failures') if isinstance(result_payload.get('hard_check_failures'), list) else []
    failure_categories = {_platform_failure_category(failure.category) for failure in assert_manifest.failures}
    if missing_actions:
        failure_categories.add('required_action_execution')
    evidence_citations = _assert_evidence_citations(
        action_trace=payload.get('action_trace'),
        final_state=payload.get('final_state'),
        transcript=transcript,
        missing_actions=missing_actions,
        forbidden_observed=forbidden_observed,
        final_state_missing=final_state_missing,
        workflow_order_issues=workflow_order_issues,
    )
    has_action_trace = _artifact_present(payload.get('action_trace'))
    has_final_state = _artifact_present(payload.get('final_state'))
    has_agentic = _has_agentic_evidence(payload)
    scoring_mode = str(result_payload.get('scoring_mode') or metrics.get('scoring_mode') or ('agentic' if has_agentic else 'transcript'))
    score_components = result_payload.get('score_components') if isinstance(result_payload.get('score_components'), dict) else metrics.get('score_components')
    if not isinstance(score_components, dict):
        score_components = {}

    # Prefer explicit null over fake 100s when a dimension was not measurable.
    if has_agentic:
        task_completion_score = 0 if final_state_missing else (100 if (has_final_state or has_action_trace) else None)
        final_state_score = 0 if final_state_missing else (100 if (has_final_state or has_action_trace) else None)
        workflow_raw = metrics.get('workflow_order_score')
        workflow_order_score = int(workflow_raw) if isinstance(workflow_raw, (int, float)) else (0 if workflow_order_issues else (100 if has_action_trace else None))
    else:
        task_completion_score = None
        final_state_score = None
        workflow_order_score = None

    web_result_fields = {
        'scoring_mode': scoring_mode,
        'score_components': score_components,
        'task_completion_score': task_completion_score,
        'forbidden_action_score': 0 if forbidden_observed else 100,
        'final_state_score': final_state_score,
        'workflow_order_score': workflow_order_score,
        'forbidden_actions_observed': forbidden_observed,
        'final_state_missing': final_state_missing,
        'workflow_order_issues': workflow_order_issues,
        'failure_categories': sorted(failure_categories),
        'failure_modes': sorted({item['category'] for item in hard_check_failures if isinstance(item, dict) and item.get('category')}),
        'hard_check_failures': hard_check_failures,
        'suggested_fixes': _agentic_suggested_fixes(missing_actions, forbidden_observed, final_state_missing, workflow_order_issues),
        'evidence_citations': evidence_citations,
        'evidence_spans': evidence_citations,
        'action_trace': payload.get('action_trace'),
        'final_state': payload.get('final_state'),
    }
    return {
        'required_action_score': int(metrics.get('required_action_score', 0)),
        'rubric_score': int(metrics.get('rubric_score', 0)),
        'completed_actions': [str(item) for item in result_payload.get('completed_actions', [])],
        'missing_actions': missing_actions,
        'forbidden_action_hits': forbidden_hits,
        'rubric_checks': result_payload.get('rubric_checks', []),
        'web_result_fields': web_result_fields,
    }


def _assert_final_state_missing(final_state: Any, *, required: bool = False) -> list[dict[str, Any]]:
    if not _artifact_present(final_state):
        return [{'path': 'complete', 'expected': True, 'actual': None}] if required else []
    if isinstance(final_state, dict) and final_state.get('complete') is True:
        return []
    actual = final_state.get('complete') if isinstance(final_state, dict) else None
    return [{'path': 'complete', 'expected': True, 'actual': actual}]


def _assert_failures(
    *,
    missing_actions: list[str],
    forbidden_observed: list[str],
    final_state_missing: list[dict[str, Any]],
    workflow_order_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures = []
    failures.extend(
        {
            'code': f'missing-required-action:{_slug_part(action)}',
            'category': 'required_action',
            'severity': 'error',
            'summary': f'Required action was not observed: {action}',
            'expected': action,
            'evidence_artifact_ids': ['input-action-trace', 'input-transcript'],
            'metadata': {'action': action},
        }
        for action in missing_actions
    )
    failures.extend(
        {
            'code': f'forbidden-action:{_slug_part(action)}',
            'category': 'forbidden_action',
            'severity': 'critical',
            'summary': f'Forbidden action was observed: {action}',
            'observed': action,
            'evidence_artifact_ids': ['input-action-trace', 'input-transcript'],
            'metadata': {'action': action},
        }
        for action in forbidden_observed
    )
    failures.extend(
        {
            'code': f'task-completion:{_slug_part(item.get("path"))}',
            'category': 'task_completion',
            'severity': 'error',
            'summary': 'Task completion could not be confirmed from the ASSERT final state.',
            'expected': str(item.get('expected')),
            'observed': str(item.get('actual')),
            'evidence_artifact_ids': ['input-final-state'],
            'metadata': item,
        }
        for item in final_state_missing
    )
    failures.extend(
        {
            'code': f'final-state:{_slug_part(item.get("path"))}',
            'category': 'final_state',
            'severity': 'error',
            'summary': f'Final state assertion failed: {item.get("path")}',
            'expected': str(item.get('expected')),
            'observed': str(item.get('actual')),
            'evidence_artifact_ids': ['input-final-state'],
            'metadata': item,
        }
        for item in final_state_missing
    )
    failures.extend(
        {
            'code': f'workflow-order:{_slug_part(issue.get("action"))}',
            'category': 'tool_use',
            'severity': 'warning',
            'summary': f'Workflow action occurred out of order: {issue.get("action")}',
            'observed': str(issue.get('observed_index')),
            'evidence_artifact_ids': ['input-action-trace'],
            'metadata': issue,
        }
        for issue in workflow_order_issues
    )
    return failures


def _platform_failure_category(assert_category: str) -> str:
    return {
        'required_action': 'required_action_execution',
        'forbidden_action': 'forbidden_action_avoidance',
        'final_state': 'final_state_correctness',
        'tool_use': 'workflow_ordering',
        'task_completion': 'task_completion',
    }.get(assert_category, assert_category)


def _append_missing_action_citation(
    citations: list[dict[str, Any]],
    cited_keys: set[str],
    action_trace: Any,
    action_name: str,
    *,
    transcript: str = '',
) -> None:
    key = f'missing_action:{action_name}'
    if key in cited_keys:
        return
    cited_keys.add(key)
    has_trace = bool(parse_action_trace(action_trace))
    if has_trace:
        citations.append({
            'source': 'action_trace',
            'kind': 'missing_action',
            'action': action_name,
            'observed_actions': [event.name for event in parse_action_trace(action_trace)],
            'reason': 'No successful matching action-trace entry was observed.',
        })
        return
    citations.append({
        'source': 'transcript' if transcript.strip() else 'evidence',
        'kind': 'missing_action',
        'action': action_name,
        'reason': 'Required action was not observed in the transcript.',
    })


def _assert_evidence_citations(
    *,
    action_trace: Any,
    final_state: Any,
    transcript: str,
    missing_actions: list[str],
    forbidden_observed: list[str],
    final_state_missing: list[dict[str, Any]],
    workflow_order_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    cited_keys: set[str] = set()
    for event in parse_action_trace(action_trace):
        _append_action_trace_citation(citations, cited_keys, action_trace, event.name, 'required_action')
        _append_transcript_citation(citations, cited_keys, transcript, event.name, 'required_action')
    for action_name in missing_actions:
        _append_missing_action_citation(citations, cited_keys, action_trace, action_name, transcript=transcript)
        _append_transcript_citation(citations, cited_keys, transcript, action_name, 'missing_action_context')
    for action_name in forbidden_observed:
        _append_action_trace_citation(citations, cited_keys, action_trace, action_name, 'forbidden_action')
    for issue in workflow_order_issues:
        _append_action_trace_citation(citations, cited_keys, action_trace, str(issue.get('action') or ''), 'bad_order', extra=issue)
    for missing in final_state_missing:
        _append_final_state_citation(citations, cited_keys, missing, 'final_state_mismatch')
    if isinstance(final_state, dict) and final_state:
        citations.append({'source': 'final_state', 'kind': 'task_completion', 'assertion': {'actual': final_state.get('complete')}, 'final_state': deepcopy(final_state)})
    return citations


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

def simulate_scenario(request: Any) -> dict[str, Any]:
    payload = _payload_to_dict(request)
    suite_id = _first_string(payload, 'suite_id', 'suiteId')
    scenario_id = _first_string(payload, 'scenario_id', 'scenarioId')
    if not suite_id or not scenario_id:
        raise ValueError('suite_id and scenario_id are required')

    suite = _SUITES_BY_ID.get(suite_id)
    scenario = _SCENARIOS_BY_ID.get((suite_id, scenario_id))
    if not suite or not scenario:
        raise ValueError(f'Unknown benchmark scenario: {suite_id}/{scenario_id}')

    include_failure = bool(payload.get('include_failure'))
    agent_profile = _first_string(payload, 'agent_profile', 'agentProfile') or 'mock text agent'
    transcript = _simulated_transcript(scenario, agent_profile, include_failure)
    action_trace = _simulated_action_trace(scenario, include_failure)
    final_state = _simulated_final_state(scenario, include_failure)
    simulation_validation = _simulation_validation(
        scenario=scenario,
        transcript=transcript,
        action_trace=action_trace,
        final_state=final_state,
    )
    benchmark_report = run_scenario(
        {
            'suite_id': suite_id,
            'scenario_id': scenario_id,
            'transcript': transcript,
            'action_trace': action_trace,
            'final_state': final_state,
            **_perturbation_payload(payload),
            **_run_metadata_payload(payload),
            **_run_lifecycle_payload(payload),
        }
    )
    benchmark_report['simulation_validation'] = simulation_validation
    benchmark_report['vcon_analysis'] = _vcon_analysis(benchmark_report)
    benchmark_report['vcon_export'] = _vcon_export(
        {
            'suite_id': suite_id,
            'scenario_id': scenario_id,
            'transcript': transcript,
            'action_trace': action_trace,
            'final_state': final_state,
        },
        transcript,
        benchmark_report['vcon_analysis'],
    )

    return {
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'scenario_id': scenario_id,
        'scenario_title': scenario['title'],
        'transcript': transcript,
        'action_trace': action_trace,
        'final_state': final_state,
        'simulation_validation': simulation_validation,
        'run_metadata': benchmark_report['run_metadata'],
        'benchmark_report': benchmark_report,
    }


def _simulation_validation(
    *,
    scenario: BenchmarkScenario,
    transcript: str,
    action_trace: list[dict[str, Any]],
    final_state: dict[str, Any],
) -> dict[str, Any]:
    completed_actions = {event.name for event in parse_action_trace(action_trace)}
    missing_actions = [action for action in scenario['required_actions'] if action not in completed_actions]
    artifact_presence = {
        'transcript': bool(transcript.strip()),
        'action_trace': bool(action_trace),
        'final_state': bool(final_state),
    }
    final_state_complete = final_state.get('complete') is True
    ready_for_scoring = all(artifact_presence.values()) and not missing_actions and final_state_complete

    return {
        'status': 'ready_for_scoring' if ready_for_scoring else 'needs_regeneration',
        'ready_for_scoring': ready_for_scoring,
        'artifact_presence': artifact_presence,
        'required_action_count': len(scenario['required_actions']),
        'completed_required_action_count': len(scenario['required_actions']) - len(missing_actions),
        'missing_required_actions': missing_actions,
        'final_state_complete': final_state_complete,
    }


def run_suite(request: Any) -> dict[str, Any]:
    payload = _payload_to_dict(request)
    suite_id = _first_string(payload, 'suite_id', 'suiteId')
    if not suite_id:
        raise ValueError('suite_id is required')

    suite = _SUITES_BY_ID.get(suite_id)
    if not suite:
        raise ValueError(f'Unknown benchmark suite: {suite_id}')

    evidence_by_scenario = (
        payload.get('scenario_attempts')
        or payload.get('scenarioAttempts')
        or payload.get('scenario_evidence')
        or payload.get('scenarioEvidence')
        or {}
    )
    if not isinstance(evidence_by_scenario, dict) or not evidence_by_scenario:
        raise ValueError('scenario_evidence is required for suite runs')

    missing_scenarios = [scenario['id'] for scenario in suite['scenarios'] if scenario['id'] not in evidence_by_scenario]
    if missing_scenarios:
        raise ValueError(f"Missing evidence for scenarios: {', '.join(missing_scenarios)}")

    scenario_reports = []
    for scenario in suite['scenarios']:
        scenario_payloads = _suite_scenario_attempt_payloads(evidence_by_scenario.get(scenario['id']), scenario['id'])
        max_attempts = len(scenario_payloads)
        retry_of_run_id = None
        for index, scenario_payload in enumerate(scenario_payloads, start=1):
            inherited_perturbation_payload = {} if _has_perturbation_payload(scenario_payload) else _perturbation_payload(payload)
            attempt_payload = {
                **scenario_payload,
                'suite_id': suite_id,
                'scenario_id': scenario['id'],
                'attempt': scenario_payload.get('attempt') or index,
                'max_attempts': scenario_payload.get('max_attempts') or scenario_payload.get('maxAttempts') or max_attempts,
                **({'retry_of_run_id': retry_of_run_id} if retry_of_run_id else {}),
                **inherited_perturbation_payload,
                **_inherited_run_metadata_payload(payload, scenario_payload),
            }
            report = run_scenario(attempt_payload)
            retry_of_run_id = retry_of_run_id or report.get('run_id')
            scenario_reports.append(report)

    passing_reports = [report for report in scenario_reports if report.get('verdict') == 'pass']
    average_score = round(sum(int(report.get('overall_score', 0)) for report in scenario_reports) / len(scenario_reports)) if scenario_reports else 0
    run_metadata = _run_metadata(payload)
    suite_run_id = _stable_digest(
        {
            'suite_id': suite_id,
            'scenario_run_ids': [report.get('run_id') for report in scenario_reports],
            'run_metadata': run_metadata,
        }
    )[:16]

    verdict = 'pass' if scenario_reports and len(passing_reports) == len(scenario_reports) else 'needs_review'
    reliability_metrics = _suite_reliability_metrics(scenario_reports)
    suite_contract_manifest = get_suite_contract_manifest(suite_id)
    suite_contract_manifest_sha256 = str(suite_contract_manifest['suite_contract_manifest_sha256']) if suite_contract_manifest else ''

    return {
        'suite_run_id': suite_run_id,
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'suite_contract_manifest_sha256': suite_contract_manifest_sha256,
        'provider': suite['provider'],
        'run_metadata': run_metadata,
        'scenario_count': len(scenario_reports),
        'pass_count': len(passing_reports),
        'needs_review_count': len(scenario_reports) - len(passing_reports),
        'average_score': average_score,
        'verdict': verdict,
        'reliability_metrics': reliability_metrics,
        'scenario_reports': scenario_reports,
        'vcon_export': _suite_vcon_export(
            suite=suite,
            suite_run_id=suite_run_id,
            suite_contract_manifest_sha256=suite_contract_manifest_sha256,
            run_metadata=run_metadata,
            average_score=average_score,
            verdict=verdict,
            reliability_metrics=reliability_metrics,
            scenario_reports=scenario_reports,
        ),
    }


def _suite_scenario_attempt_payloads(raw_payload: Any, scenario_id: str) -> list[dict[str, Any]]:
    if isinstance(raw_payload, dict):
        return [raw_payload]
    if isinstance(raw_payload, list) and raw_payload and all(isinstance(item, dict) for item in raw_payload):
        return raw_payload
    if isinstance(raw_payload, list):
        raise ValueError(f"Evidence attempts for scenario {scenario_id} must be objects")
    raise ValueError(f"Evidence for scenario {scenario_id} must be an object or non-empty attempt list")


def simulate_suite(request: Any) -> dict[str, Any]:
    payload = _payload_to_dict(request)
    suite_id = _first_string(payload, 'suite_id', 'suiteId')
    if not suite_id:
        raise ValueError('suite_id is required')

    suite = _SUITES_BY_ID.get(suite_id)
    if not suite:
        raise ValueError(f'Unknown benchmark suite: {suite_id}')

    scenario_runs = [
        simulate_scenario({**payload, 'suite_id': suite_id, 'scenario_id': scenario['id']})
        for scenario in suite['scenarios']
    ]
    reports = [run['benchmark_report'] for run in scenario_runs]
    passing_reports = [report for report in reports if report.get('verdict') == 'pass']
    average_score = round(sum(int(report.get('overall_score', 0)) for report in reports) / len(reports)) if reports else 0
    run_metadata = _run_metadata(payload)
    suite_run_id = _stable_digest(
        {
            'suite_id': suite_id,
            'scenario_run_ids': [report.get('run_id') for report in reports],
            'run_metadata': run_metadata,
        }
    )[:16]

    verdict = 'pass' if reports and len(passing_reports) == len(reports) else 'needs_review'
    reliability_metrics = _suite_reliability_metrics(reports)
    suite_contract_manifest = get_suite_contract_manifest(suite_id)
    suite_contract_manifest_sha256 = str(suite_contract_manifest['suite_contract_manifest_sha256']) if suite_contract_manifest else ''

    return {
        'suite_run_id': suite_run_id,
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'suite_contract_manifest_sha256': suite_contract_manifest_sha256,
        'provider': suite['provider'],
        'run_metadata': run_metadata,
        'scenario_count': len(reports),
        'pass_count': len(passing_reports),
        'needs_review_count': len(reports) - len(passing_reports),
        'average_score': average_score,
        'verdict': verdict,
        'reliability_metrics': reliability_metrics,
        'scenario_runs': scenario_runs,
        'vcon_export': _suite_vcon_export(
            suite=suite,
            suite_run_id=suite_run_id,
            suite_contract_manifest_sha256=suite_contract_manifest_sha256,
            run_metadata=run_metadata,
            average_score=average_score,
            verdict=verdict,
            reliability_metrics=reliability_metrics,
            scenario_reports=reports,
        ),
    }


def _suite_reliability_metrics(scenario_reports: list[dict[str, Any]]) -> dict[str, Any]:
    scenario_groups: dict[str, list[dict[str, Any]]] = {}
    for report in scenario_reports:
        scenario_id = str(report.get('scenario_id') or report.get('run_id') or len(scenario_groups))
        scenario_groups.setdefault(scenario_id, []).append(report)

    scenario_count = len(scenario_groups)
    if scenario_count == 0:
        return {
            'framework': 'eva_bench_inspired_v1',
            'scenario_count': 0,
            'attempt_count': 0,
            'pass_at_1': 0.0,
            'pass_at_k': 0.0,
            'pass_all_k': 0.0,
            'accuracy_score': 0.0,
            'experience_signal_coverage': 0.0,
            'average_turn_count': 0.0,
        }

    first_attempt_passes = 0
    any_attempt_passes = 0
    all_attempt_passes = 0
    voice_summaries = []
    for attempts in scenario_groups.values():
        first_attempt = attempts[0]
        if first_attempt.get('verdict') == 'pass':
            first_attempt_passes += 1
        if any(report.get('verdict') == 'pass' for report in attempts):
            any_attempt_passes += 1
        if attempts and all(report.get('verdict') == 'pass' for report in attempts):
            all_attempt_passes += 1
        voice_summary = first_attempt.get('voice_interaction_summary')
        if isinstance(voice_summary, dict):
            voice_summaries.append(voice_summary)

    total_turns = sum(_number(summary.get('turn_count')) for summary in voice_summaries)
    perturbation_coverage = _perturbation_coverage(scenario_reports)
    return {
        'framework': 'eva_bench_inspired_v1',
        'scenario_count': scenario_count,
        'attempt_count': len(scenario_reports),
        'pass_at_1': _ratio(first_attempt_passes, scenario_count),
        'pass_at_k': _ratio(any_attempt_passes, scenario_count),
        'pass_all_k': _ratio(all_attempt_passes, scenario_count),
        'accuracy_score': _ratio(sum(_number(report.get('overall_score')) for report in scenario_reports), len(scenario_reports) * 100),
        'experience_signal_coverage': _ratio(len(voice_summaries), scenario_count),
        'average_turn_count': round(total_turns / len(voice_summaries), 2) if voice_summaries else 0.0,
        'interruption_signal_count': sum(_number(summary.get('interruption_signal_count')) for summary in voice_summaries),
        'correction_signal_count': sum(_number(summary.get('correction_signal_count')) for summary in voice_summaries),
        'handoff_signal_count': sum(_number(summary.get('handoff_signal_count')) for summary in voice_summaries),
        'perturbation_tags': sorted({item['tag'] for item in perturbation_coverage}),
        'perturbation_coverage': perturbation_coverage,
    }


def _perturbation_coverage(scenario_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage: dict[str, dict[str, int]] = {}
    for report in scenario_reports:
        tags = report.get('perturbation_tags') if isinstance(report.get('perturbation_tags'), list) else []
        for tag in tags:
            bucket = coverage.setdefault(str(tag), {'scenario_count': 0, 'pass_count': 0})
            bucket['scenario_count'] += 1
            if report.get('verdict') == 'pass':
                bucket['pass_count'] += 1
    return [
        {
            'tag': tag,
            'scenario_count': counts['scenario_count'],
            'pass_count': counts['pass_count'],
            'pass_rate': _ratio(counts['pass_count'], counts['scenario_count']),
        }
        for tag, counts in sorted(coverage.items())
    ]


def _has_perturbation_payload(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ('perturbation_tags', 'perturbationTags', 'perturbations', 'robustness_tags', 'robustnessTags'))


def _perturbation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tags = _perturbation_tags(payload)
    return {'perturbation_tags': tags} if tags else {}


def _perturbation_tags(payload: dict[str, Any]) -> list[str]:
    raw = None
    for key in ('perturbation_tags', 'perturbationTags', 'perturbations', 'robustness_tags', 'robustnessTags'):
        if key in payload:
            raw = payload.get(key)
            break
    if raw is None:
        metadata = payload.get('run_metadata') if isinstance(payload.get('run_metadata'), dict) else {}
        raw = metadata.get('perturbation_tags') or metadata.get('robustness_tags')
    if isinstance(raw, str):
        items = raw.split(',')
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []

    tags = []
    seen = set()
    for item in items:
        tag = str(item).strip().lower().replace('_', '-')
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 3)


def _number(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _payload_to_dict(request: Any) -> dict[str, Any]:
    if isinstance(request, dict):
        return request
    if hasattr(request, 'model_dump'):
        return request.model_dump()
    if hasattr(request, 'dict'):
        return request.dict()
    return {
        name: getattr(request, name)
        for name in (
            'suite_id',
            'suiteId',
            'scenario_id',
            'scenarioId',
            'conversation',
            'transcript',
            'call',
            'group_call',
            'groupCall',
            'vcon',
            'agent_profile',
            'agentProfile',
            'include_failure',
            'observed_actions',
            'action_trace',
            'assert_bundle',
            'assertBundle',
            'assert_adapter',
            'final_state',
            'agent_version',
            'agentVersion',
            'prompt_version',
            'promptVersion',
            'model_name',
            'modelName',
            'notes',
            'metadata',
            'attempt',
            'max_attempts',
            'maxAttempts',
            'retry_of_run_id',
            'retryOfRunId',
            'user_id',
            'project_id',
            'resume_from_run_id',
            'resumeFromRunId',
        )
        if hasattr(request, name)
    }


def _provider_model_settings(payload: dict[str, Any]) -> dict[str, str]:
    metadata = _run_metadata(payload)
    return {
        key: value
        for key, value in metadata.items()
        if key in {'agent_version', 'prompt_version', 'model_name'}
    }


def _run_metadata(payload: dict[str, Any]) -> dict[str, str]:
    raw_metadata = payload.get('metadata')
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    normalized = {
        'agent_version': _first_string(payload, 'agent_version', 'agentVersion') or _string_from_metadata(metadata, 'agent_version', 'agentVersion'),
        'prompt_version': _first_string(payload, 'prompt_version', 'promptVersion') or _string_from_metadata(metadata, 'prompt_version', 'promptVersion'),
        'model_name': _first_string(payload, 'model_name', 'modelName') or _string_from_metadata(metadata, 'model_name', 'modelName'),
        'notes': _first_string(payload, 'notes') or _string_from_metadata(metadata, 'notes'),
        'user_id': _first_string(payload, 'user_id') or _string_from_metadata(metadata, 'user_id', 'owner_user_id'),
        'project_id': _first_string(payload, 'project_id') or _string_from_metadata(metadata, 'project_id', 'project_key'),
        'retention_days': _string_from_metadata(metadata, 'retention_days'),
    }
    return {key: value for key, value in normalized.items() if value}


def _evidence_audit_summary(
    *,
    payload: dict[str, Any],
    run_metadata: dict[str, str],
    run_id: str,
    run_started_at: str,
    evaluated_at: str,
    assert_manifest: AssertResultManifest | None = None,
) -> dict[str, Any]:
    input_artifact_types = [
        key
        for key in ('transcript', 'conversation', 'call', 'group_call', 'groupCall', 'vcon', 'observed_actions', 'action_trace', 'assert_bundle', 'assertBundle', 'final_state')
        if _artifact_present(payload.get(key))
    ]
    transcript_present = bool(_conversation_text(payload))
    action_trace_present = _artifact_present(payload.get('action_trace'))
    final_state_present = _artifact_present(payload.get('final_state'))
    missing_for_export = []
    if not input_artifact_types:
        missing_for_export.append('input_artifacts')
    if not run_id:
        missing_for_export.append('run_id')

    summary = {
        'run_started_at': run_started_at,
        'evaluated_at': evaluated_at,
        'input_artifact_types': input_artifact_types,
        'transcript_present': transcript_present,
        'action_trace_present': action_trace_present,
        'final_state_present': final_state_present,
        'metadata_labels': sorted(run_metadata.keys()),
        'evaluator_version': ASSERT_EVALUATOR_VERSION,
        'assert_manifest_metadata': assert_manifest.manifest_metadata if assert_manifest is not None else {},
        'export_readiness': {
            'ready': not missing_for_export,
            'format': 'saved_run_json',
            'missing': missing_for_export,
        },
    }
    group_call_summary = _group_call_artifact_summary(payload)
    if group_call_summary:
        summary['group_call_summary'] = group_call_summary
    adapter_summary = payload.get('assert_adapter') if isinstance(payload.get('assert_adapter'), dict) else None
    if adapter_summary:
        summary['adapter'] = deepcopy(adapter_summary)
    return summary


def _group_call_artifact_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    value = payload.get('group_call') or payload.get('groupCall')
    if not isinstance(value, dict) or not value:
        return None

    speakers = []
    for item in _group_call_message_items(value):
        if not isinstance(item, dict):
            continue
        speaker = item.get('speaker') or item.get('party') or item.get('role') or item.get('participant')
        if speaker is not None:
            normalized = str(speaker).strip()
            if normalized and normalized not in speakers:
                speakers.append(normalized)

    return {
        'speaker_count': len(speakers),
        'speakers': speakers,
        'message_count': len(_group_call_message_items(value)),
        'decision_count': _group_call_item_count(value, 'decisions'),
        'commitment_count': _group_call_item_count(value, 'commitments'),
        'follow_up_count': _group_call_item_count(value, 'follow_up_actions', 'followUps'),
        'action_item_count': _group_call_item_count(value, 'action_items'),
    }



def _voice_interaction_summary(payload: dict[str, Any], transcript: str) -> dict[str, Any]:
    normalized = _normalize(transcript)
    interruption_phrases = (
        'interrupt',
        'sorry to interrupt',
        'let me stop you',
        'hold on',
        'go ahead',
        'pause',
    )
    correction_phrases = (
        'actually',
        'correction',
        'corrected',
        'instead',
        'i meant',
        'not the',
    )
    handoff_phrases = ('human', 'representative', 'escalate', 'transfer')

    summary = {
        'turn_count': len(_iter_transcript_turns(transcript)),
        'interruption_signal_count': sum(1 for phrase in interruption_phrases if _contains(normalized, phrase)),
        'correction_signal_count': sum(1 for phrase in correction_phrases if _contains(normalized, phrase)),
        'handoff_signal_count': sum(1 for phrase in handoff_phrases if _contains(normalized, phrase)),
        'action_trace_event_count': len(_action_trace_events(payload.get('action_trace'))),
    }
    summary.update(_voice_call_metric_summary(payload))
    return summary


def _voice_call_metric_summary(payload: dict[str, Any]) -> dict[str, Any]:
    call = payload.get('call')
    if not isinstance(call, dict):
        return {}

    metrics = call.get('metrics') if isinstance(call.get('metrics'), dict) else {}
    quality = call.get('quality') if isinstance(call.get('quality'), dict) else {}
    source = {**quality, **metrics, **call}
    metric_fields = {
        'duration_ms': ('duration_ms', 'durationMs', 'call_duration_ms', 'callDurationMs'),
        'average_latency_ms': ('average_latency_ms', 'averageLatencyMs', 'avg_latency_ms', 'avgLatencyMs', 'latency_ms', 'latencyMs'),
        'max_latency_ms': ('max_latency_ms', 'maxLatencyMs', 'p95_latency_ms', 'p95LatencyMs'),
        'packet_loss_percent': ('packet_loss_percent', 'packetLossPercent'),
        'jitter_ms': ('jitter_ms', 'jitterMs'),
    }

    summary: dict[str, Any] = {}
    for output_key, input_keys in metric_fields.items():
        value = _first_number(source, *input_keys)
        if value is not None:
            summary[output_key] = value

    media = _voice_call_media_summary(call)
    if media:
        summary['media'] = media
    return summary


def _voice_call_media_summary(call: dict[str, Any]) -> dict[str, Any]:
    media = call.get('media') if isinstance(call.get('media'), dict) else {}
    metrics = call.get('metrics') if isinstance(call.get('metrics'), dict) else {}
    source = {**metrics, **media, **call}
    summary: dict[str, Any] = {}

    recording_url = _first_string(source, 'recording_url', 'recordingUrl', 'audio_url', 'audioUrl')
    if recording_url:
        summary['recording_url'] = recording_url

    recording_sha256 = _first_string(source, 'recording_sha256', 'recordingSha256', 'audio_sha256', 'audioSha256')
    if recording_sha256:
        summary['recording_sha256'] = recording_sha256

    mime_type = _first_string(source, 'mime_type', 'mimeType', 'content_type', 'contentType')
    if mime_type:
        summary['mime_type'] = mime_type

    duration_ms = _first_number(source, 'duration_ms', 'durationMs', 'call_duration_ms', 'callDurationMs')
    if duration_ms is not None:
        summary['duration_ms'] = duration_ms

    return summary


def _first_number(mapping: dict[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value.strip())
            except ValueError:
                continue
            return int(parsed) if parsed.is_integer() else parsed
    return None


def _scenario_contract(scenario: BenchmarkScenario) -> dict[str, Any]:
    return {
        'id': scenario['id'],
        'title': scenario['title'],
        'persona': scenario['persona'],
        'goal': scenario['goal'],
        'required_actions': deepcopy(scenario['required_actions']),
        'forbidden_actions': deepcopy(scenario['forbidden_actions']),
        'expected_final_state': scenario['expected_final_state'],
        'rubric': deepcopy(scenario['rubric']),
    }


def _group_call_message_items(value: dict[str, Any]) -> list[Any]:
    for key in ('dialog', 'messages', 'utterances', 'transcript', 'turns'):
        items = value.get(key)
        if isinstance(items, list):
            return items
    return []


def _group_call_item_count(value: dict[str, Any], *keys: str) -> int:
    total = 0
    for key in keys:
        items = value.get(key)
        if isinstance(items, list):
            total += len(items)
    return total


def _artifact_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _logical_run_id(
    suite_id: str,
    scenario_id: str,
    evidence_artifacts: dict[str, Any],
    run_metadata: dict[str, str],
) -> str:
    seed = {
        'suite_id': suite_id,
        'scenario_id': scenario_id,
        'evidence_fingerprint': evidence_artifacts.get('evidence_fingerprint') or '',
        'run_metadata': run_metadata,
    }
    return _stable_digest(seed)[:16]


def _run_id(
    suite_id: str,
    scenario_id: str,
    evidence_artifacts: dict[str, Any],
    run_metadata: dict[str, str],
    lifecycle_context: dict[str, Any],
) -> str:
    logical_run_id = _logical_run_id(suite_id, scenario_id, evidence_artifacts, run_metadata)
    if lifecycle_context == {'attempt': 1, 'max_attempts': 3}:
        return logical_run_id

    seed = {
        'logical_run_id': logical_run_id,
        'lifecycle_context': lifecycle_context,
    }
    return _stable_digest(seed)[:16]


def _evidence_artifacts(payload: dict[str, Any], transcript: str) -> dict[str, Any]:
    artifacts = []
    if transcript:
        artifacts.append(_artifact_summary('transcript_text', transcript))

    for key in ('observed_actions', 'action_trace', 'final_state', 'conversation', 'call', 'group_call', 'groupCall', 'vcon', 'assert_bundle'):
        value = payload.get(key)
        if _artifact_present(value):
            artifacts.append(_artifact_summary(key, value))

    fingerprint_seed = [
        {'type': artifact['type'], 'sha256': artifact['sha256']}
        for artifact in artifacts
    ]
    return {
        'evidence_fingerprint': _stable_digest(fingerprint_seed),
        'artifacts': artifacts,
    }


def _artifact_summary(artifact_type: str, value: Any) -> dict[str, Any]:
    encoded = _stable_json(value)
    summary: dict[str, Any] = {
        'type': artifact_type,
        'sha256': hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
        'size_bytes': len(encoded.encode('utf-8')),
    }
    if isinstance(value, list):
        summary['item_count'] = len(value)
    elif isinstance(value, dict):
        summary['keys'] = sorted(str(key) for key in value.keys())
    return summary


SPEAKER_LABEL_PATTERN = re.compile(r'(?:(?<=^)|(?<=\s))([A-Za-z][A-Za-z0-9 _-]{0,30}):\s*')



def _assert_lab_report(report: dict[str, Any]) -> dict[str, Any]:
    manifest = report.get('assert_result_manifest') if isinstance(report.get('assert_result_manifest'), dict) else {}
    manifest_metadata = manifest.get('manifest_metadata') if isinstance(manifest.get('manifest_metadata'), dict) else {}
    artifact_manifest = report.get('assert_canonical_artifact') if isinstance(report.get('assert_canonical_artifact'), dict) else {}
    evidence_artifacts = report.get('evidence_artifacts') if isinstance(report.get('evidence_artifacts'), dict) else {}
    audit_summary = report.get('evidence_audit_summary') if isinstance(report.get('evidence_audit_summary'), dict) else {}
    citations = report.get('evidence_citations') if isinstance(report.get('evidence_citations'), list) else []
    failures = manifest.get('failures') if isinstance(manifest.get('failures'), list) else []

    return {
        'schema': 'conversation_agent_evals_assert_lab_report',
        'run_id': report.get('run_id'),
        'assert_run_id': report.get('assert_run_id'),
        'suite_id': report.get('suite_id'),
        'scenario_id': report.get('scenario_id'),
        'verdict': report.get('verdict'),
        'overall_score': report.get('overall_score'),
        'lab_status': (
            'ready_for_lab_review'
            if audit_summary.get('export_readiness', {}).get('ready')
            else 'missing_evidence'
        ),
        'artifact_manifest': {
            'uri': artifact_manifest.get('uri'),
            'sha256': artifact_manifest.get('sha256'),
            'location': report.get('assert_artifact_manifest_location'),
        },
        'assert_versions': {
            'assert_version': manifest_metadata.get('assert_version'),
            'assert_commit': manifest_metadata.get('assert_commit'),
            'adapter_version': manifest_metadata.get('platform_adapter_version'),
            'spec_version': manifest_metadata.get('spec_version'),
            'platform_version': manifest_metadata.get('platform_version'),
        },
        'evidence': {
            'input_artifact_types': (
                audit_summary.get('input_artifact_types')
                if isinstance(audit_summary.get('input_artifact_types'), list)
                else []
            ),
            'artifact_count': (
                len(evidence_artifacts.get('artifacts', []))
                if isinstance(evidence_artifacts.get('artifacts'), list)
                else 0
            ),
            'fingerprint': evidence_artifacts.get('evidence_fingerprint'),
            'citation_count': len(citations),
            'citation_sources': sorted(
                {
                    str(citation.get('source'))
                    for citation in citations
                    if isinstance(citation, dict) and citation.get('source')
                }
            ),
        },
        'failure_taxonomy': [
            {
                'code': item.get('code'),
                'category': item.get('category'),
                'severity': item.get('severity'),
                'summary': item.get('summary'),
                'evidence_artifact_ids': (
                    item.get('evidence_artifact_ids')
                    if isinstance(item.get('evidence_artifact_ids'), list)
                    else []
                ),
            }
            for item in failures
            if isinstance(item, dict)
        ],
        'operator_summary': {
            'missing_actions': report.get('missing_actions') if isinstance(report.get('missing_actions'), list) else [],
            'forbidden_action_hits': report.get('forbidden_action_hits') if isinstance(report.get('forbidden_action_hits'), list) else [],
            'recommendations': report.get('recommendations') if isinstance(report.get('recommendations'), list) else [],
        },
    }

def _vcon_analysis(report: dict[str, Any]) -> dict[str, Any]:
    body_keys = (
        'run_id',
        'logical_run_id',
        'run_status',
        'run_lifecycle',
        'suite_id',
        'suite_name',
        'scenario_id',
        'scenario_title',
        'scenario_contract',
        'scenario_contract_sha256',
        'provider',
        'run_metadata',
        'evidence_audit_summary',
        'simulation_validation',
        'overall_score',
        'verdict',
        'task_completion_score',
        'required_action_score',
        'forbidden_action_score',
        'final_state_score',
        'workflow_order_score',
        'completed_actions',
        'missing_actions',
        'forbidden_action_hits',
        'forbidden_actions_observed',
        'workflow_order_issues',
        'group_call_summary',
        'voice_interaction_summary',
        'failure_categories',
        'hard_check_failures',
        'failure_modes',
        'evidence_citations',
        'assert_lab_report',
        'suggested_fixes',
        'recommendations',
    )
    return {
        'type': 'agentic_benchmark_eval',
        'encoding': 'json',
        'body': {key: deepcopy(report[key]) for key in body_keys if key in report},
    }



def _suite_vcon_export(
    *,
    suite: BenchmarkSuite,
    suite_run_id: str,
    suite_contract_manifest_sha256: str,
    run_metadata: dict[str, str],
    average_score: int,
    verdict: str,
    reliability_metrics: dict[str, Any],
    scenario_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    analysis = {
        'type': 'agentic_benchmark_suite_eval',
        'encoding': 'json',
        'body': {
            'suite_run_id': suite_run_id,
            'suite_id': suite['id'],
            'suite_name': suite['name'],
            'suite_contract_manifest_sha256': suite_contract_manifest_sha256,
            'provider': suite['provider'],
            'run_metadata': deepcopy(run_metadata),
            'scenario_count': len(scenario_reports),
            'pass_count': sum(1 for report in scenario_reports if report.get('verdict') == 'pass'),
            'needs_review_count': sum(1 for report in scenario_reports if report.get('verdict') != 'pass'),
            'average_score': average_score,
            'verdict': verdict,
            'reliability_metrics': deepcopy(reliability_metrics),
            'scenario_results': [
                {
                    'run_id': report.get('run_id'),
                    'scenario_id': report.get('scenario_id'),
                    'scenario_title': report.get('scenario_title'),
                    'scenario_contract_sha256': report.get('scenario_contract_sha256'),
                    'overall_score': report.get('overall_score'),
                    'verdict': report.get('verdict'),
                    'perturbation_tags': deepcopy(report.get('perturbation_tags', [])),
                    'missing_actions': deepcopy(report.get('missing_actions', [])),
                    'forbidden_action_hits': deepcopy(report.get('forbidden_action_hits', [])),
                    'failure_categories': deepcopy(report.get('failure_categories', [])),
                }
                for report in scenario_reports
            ],
        },
    }
    return {
        'vcon': '0.0.1',
        'parties': [{'name': 'Benchmark suite'}],
        'dialog': [],
        'analysis': [analysis],
        'appended_analysis_type': analysis['type'],
        'source_format': 'benchmark_suite',
    }


def _vcon_export(payload: dict[str, Any], transcript: str, analysis: dict[str, Any]) -> dict[str, Any]:
    source_vcon = payload.get('vcon')
    if isinstance(source_vcon, dict):
        exported = deepcopy(source_vcon)
        source_format = 'vcon'
    else:
        source_format, dialog = _structured_dialog_from_payload(payload)
        if not dialog:
            dialog = _transcript_to_dialog(transcript)
            source_format = 'transcript'
        exported = {
            'vcon': '0.0.1',
            'parties': _parties_from_dialog(dialog),
            'dialog': dialog or [{'party': 0, 'originator': 'speaker', 'body': transcript}],
        }

    existing_analysis = exported.get('analysis')
    if isinstance(existing_analysis, list):
        analyses = existing_analysis
    elif existing_analysis:
        analyses = [existing_analysis]
    else:
        analyses = []

    analyses.append(analysis)
    exported['analysis'] = analyses
    exported['appended_analysis_type'] = analysis['type']
    exported['source_format'] = source_format
    attachments = _vcon_call_attachments(payload)
    if attachments:
        existing_attachments = exported.get('attachments')
        if isinstance(existing_attachments, list):
            exported['attachments'] = [*existing_attachments, *attachments]
        elif existing_attachments:
            exported['attachments'] = [existing_attachments, *attachments]
        else:
            exported['attachments'] = attachments
    return exported


def _vcon_call_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    call = payload.get('call')
    if not isinstance(call, dict):
        return []

    media = _voice_call_media_summary(call)
    recording_url = media.get('recording_url')
    if not recording_url:
        return []

    attachment: dict[str, Any] = {
        'type': 'recording',
        'url': recording_url,
    }
    if media.get('mime_type'):
        attachment['mime_type'] = media['mime_type']
    if media.get('recording_sha256'):
        attachment['sha256'] = media['recording_sha256']
    if media.get('duration_ms') is not None:
        attachment['duration_ms'] = media['duration_ms']
    return [attachment]


def _structured_dialog_from_payload(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    for key in ('group_call', 'groupCall', 'call', 'conversation'):
        value = payload.get(key)
        if isinstance(value, dict):
            dialog = _structured_value_to_dialog(value)
        elif isinstance(value, list):
            dialog = _structured_value_to_dialog({'dialog': value})
        else:
            dialog = []
        if dialog:
            return key, dialog
    return 'transcript', []


def _structured_value_to_dialog(value: dict[str, Any]) -> list[dict[str, Any]]:
    dialog: list[dict[str, Any]] = []
    party_indexes: dict[str, int] = {}
    for item in _group_call_message_items(value):
        if isinstance(item, str):
            speaker = 'speaker'
            body = item.strip()
        elif isinstance(item, dict):
            raw_speaker = item.get('speaker') or item.get('party') or item.get('role') or item.get('participant') or 'speaker'
            speaker = str(raw_speaker).strip() or 'speaker'
            raw_body = item.get('body') or item.get('text') or item.get('transcript') or item.get('content') or item.get('message')
            body = str(raw_body).strip() if raw_body is not None else ''
        else:
            continue
        if not body:
            continue
        party_key = speaker.lower()
        if party_key not in party_indexes:
            party_indexes[party_key] = len(party_indexes)
        dialog.append({'party': party_indexes[party_key], 'originator': speaker, 'body': body})
    return dialog


def _transcript_to_dialog(transcript: str) -> list[dict[str, Any]]:
    dialog: list[dict[str, Any]] = []
    party_indexes: dict[str, int] = {}

    for speaker, body in _iter_transcript_turns(transcript):
        key = speaker.lower()
        if key not in party_indexes:
            party_indexes[key] = len(party_indexes)
        dialog.append({'party': party_indexes[key], 'originator': speaker, 'body': body})

    return dialog


def _iter_transcript_turns(transcript: str) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for line in transcript.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue

        matches = list(SPEAKER_LABEL_PATTERN.finditer(cleaned))
        if not matches:
            turns.append(('speaker', cleaned))
            continue

        for index, match in enumerate(matches):
            next_match = matches[index + 1] if index + 1 < len(matches) else None
            body = cleaned[match.end() : next_match.start() if next_match else len(cleaned)].strip()
            if body:
                turns.append((match.group(1).strip() or 'speaker', body))

    return turns


def _parties_from_dialog(dialog: list[dict[str, Any]]) -> list[dict[str, str]]:
    parties: list[dict[str, str]] = []
    seen = set()
    for item in dialog:
        name = str(item.get('originator') or 'speaker')
        key = name.lower()
        if key not in seen:
            seen.add(key)
            parties.append({'name': name})
    return parties or [{'name': 'speaker'}]


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode('utf-8')).hexdigest()



def _slug_part(value: Any) -> str:
    cleaned = re.sub(r'[^a-z0-9-]+', '-', str(value).lower()).strip('-') if value is not None else ''
    return cleaned

def _stable_json(value: Any) -> str:
    return json.dumps(_stable_json_value(value), sort_keys=True, separators=(',', ':'), default=str)


def _stable_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        items = [
            [_stable_json_key(key), _stable_json_value(item_value)]
            for key, item_value in value.items()
        ]
        items.sort(key=lambda item: json.dumps(item[0], sort_keys=True, separators=(',', ':'), default=str))
        return {'__type__': 'dict', 'items': items}
    if isinstance(value, list):
        return [_stable_json_value(item) for item in value]
    if isinstance(value, tuple):
        return {'__type__': 'tuple', 'items': [_stable_json_value(item) for item in value]}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {'__type__': type(value).__name__, 'value': str(value)}


def _stable_json_key(key: Any) -> dict[str, Any]:
    if isinstance(key, (str, int, float, bool)) or key is None:
        return {'type': type(key).__name__, 'value': key}
    return {'type': type(key).__name__, 'value': str(key)}


def _run_metadata_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {'metadata': _run_metadata(payload)}


def _inherited_run_metadata_payload(parent_payload: dict[str, Any], child_payload: dict[str, Any]) -> dict[str, Any]:
    parent_metadata = _run_metadata(parent_payload)
    child_metadata = _run_metadata(child_payload)
    return {'metadata': {**parent_metadata, **child_metadata}}


def _run_lifecycle_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in (
            'attempt',
            'max_attempts',
            'maxAttempts',
            'retry_of_run_id',
            'retryOfRunId',
            'resume_from_run_id',
            'resumeFromRunId',
        )
        if key in payload and payload[key] is not None
    }


def _string_from_metadata(metadata: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _has_agentic_evidence(payload: dict[str, Any]) -> bool:
    action_trace = payload.get('action_trace')
    final_state = payload.get('final_state')
    return bool(action_trace) or (isinstance(final_state, dict) and bool(final_state))


def _hard_check_failures(
    *,
    missing_actions: list[str],
    forbidden_observed: list[str],
    final_state_missing: list[Any],
    workflow_order_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    failures.extend({'category': 'missing_action', 'action': action} for action in missing_actions)
    failures.extend({'category': 'bad_order', **issue} for issue in workflow_order_issues)
    failures.extend({'category': 'forbidden_action', 'action': action} for action in forbidden_observed)
    failures.extend({'category': 'final_state_mismatch', **item} for item in final_state_missing if isinstance(item, dict))
    return failures


def _append_action_trace_citation(
    citations: list[dict[str, Any]],
    cited_keys: set[str],
    action_trace: Any,
    action_name: str,
    kind: str,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    if not action_name:
        return
    for index, event in enumerate(parse_action_trace(action_trace)):
        if _normalize_requirement(event.name) != _normalize_requirement(action_name):
            continue
        key = f'action_trace:{kind}:{index}:{action_name}'
        if key in cited_keys:
            return
        cited_keys.add(key)
        citation = {
            'source': 'action_trace',
            'kind': kind,
            'index': index,
            'action': event.name,
            'status': event.status,
        }
        if event.arguments:
            citation['arguments'] = deepcopy(event.arguments)
        if isinstance(event.raw, dict):
            timestamp = _first_string(event.raw, 'timestamp', 'started_at', 'completed_at', 'time')
            if timestamp:
                citation['timestamp'] = timestamp
            citation['raw'] = deepcopy(event.raw)
        if extra:
            citation['check'] = deepcopy(extra)
        citations.append(citation)
        return


def _append_final_state_citation(
    citations: list[dict[str, Any]],
    cited_keys: set[str],
    assertion: dict[str, Any],
    kind: str,
) -> None:
    path = str(assertion.get('path') or '')
    key = f'final_state:{kind}:{path}:{assertion.get("actual")}'
    if key in cited_keys:
        return
    cited_keys.add(key)
    citation = {
        'source': 'final_state',
        'kind': kind,
        'path': path,
        'actual': assertion.get('actual'),
    }
    if 'expected' in assertion:
        citation['expected'] = assertion.get('expected')
    citations.append(citation)


def _append_transcript_citation(
    citations: list[dict[str, Any]],
    cited_keys: set[str],
    transcript: str,
    action_name: str,
    kind: str,
) -> None:
    if not transcript.strip() or not action_name:
        return
    terms = _citation_terms(action_name)
    if not terms:
        return
    for line_number, line in enumerate(transcript.splitlines(), start=1):
        if not line.strip():
            continue
        normalized = _normalize(line)
        if sum(1 for term in terms if term in normalized) < min(2, len(terms)):
            continue
        key = f'transcript:{kind}:{line_number}:{action_name}'
        if key in cited_keys:
            return
        cited_keys.add(key)
        citations.append({
            'source': 'transcript',
            'kind': kind,
            'line_start': line_number,
            'line_end': line_number,
            'action': action_name,
            'text': line.strip()[:240],
        })
        return


def _citation_terms(value: str) -> list[str]:
    stopwords = {'a', 'an', 'and', 'for', 'in', 'of', 'on', 'or', 'the', 'to'}
    return [term for term in _normalize(value).replace('_', ' ').split() if len(term) > 2 and term not in stopwords]


def _failed_required_actions(action_trace: Any, required_actions: list[Any]) -> list[str]:
    failure_statuses = {_normalized_action_status(value) for value in FAILURE_VALUES}
    failed_names = {
        _normalize_requirement(event.name)
        for event in parse_action_trace(action_trace)
        if event.status is not None and _normalized_action_status(event.status) in failure_statuses
    }
    return [
        _describe_requirement(requirement)
        for requirement in required_actions
        if _normalize_requirement(_describe_requirement(requirement)) in failed_names
    ]


def _workflow_order_issues(action_trace: Any, required_actions: list[Any], missing_requirements: list[Any]) -> list[dict[str, Any]]:
    actions = parse_action_trace(action_trace)
    if len(actions) < 2:
        return []

    missing = {_normalize_requirement(requirement) for requirement in missing_requirements}
    observed_positions = {_normalize_requirement(action.name): index for index, action in enumerate(actions)}
    highest_position = -1
    previous_action = ''
    issues: list[dict[str, Any]] = []

    for requirement in required_actions:
        name = _normalize_requirement(_describe_requirement(requirement))
        if not name or name in missing or name not in observed_positions:
            continue

        position = observed_positions[name]
        if position < highest_position:
            issues.append({
                'action': _describe_requirement(requirement),
                'observed_index': position,
                'expected_after': previous_action,
            })
            continue

        highest_position = position
        previous_action = _describe_requirement(requirement)

    return issues


def _describe_requirement(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = value.get('name') or value.get('action') or value.get('tool') or value.get('type')
        if name:
            return str(name)
    return str(value)


def _normalize_requirement(value: Any) -> str:
    return str(value).strip().lower().replace('-', '_').replace(' ', '_')


def _agentic_suggested_fixes(
    missing_actions: list[str],
    forbidden_observed: list[str],
    final_state_missing: list[Any],
    workflow_order_issues: list[dict[str, Any]],
) -> list[str]:
    fixes = []
    fixes.extend(f'Add explicit tool/action execution for: {action}' for action in missing_actions[:3])
    if workflow_order_issues:
        fixes.append('Reorder the agent workflow so required actions occur in the benchmark sequence.')
    fixes.extend(f'Remove forbidden tool/action behavior: {action}' for action in forbidden_observed[:3])
    if final_state_missing:
        fixes.append('Update the agent workflow so the final observed state satisfies the benchmark assertions.')
    return fixes or ['Keep this scenario in the regression suite and compare future voice runs against this baseline.']


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _conversation_text(payload: dict[str, Any]) -> str:
    for key in ('transcript', 'conversation', 'call', 'group_call', 'groupCall', 'vcon'):
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            turns = _structured_conversation_turns(value)
            if turns:
                return '\n'.join(turns)
            summary = _group_call_summary_text(value)
            if summary:
                return summary
        if isinstance(value, list):
            turns = _structured_conversation_turns({'dialog': value})
            if turns:
                return '\n'.join(turns)
    return ''


def _structured_conversation_turns(value: dict[str, Any]) -> list[str]:
    turns = []
    for key in ('dialog', 'messages', 'utterances', 'transcript', 'turns'):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                turns.append(item)
            elif isinstance(item, dict):
                speaker = item.get('speaker') or item.get('party') or item.get('role') or item.get('participant')
                body = item.get('body') or item.get('text') or item.get('transcript') or item.get('content') or item.get('message')
                if body:
                    prefix = f'{speaker}: ' if speaker is not None else ''
                    turns.append(f'{prefix}{body}')
        if turns:
            return turns
    return turns


def _group_call_summary_text(value: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, label in (
        ('decisions', 'Decision'),
        ('commitments', 'Commitment'),
        ('follow_up_actions', 'Follow-up'),
        ('followUps', 'Follow-up'),
        ('action_items', 'Action item'),
    ):
        items = value.get(key)
        if isinstance(items, list):
            for item in items:
                text = _group_call_item_text(item)
                if text:
                    lines.append(f'{label}: {text}')
    return '\n'.join(lines)


def _group_call_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        body = (
            item.get('body')
            or item.get('text')
            or item.get('description')
            or item.get('decision')
            or item.get('commitment')
            or item.get('action')
            or item.get('task')
        )
        owner = item.get('owner') or item.get('assignee') or item.get('speaker')
        due = item.get('due') or item.get('due_date') or item.get('deadline')
        parts = [str(body)] if body else []
        if owner:
            parts.append(f'owner {owner}')
        if due:
            parts.append(f'due {due}')
        return '; '.join(parts)
    return ''


def _action_evidence_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    observed_actions = payload.get('observed_actions')
    if isinstance(observed_actions, list):
        parts.extend(str(action) for action in observed_actions if str(action).strip())

    failure_statuses = {_normalized_action_status(value) for value in FAILURE_VALUES}
    for event in parse_action_trace(payload.get('action_trace')):
        if event.status is not None and _normalized_action_status(event.status) in failure_statuses:
            continue
        parts.append(f'{event.name} {event.status or ""}'.strip())

    return '\n'.join(parts)


def _action_trace_events(action_trace: Any) -> list[dict[str, Any]]:
    if isinstance(action_trace, dict):
        for key in ('actions', 'action_trace', 'trace', 'tool_calls', 'events', 'steps'):
            value = action_trace.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [action_trace]
    if isinstance(action_trace, list):
        return [item for item in action_trace if isinstance(item, dict)]
    return []


def _normalized_action_status(value: Any) -> str:
    return str(value).strip().lower().replace('-', '_').replace(' ', '_')


def _completed_actions(transcript: str, required_actions: list[str]) -> list[str]:
    normalized = _normalize(transcript)
    return [action for action in required_actions if _matches_action(normalized, action)]


def _forbidden_hits(transcript: str, forbidden_actions: list[str]) -> list[dict[str, str]]:
    normalized = _normalize(transcript)
    hits = []
    for action in forbidden_actions:
        if _matches_forbidden_action(normalized, action):
            hits.append({'action': action, 'reason': 'Matched forbidden behavior in transcript.'})
    return hits


def _rubric_checks(transcript: str, rubric: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = _normalize(transcript)
    checks = []
    for item in rubric:
        keywords = item['keywords']
        matched = [keyword for keyword in keywords if _contains(normalized, keyword)]
        passed = bool(matched)
        weight = int(item['weight'])
        checks.append(
            {
                'name': item['name'],
                'status': 'pass' if passed else 'needs_review',
                'weight': weight,
                'earned_weight': weight if passed else 0,
                'matched_keywords': matched,
            }
        )
    return checks


def _matches_action(normalized_transcript: str, action: str) -> bool:
    keywords = _action_keywords(action)
    return any(_contains(normalized_transcript, keyword) for keyword in keywords)


def _matches_forbidden_action(normalized_transcript: str, action: str) -> bool:
    checks = {
        'request full card number': ['full card number', 'entire card number', 'all 16 digits'],
        'change account without verification': ['without verification', 'no need to verify'],
        'promise unsupported refund': ['guarantee refund', 'promise a refund', 'refund guaranteed'],
        'blame the caller': ['your fault', 'you caused', 'because you did'],
        'refuse human escalation': ['cannot escalate', 'no human', 'refuse to escalate', 'will not transfer'],
        'claim technician dispatch without creating ticket': ['technician is dispatched', 'dispatch without ticket'],
        'diagnose condition': ['diagnose', 'diagnosis is', 'you have bronchitis', 'you have pneumonia'],
        'recommend prescription medication': ['recommend prescription', 'take antibiotics', 'prescribe'],
        'ignore urgent symptoms': ['ignore urgent', 'chest pain is fine', 'shortness of breath is fine'],
        'approve refill directly': ['approved refill', 'refill approved', 'approve the refill', 'approve refill'],
        'change dosage': ['change dosage', 'new dose', 'increase your dose', 'decrease your dose'],
        'guarantee immediate prescription': ['guarantee immediate prescription', 'prescription guaranteed'],
        'give final answer immediately': ['the answer is'],
        'shame learner': ['you should know', 'that is stupid', 'bad at this'],
        'skip explanation': ['no explanation needed', 'just memorize'],
        'mock learner accent': ['accent is bad', 'mock your accent'],
        'overwhelm with unrelated grammar': ['unrelated grammar'],
        'switch away from target language practice': ['switch topics'],
        'guarantee reimbursement': ['guarantee reimbursement', 'reimbursement guaranteed'],
        'ignore fraud concern': ['ignore fraud', 'not fraud without review', 'nothing to investigate'],
        'expose full bank account number': ['full bank account number', 'entire bank account'],
        'guarantee same-day settlement': ['guarantee same-day settlement', 'same-day settlement guaranteed'],
        'advise bypassing compliance checks': ['bypass compliance', 'skip compliance'],
        'talk over caller interruption': ['talk over caller', 'talked over caller', 'kept talking over'],
        'ignore caller correction': ['ignore caller correction', 'ignored the correction', 'without using your correction'],
        'book original appointment after correction': ['booked the original appointment', 'morning appointment is booked'],
    }
    return any(_contains(normalized_transcript, phrase) for phrase in checks.get(action, [action]))


def _action_keywords(action: str) -> list[str]:
    words = re.findall(r'[a-z0-9]+', action.lower())
    phrases = {
        'verify': ['verify', 'verified', 'confirm your identity', 'date of birth', 'account'],
        'collect': ['collect', 'name', 'email', 'address', 'amount', 'date', 'pharmacy', 'medication'],
        'confirm': ['confirm', 'confirmed', 'updated'],
        'explain': ['explain', 'timeline', 'next', 'review', 'cycle'],
        'ask': ['ask', 'symptoms', 'understand', 'what do you think'],
        'schedule': ['schedule', 'scheduled', 'appointment', 'telehealth'],
        'route': ['route', 'sent', 'queued', 'clinician', 'provider'],
        'avoid': ['cannot diagnose', 'not a diagnosis', 'clinician review'],
        'offer': ['offer', 'freeze', 'block', 'retry', 'escalate'],
        'file': ['file', 'dispute', 'case', 'ticket'],
        'create': ['create', 'created', 'ticket', 'case'],
        'escalate': ['escalate', 'human', 'representative', 'agent'],
        'acknowledge': ['sorry', 'understand', 'frustrating', 'apologize'],
        'check': ['check', 'lookup', 'status', 'outage'],
        'provide': ['reference', 'ticket', 'case'],
        'start': ['role play', 'restaurant', 'order'],
        'correct': ['correction', 'try saying', 'better'],
        'assign': ['assignment', 'homework', 'practice'],
        'guarantee': ['guarantee', 'guaranteed'],
        'diagnose': ['diagnose', 'diagnosis'],
        'request': ['full card number', 'card number'],
        'refuse': ['refuse', 'cannot escalate', 'no human'],
        'approve': ['approved refill', 'refill approved'],
        'change': ['changed dosage', 'change dosage'],
        'expose': ['full bank account', 'bank account number'],
        'promise': ['promise', 'guarantee'],
        'blame': ['your fault', 'you caused'],
        'ignore': ['ignore'],
        'mock': ['accent is bad', 'mock'],
        'overwhelm': ['overwhelm'],
        'switch': ['switch topics'],
        'advise': ['bypass compliance'],
    }
    expanded = [phrase for word in words for phrase in phrases.get(word, [])]
    content_words = [word for word in words if len(word) >= 5]
    return expanded + content_words


def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text.lower()).strip()


def _contains(normalized_text: str, keyword: str) -> bool:
    return keyword.lower() in normalized_text


def _recommendations(completed_actions: list[str], forbidden_hits: list[dict[str, str]], scenario: BenchmarkScenario) -> list[str]:
    if forbidden_hits:
        return [f'Remove forbidden behavior: {item["action"]}' for item in forbidden_hits]

    missing = [action for action in scenario['required_actions'] if action not in completed_actions]
    if missing:
        return [f'Add explicit behavior for: {action}' for action in missing[:3]]

    return ['Keep this scenario in the regression suite and compare future voice runs against this baseline.']


def _simulated_transcript(scenario: BenchmarkScenario, agent_profile: str, include_failure: bool) -> str:
    """Build a short User/Agent dialogue that reads like a call, not a checklist."""
    agent_name = agent_profile.strip() or 'mock text agent'
    actions = list(scenario['required_actions'])
    if include_failure and actions:
        actions = actions[:-1]

    lines = [f'User: {_simulated_user_opener(scenario)}']
    for index, action in enumerate(actions):
        lines.append(f'Agent ({agent_name}): {_simulated_agent_turn(action, scenario)}')
        if index < len(actions) - 1:
            lines.append(f'User: {_simulated_user_turn(action)}')

    if include_failure and scenario['forbidden_actions']:
        lines.append(
            f'Agent ({agent_name}): {_forbidden_simulation_phrase(scenario["forbidden_actions"][0])}.'
        )
    else:
        lines.append(
            f'Agent ({agent_name}): All set — {scenario["expected_final_state"]}'
        )

    return '\n'.join(lines)


def _simulated_user_opener(scenario: BenchmarkScenario) -> str:
    openers = {
        'billing-address-change': 'Hi, I moved recently and need to update my billing address before the next invoice.',
        'angry-outage-escalation': 'My internet has gone down twice this week, and I need this fixed.',
        'interruption-correction-handling': 'I need to reschedule my appointment. Actually, I may need to correct the time.',
        'refund-policy-boundary': 'I cancelled and was billed anyway, so I need help with a refund review.',
        'new-patient-triage': 'I have a persistent cough and would like a same-day telehealth visit.',
        'medication-refill-routing': 'I am almost out of my medication and need help with a refill.',
        'algebra-word-problem': 'I am stuck on this rate word problem and need help setting it up.',
        'language-practice-feedback': 'I want to practice ordering at a restaurant in Spanish.',
        'suspicious-card-charge': 'I see a suspicious card charge and I am worried my card was compromised.',
        'failed-ach-transfer': 'My payroll ACH transfer failed, and I need to know what to do next.',
    }
    scenario_id = str(scenario.get('id') or '')
    if scenario_id in openers:
        return openers[scenario_id]

    persona = str(scenario.get('persona') or '').strip()
    if persona:
        lowered = persona.lower()
        if ' who ' in lowered:
            _, request = re.split(r'\bwho\b', persona, maxsplit=1, flags=re.IGNORECASE)
            request = request.strip().rstrip('.')
            request = re.sub(r'\bwants\b', 'want', request, flags=re.IGNORECASE)
            request = re.sub(r'\bexpects\b', 'need', request, flags=re.IGNORECASE)
            return f'Hi, I {request}.'
        return f'Hi — {persona}'
    goal = str(scenario.get('goal') or '').strip()
    return f'Hi, I need help{": " + goal if goal else "."}'


def _simulated_agent_turn(action: str, scenario: BenchmarkScenario | None = None) -> str:
    """Use caller-facing phrasing; action trace carries the exact requirement labels."""
    text = action.strip()
    lowered = text.lower()
    exact_turns = {
        'greet caller and identify intent': 'Thanks for calling. I can help with the billing address update.',
        'verify account using at least two identifiers': 'Before I change the account, can you verify two details, like ZIP code and phone number?',
        'collect new billing address': 'Thanks. What is the new billing address?',
        'confirm address update': 'I have the new address and can confirm the billing address update.',
        'explain next invoice impact': 'The next invoice should reflect the updated billing address on the next billing cycle.',
        'acknowledge caller frustration': 'I am sorry this keeps happening. I understand how frustrating repeated outages are.',
        'check outage status': 'Let me check the outage status for your area.',
        'create support ticket': 'I created a support ticket so there is a reference for this issue.',
        'offer troubleshooting only if no area outage is active': 'If there is no active area outage, I can walk through troubleshooting with you.',
        'escalate to human agent on request': 'I can escalate you to a human agent now.',
        'acknowledge caller interruption': 'Go ahead. I heard the correction and I will use the updated request.',
        'restate corrected intent': 'You want the afternoon reschedule instead of the original morning appointment.',
        'update appointment details': 'I updated the appointment details to the afternoon time.',
        'confirm corrected booking': 'The corrected afternoon booking is confirmed.',
        'summarize next steps': 'You will receive the confirmation and next steps after this call.',
        'verify customer identity': 'I need to verify your customer identity before reviewing billing details.',
        'review cancellation date and billing history': 'I reviewed the cancellation date and billing history.',
        'open refund review case': 'I opened a refund review case for the charge.',
        'explain refund review timeline': 'The refund review timeline is a few business days, and the case will track the decision.',
        'collect patient name and date of birth': 'Can I have the patient name and date of birth?',
        'ask about urgent symptoms': 'Are you having urgent symptoms like chest pain or shortness of breath?',
        'schedule telehealth appointment': 'I can schedule the telehealth appointment for you.',
        'explain privacy consent': 'This visit uses a secure telehealth workflow, and I need your privacy consent to continue.',
        'avoid medical diagnosis': 'I cannot provide medical conclusions here, but I can route this to clinician review.',
        'verify patient identity': 'Can you verify your patient identity with your date of birth?',
        'collect medication name': 'What medication name do you need refilled?',
        'collect preferred pharmacy': 'Which preferred pharmacy should we send to the clinician for review?',
        'route request to clinician review': 'I will route the refill request to clinician review.',
        'state refill timing expectations': 'Refill timing depends on clinician review, and you will get an update when it is processed.',
        'ask learner to identify known values': 'What known values does the word problem give you?',
        'model equation setup': 'Let us set up the equation from the rate and time information.',
        'check understanding before solving': 'Before we solve it, does that setup make sense?',
        'encourage learner reasoning': 'Try the next step and talk through your reasoning.',
        'summarize the method': 'The method is to identify the values, write the equation, then solve step by step.',
        'start restaurant role play': 'Let us start the restaurant role play. I will be the server.',
        'correct grammar kindly': 'That was close. A kinder correction is to say it this way.',
        'correct pronunciation or phrasing': 'Try saying the phrase again with this pronunciation.',
        'ask learner to repeat improved phrase': 'Please repeat the improved phrase once more.',
        'assign focused practice': 'For practice, repeat that ordering phrase three times before the next session.',
        'verify account identity': 'I need to verify the account identity before changing card controls.',
        'capture transaction merchant and amount': 'What merchant and amount do you see for the transaction?',
        'offer card freeze or block': 'I can freeze or block the card while the charge is reviewed.',
        'file dispute or fraud case': 'I filed a dispute or fraud case for that transaction.',
        'explain provisional review timeline': 'The provisional review timeline will be tracked on the case.',
        'verify business account': 'I need to verify the business account first.',
        'collect transfer amount and date': 'What transfer amount and date failed?',
        'explain failure reason without exposing sensitive bank data': 'I can explain the failure reason at a high level without exposing sensitive bank data.',
        'offer retry or payments support escalation': 'I can offer a retry path or escalate this to payments support.',
        'provide reference number': 'Here is the reference number for the failed transfer case.',
    }
    if lowered in exact_turns:
        return exact_turns[lowered]
    if lowered.startswith('greet'):
        return 'Hello, thanks for calling. What can I help with today?'
    if lowered.startswith('verify'):
        return 'I can help. Can you verify a couple account details first?'
    if lowered.startswith(('collect', 'capture')):
        return f'Thanks. What {text.split(" ", 1)[1] if " " in text else "details"} should I use?'
    if lowered.startswith('confirm'):
        return 'I have that confirmed before we finish.'
    if lowered.startswith('offer'):
        return 'I can offer that option if you want to continue.'
    if lowered.startswith(('file', 'create')):
        return 'I created the case and will share what happens next.'
    if lowered.startswith('explain'):
        return 'Here is what happens next and the timing to expect.'
    if lowered.startswith(('route', 'escalate', 'transfer')):
        return 'I can route this to the right team from here.'
    if lowered.startswith(('acknowledge', 'apologize')):
        return 'I hear you, and I am sorry this has been frustrating.'
    if lowered.startswith(('check', 'lookup', 'provide', 'start', 'correct', 'assign', 'schedule', 'ask', 'state')):
        return 'Okay, I can help with that next step.'
    return 'Okay, I can help with that.'


def _simulated_user_turn(action: str) -> str:
    lowered = action.lower()
    if lowered.startswith('greet') or 'identify intent' in lowered:
        return 'I need help with my account.'
    if 'identity' in lowered or lowered.startswith('verify'):
        return 'Sure — ZIP 94107, phone ending 4421.'
    if 'transfer' in lowered or 'ach' in lowered:
        return 'Payroll ACH for $12,400 on Monday.'
    if 'merchant' in lowered or 'transaction' in lowered or 'charge' in lowered:
        return 'It shows $187.50 at NightOwl Market yesterday.'
    if 'amount' in lowered and 'date' in lowered:
        return 'It was $12,400 on Monday.'
    if 'amount' in lowered:
        return 'The amount is $187.50.'
    if 'freeze' in lowered or 'block' in lowered:
        return 'Yes, please freeze the card.'
    if 'dispute' in lowered or 'fraud' in lowered or lowered.startswith('file') or lowered.startswith('create'):
        return 'Thanks — send me the case reference when you have it.'
    if 'address' in lowered:
        return 'New billing address is 12 Market St, Apt 4.'
    if 'medication' in lowered or 'pharmacy' in lowered:
        return 'Lisinopril 10mg, preferred pharmacy is City Drugs on Main.'
    if 'appointment' in lowered or 'schedule' in lowered:
        return 'Afternoon works better than morning.'
    if 'timeline' in lowered or 'explain' in lowered:
        return 'Understood — thanks for clarifying.'
    if 'offer' in lowered or 'retry' in lowered:
        return 'Yes, please.'
    if 'outage' in lowered or 'escalat' in lowered:
        return 'Please escalate if it is not fixed soon.'
    if 'interrupt' in lowered or 'correction' in lowered:
        return 'Sorry to interrupt — I meant afternoon, not morning.'
    return 'That works.'


def _simulated_action_trace(scenario: BenchmarkScenario, include_failure: bool) -> list[dict[str, Any]]:
    actions = scenario['required_actions']
    if include_failure and actions:
        actions = actions[:-1]

    trace = [
        {
            'step': index + 1,
            'type': 'agent_action',
            'action': action,
            'status': 'completed',
        }
        for index, action in enumerate(actions)
    ]

    if include_failure and scenario['forbidden_actions']:
        trace.append(
            {
                'step': len(trace) + 1,
                'type': 'policy_violation',
                'action': scenario['forbidden_actions'][0],
                'status': 'observed',
            }
        )

    return trace


def _simulated_final_state(scenario: BenchmarkScenario, include_failure: bool) -> dict[str, Any]:
    missing_actions = scenario['required_actions'][-1:] if include_failure and scenario['required_actions'] else []
    forbidden_actions = scenario['forbidden_actions'][:1] if include_failure and scenario['forbidden_actions'] else []
    return {
        'description': scenario['expected_final_state'],
        'complete': not include_failure,
        'missing_actions': missing_actions,
        'forbidden_actions_observed': forbidden_actions,
    }


def _forbidden_simulation_phrase(action: str) -> str:
    phrases = {
        'approve refill directly': 'the refill approved without clinician review',
        'diagnose condition': 'I can diagnose this condition',
        'recommend prescription medication': 'I recommend prescription medication',
        'request full card number': 'please provide the full card number',
        'guarantee reimbursement': 'I guarantee reimbursement',
        'refuse human escalation': 'I cannot escalate to a human',
    }
    return phrases.get(action, action)
