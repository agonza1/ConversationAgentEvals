from __future__ import annotations

import json
from pathlib import Path

from app.services.agentic_contact_center_example import build_assert_run_request, normalize_acc_run


def _scenario() -> dict:
    test_path = Path(__file__).resolve()
    scenario_path = next(
        candidate
        for parent in test_path.parents
        for candidate in [parent / 'docs' / 'examples' / 'agentic-contact-center-cancellation-rescue.json']
        if candidate.exists()
    )
    return json.loads(scenario_path.read_text())


def _acc_payload() -> dict:
    return {
        'outcome': 'scripted_wrap_complete',
        'call': {
            'session': {
                'callId': 'demo-call-98',
                'runtimeModeLabels': {
                    'telephony': 'mocked_telephony',
                    'media': 'generated_media',
                    'rtcAsr': 'rtc_asr_blocked',
                },
            },
            'flowState': 'wrap',
            'operatorSteer': {'pending': False, 'lastAction': 'approve_offer'},
            'demoFallback': {'armed': False, 'mode': None},
            'transcript': [
                {
                    'speaker': 'caller',
                    'text': 'I want to cancel because the renewal increase is too high.',
                    'timestamp': '2026-07-13T01:00:00Z',
                },
                {
                    'speaker': 'agent',
                    'text': 'I will pause for an approved safe review path.',
                    'timestamp': '2026-07-13T01:00:01Z',
                },
                {
                    'speaker': 'operator',
                    'text': 'Approved safe follow-up only.',
                    'timestamp': '2026-07-13T01:00:02Z',
                },
            ],
            'events': [
                {
                    'type': 'call_started',
                    'at': '2026-07-13T01:00:00Z',
                    'detail': {'mode': 'mocked_telephony'},
                },
                {
                    'type': 'operator_steer_applied',
                    'at': '2026-07-13T01:00:02Z',
                    'detail': {'action': 'approve_offer'},
                },
                {
                    'type': 'call_wrapped',
                    'at': '2026-07-13T01:00:03Z',
                    'detail': {'disposition': 'follow_up_requested'},
                },
            ],
            'latencyMarks': [
                {
                    'stage': 'caller_turn_received',
                    'elapsedMs': 22,
                    'budgetMs': 100,
                    'recordedAt': '2026-07-13T01:00:00Z',
                },
                {
                    'stage': 'operator_steer',
                    'elapsedMs': 650,
                    'budgetMs': 500,
                    'recordedAt': '2026-07-13T01:00:02Z',
                },
            ],
        },
        'proof': {
            'callId': 'demo-call-98',
            'outcome': 'scripted_wrap_complete',
            'artifacts': {'proof': '/api/calls/demo-call-98/proof'},
        },
    }


def test_normalize_acc_run_preserves_call_evidence_and_limitations():
    normalized = normalize_acc_run(_acc_payload(), scenario=_scenario())

    assert normalized['execution_mode'] == 'acc_http_scripted_fixture'
    assert normalized['call_id'] == 'demo-call-98'
    assert normalized['outcome'] == 'scripted_wrap_complete'
    assert normalized['final_state']['complete'] is True
    assert normalized['final_state']['flow_state'] == 'wrap'
    assert normalized['final_state']['operator_steer']['lastAction'] == 'approve_offer'
    assert 'Caller: I want to cancel' in normalized['transcript']
    assert len(normalized['conversation']['dialog']) == 3
    assert [event['type'] for event in normalized['action_trace']] == [
        'call_started',
        'operator_steer_applied',
        'call_wrapped',
    ]
    assert normalized['latency_evidence']['within_budget'] == 1
    assert normalized['latency_evidence']['over_budget'] == 1
    assert 'Full-duplex media and barge-in are not proven by this example.' in normalized['runtime_caveats']
    assert normalized['provenance']['source_repo'] == 'agonza1/agentic-contact-center'


def test_build_assert_run_request_uses_canonical_evidence_contract():
    scenario = _scenario()
    normalized = normalize_acc_run(_acc_payload(), scenario=scenario)

    request = build_assert_run_request(
        normalized,
        scenario=scenario,
        user_id='alberto',
        project_id='acc-cluecon',
    )

    assert request.spec_ref.spec_id == 'agentic-contact-center/cancellation-rescue'
    assert request.spec_ref.spec_kind == 'scenario'
    assert request.evidence.transcript is not None
    assert request.evidence.transcript.inline_data.startswith('Caller:')
    assert request.evidence.conversation is not None
    assert request.evidence.action_trace is not None
    assert request.evidence.final_state is not None
    assert request.evidence.assert_bundle is not None
    assert len(request.evidence.additional_artifacts) == 2
    assert request.evidence.provenance['call_id'] == 'demo-call-98'
    assert request.runtime_config.invocation_target.entrypoint == '/api/assert/runs'
    assert request.runtime_config.scenario_overrides['required_actions'] == scenario['required_actions']
    assert request.platform_metadata.user_id == 'alberto'
    assert request.platform_metadata.project_id == 'acc-cluecon'
    assert 'acc_http_scripted_fixture' in request.platform_metadata.labels


def test_non_terminal_acc_payload_does_not_claim_completion():
    payload = _acc_payload()
    payload['outcome'] = 'in_progress'
    payload['call']['flowState'] = 'policy_hold'

    normalized = normalize_acc_run(payload, scenario=_scenario())

    assert normalized['final_state']['complete'] is False
    assert normalized['final_state']['flow_state'] == 'policy_hold'
