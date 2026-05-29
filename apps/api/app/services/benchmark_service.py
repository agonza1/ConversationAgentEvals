from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.services.benchmark_evaluator import BenchmarkEvaluation, evaluate_benchmark, parse_action_trace


BenchmarkScenario = dict[str, Any]
BenchmarkSuite = dict[str, Any]
DETERMINISTIC_EVALUATOR_VERSION = 'deterministic-agentic-v1'


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
    return [
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


def get_suite(suite_id: str) -> BenchmarkSuite | None:
    suite = _SUITES_BY_ID.get(suite_id)
    return deepcopy(suite) if suite else None


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
        {'from': 'running', 'to': 'evaluating', 'at': run_started_at, 'reason': 'deterministic evaluator started'},
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
    suite_id = _first_string(payload, 'suite_id', 'suiteId')
    scenario_id = _first_string(payload, 'scenario_id', 'scenarioId')
    if not suite_id or not scenario_id:
        raise ValueError('suite_id and scenario_id are required')

    suite = _SUITES_BY_ID.get(suite_id)
    scenario = _SCENARIOS_BY_ID.get((suite_id, scenario_id))
    if not suite or not scenario:
        raise ValueError(f'Unknown benchmark scenario: {suite_id}/{scenario_id}')

    transcript = _conversation_text(payload)
    action_evidence_text = _action_evidence_text(payload)
    scoring_text = '\n'.join(item for item in (transcript, action_evidence_text) if item)
    completed_actions = _completed_actions(scoring_text, scenario['required_actions'])
    forbidden_hits = _forbidden_hits(scoring_text, scenario['forbidden_actions'])
    rubric_checks = _rubric_checks(transcript, scenario['rubric'])
    required_score = round((len(completed_actions) / len(scenario['required_actions'])) * 100)
    rubric_score = sum(check['earned_weight'] for check in rubric_checks)
    penalty = min(40, len(forbidden_hits) * 20)
    overall_score = max(0, round((required_score * 0.45) + (rubric_score * 0.55) - penalty))
    verdict = 'pass' if overall_score >= 75 and not forbidden_hits else 'needs_review'
    run_metadata = _run_metadata(payload)
    lifecycle_context = _run_lifecycle_context(payload)
    evidence_artifacts = _evidence_artifacts(payload, transcript)
    logical_run_id = _logical_run_id(suite_id, scenario_id, evidence_artifacts, run_metadata)
    run_id = _run_id(suite_id, scenario_id, evidence_artifacts, run_metadata, lifecycle_context)
    action_trace = payload.get('action_trace')
    final_state = payload.get('final_state')
    agentic_evaluation = _agentic_evaluation(scenario, action_trace, final_state) if _has_agentic_evidence(payload) else None

    if agentic_evaluation:
        overall_score = agentic_evaluation.overall_score
        verdict = 'pass' if overall_score >= 75 and agentic_evaluation.forbidden_action_avoidance.passed else 'needs_review'

    scenario_contract = _scenario_contract(scenario)
    evaluated_at = datetime.now(UTC).isoformat()
    report = {
        'run_id': run_id,
        'logical_run_id': logical_run_id,
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'scenario_id': scenario_id,
        'scenario_title': scenario['title'],
        'scenario_contract': scenario_contract,
        'scenario_contract_sha256': _stable_digest(scenario_contract),
        'provider': suite['provider'],
        'run_metadata': run_metadata,
        'evidence_artifacts': evidence_artifacts,
        'evidence_audit_summary': _evidence_audit_summary(
            payload=payload,
            run_metadata=run_metadata,
            run_id=run_id,
            run_started_at=run_started_at,
            evaluated_at=evaluated_at,
        ),
        'overall_score': overall_score,
        'verdict': verdict,
        'required_action_score': required_score,
        'rubric_score': rubric_score,
        'completed_actions': completed_actions,
        'missing_actions': [action for action in scenario['required_actions'] if action not in completed_actions],
        'forbidden_action_hits': forbidden_hits,
        'rubric_checks': rubric_checks,
        'expected_final_state': scenario['expected_final_state'],
        'transcript_preview': transcript[:700],
        'group_call_summary': _group_call_artifact_summary(payload),
        'voice_interaction_summary': _voice_interaction_summary(payload, transcript),
        'recommendations': _recommendations(completed_actions, forbidden_hits, scenario),
    }
    if agentic_evaluation:
        report.update(_agentic_report_fields(agentic_evaluation, action_trace, final_state, scenario['required_actions']))
        if report.get('workflow_order_issues'):
            report['verdict'] = 'needs_review'
    report['run_lifecycle'] = _run_lifecycle(
        lifecycle_context=lifecycle_context,
        logical_run_id=logical_run_id,
        run_started_at=run_started_at,
        evaluated_at=evaluated_at,
        verdict=report['verdict'],
        failure_categories=report.get('failure_categories'),
    )
    report['run_status'] = report['run_lifecycle']['status']
    report['vcon_analysis'] = _vcon_analysis(report)
    report['vcon_export'] = _vcon_export(payload, transcript, report['vcon_analysis'])
    return report


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
    benchmark_report = run_scenario(
        {
            'suite_id': suite_id,
            'scenario_id': scenario_id,
            'transcript': transcript,
            'action_trace': action_trace,
            'final_state': final_state,
            **_run_metadata_payload(payload),
            **_run_lifecycle_payload(payload),
        }
    )

    return {
        'suite_id': suite_id,
        'suite_name': suite['name'],
        'scenario_id': scenario_id,
        'scenario_title': scenario['title'],
        'transcript': transcript,
        'action_trace': action_trace,
        'final_state': final_state,
        'run_metadata': benchmark_report['run_metadata'],
        'benchmark_report': benchmark_report,
    }


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
) -> dict[str, Any]:
    input_artifact_types = [
        key
        for key in ('transcript', 'conversation', 'call', 'group_call', 'groupCall', 'vcon', 'observed_actions', 'action_trace', 'final_state')
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
        'evaluator_version': DETERMINISTIC_EVALUATOR_VERSION,
        'export_readiness': {
            'ready': not missing_for_export,
            'format': 'saved_run_json',
            'missing': missing_for_export,
        },
    }
    group_call_summary = _group_call_artifact_summary(payload)
    if group_call_summary:
        summary['group_call_summary'] = group_call_summary
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

    return {
        'turn_count': len(_iter_transcript_turns(transcript)),
        'interruption_signal_count': sum(1 for phrase in interruption_phrases if _contains(normalized, phrase)),
        'correction_signal_count': sum(1 for phrase in correction_phrases if _contains(normalized, phrase)),
        'handoff_signal_count': sum(1 for phrase in handoff_phrases if _contains(normalized, phrase)),
        'action_trace_event_count': len(_action_trace_events(payload.get('action_trace'))),
    }

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

    for key in ('observed_actions', 'action_trace', 'final_state', 'conversation', 'call', 'group_call', 'groupCall', 'vcon'):
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
        'suggested_fixes',
        'recommendations',
    )
    return {
        'type': 'agentic_benchmark_eval',
        'encoding': 'json',
        'body': {key: deepcopy(report[key]) for key in body_keys if key in report},
    }


def _vcon_export(payload: dict[str, Any], transcript: str, analysis: dict[str, Any]) -> dict[str, Any]:
    source_vcon = payload.get('vcon')
    if isinstance(source_vcon, dict):
        exported = deepcopy(source_vcon)
        source_format = 'vcon'
    else:
        dialog = _transcript_to_dialog(transcript)
        exported = {
            'vcon': '0.0.1',
            'parties': _parties_from_dialog(dialog),
            'dialog': dialog or [{'party': 0, 'originator': 'speaker', 'body': transcript}],
        }
        source_format = 'transcript'

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
    return exported


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


def _agentic_evaluation(scenario: BenchmarkScenario, action_trace: Any, final_state: Any) -> BenchmarkEvaluation:
    return evaluate_benchmark(
        action_trace=action_trace,
        final_state=final_state,
        task_completion={'completed': True},
        required_actions=scenario['required_actions'],
        forbidden_actions=scenario['forbidden_actions'],
        expected_final_state={'complete': True},
    )


def _agentic_report_fields(
    evaluation: BenchmarkEvaluation,
    action_trace: Any,
    final_state: Any,
    required_actions: list[Any],
) -> dict[str, Any]:
    missing_actions = [_describe_requirement(item) for item in evaluation.required_action_execution.missing]
    forbidden_observed = [_describe_requirement(item) for item in evaluation.forbidden_action_avoidance.violations]
    final_state_missing = evaluation.final_state_correctness.missing
    workflow_order_issues = _workflow_order_issues(action_trace, required_actions, evaluation.required_action_execution.missing)
    failure_categories = []
    if not evaluation.task_completion.passed:
        failure_categories.append('task_completion')
    if missing_actions:
        failure_categories.append('required_action_execution')
    if workflow_order_issues:
        failure_categories.append('workflow_ordering')
    if forbidden_observed:
        failure_categories.append('forbidden_action_avoidance')
    if final_state_missing:
        failure_categories.append('final_state_correctness')

    return {
        'score': evaluation.overall_score,
        'task_completion_score': evaluation.task_completion.score,
        'required_action_score': evaluation.required_action_execution.score,
        'forbidden_action_score': evaluation.forbidden_action_avoidance.score,
        'final_state_score': evaluation.final_state_correctness.score,
        'workflow_order_score': 0 if workflow_order_issues else 100,
        'missing_actions': missing_actions,
        'forbidden_actions_observed': forbidden_observed,
        'final_state_missing': final_state_missing,
        'workflow_order_issues': workflow_order_issues,
        'failure_categories': failure_categories,
        'suggested_fixes': _agentic_suggested_fixes(missing_actions, forbidden_observed, final_state_missing, workflow_order_issues),
        'evidence': (
            evaluation.task_completion.evidence
            + evaluation.required_action_execution.evidence
            + evaluation.final_state_correctness.evidence
        ),
        'evidence_spans': (
            evaluation.task_completion.evidence
            + evaluation.required_action_execution.evidence
            + evaluation.final_state_correctness.evidence
        ),
        'action_trace': action_trace,
        'final_state': final_state,
    }


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

    for event in _action_trace_events(payload.get('action_trace')):
        name = event.get('action') or event.get('name') or event.get('tool') or event.get('tool_name') or event.get('type')
        status = event.get('status') or event.get('state') or event.get('outcome')
        if name:
            parts.append(f'{name} {status or ""}'.strip())

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
        'approve refill directly': ['approved refill', 'refill approved'],
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
    agent_name = agent_profile.strip() or 'mock text agent'
    lines = [
        f'Synthetic user: {scenario["persona"]} Goal: {scenario["goal"]}',
        f'Agent ({agent_name}): I understand the request and will handle it step by step.',
    ]

    actions = scenario['required_actions']
    if include_failure and actions:
        actions = actions[:-1]

    for action in actions:
        lines.append(f'Agent ({agent_name}): I will {action}.')

    if include_failure and scenario['forbidden_actions']:
        lines.append(
            f'Agent ({agent_name}): I made an error: {_forbidden_simulation_phrase(scenario["forbidden_actions"][0])}.'
        )
    else:
        lines.append(f'Agent ({agent_name}): Final state confirmed: {scenario["expected_final_state"]}')

    return '\n'.join(lines)


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
