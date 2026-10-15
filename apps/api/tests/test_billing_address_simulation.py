from copy import deepcopy
import json

import pytest

from app.services.billing_address_simulation import (
    ADDRESS, CODE, CUSTOMER_NAME, SCENARIO, SUITE, BillingSimulation, parse_control, replay_evidence,
)


def ready_proposal(session='session-1'):
    sim = BillingSimulation('exec-test', session)
    sim.caller(f'My name is {CUSTOMER_NAME}. My verification code is {CODE}. Please update my billing address.')
    assert sim.execute('verify_customer', {'full_name': CUSTOMER_NAME, 'verification_code': CODE})['ok']
    sim.speak('What is your new billing address?')
    sim.caller(', '.join(ADDRESS.values()))
    result = sim.execute('prepare_address', deepcopy(ADDRESS))
    assert result['ok']
    sim.speak(result['read_back'] + '. Is that correct?')
    return sim, result['proposal_id']


def completed_simulation():
    sim, proposal = ready_proposal()
    sim.caller("Yes, that's correct.")
    assert sim.execute('commit_address', {'proposal_id': proposal})['ok']
    assert sim.execute('read_address', {})['address'] == ADDRESS
    sim.speak('Updated. Your next invoice will use this address; the amount is unchanged.')
    return sim


def test_complete_test_is_verified_and_context_is_separate():
    sim = completed_simulation()
    result = sim.assessment()
    assert result['label'] == 'Verified'
    assert result['context'] == 'simulation'
    assert all(result['checks'].values())
    assert result['tool_calls'] == 4
    assert sim.final_state()['complete'] is True
    assert sim.final_state()['billing_address'] == ADDRESS
    replay = replay_evidence(sim.evidence(), execution_run_id='exec-test', session_id='session-1')
    assert replay.assessment() == result
    assert replay.action_trace() == sim.action_trace()


@pytest.mark.parametrize('name,args', [('read_address', {}), ('commit_address', {'proposal_id': 'guess'}), ('prepare_address', ADDRESS)])
def test_no_account_access_before_verification(name, args):
    sim = BillingSimulation('e', 's')
    sim.caller('Please change my address')
    assert not sim.execute(name, args)['ok']
    assert sim.version == 0


@pytest.mark.parametrize('name,code,caller', [
    ('Someone Else', CODE, f'{CUSTOMER_NAME} {CODE}'),
    (CUSTOMER_NAME, '000000', f'{CUSTOMER_NAME} {CODE}'),
    (CUSTOMER_NAME, CODE, 'Please update my address'),
    (CUSTOMER_NAME, CODE, CUSTOMER_NAME),
    (CUSTOMER_NAME, CODE, f'{CUSTOMER_NAME} 12468101'),
])
def test_two_correct_caller_identifiers_required(name, code, caller):
    sim = BillingSimulation('e', 's'); sim.caller(caller)
    assert not sim.execute('verify_customer', {'full_name': name, 'verification_code': code})['ok']
    assert sim.assessment()['status'] == 'unverified'


@pytest.mark.parametrize('confirmation', ['No', 'Yes but use Dallas instead', "Yes, don't change it", 'Maybe', 'Yes tomorrow', ''])
def test_negative_or_ambiguous_confirmation_cannot_commit(confirmation):
    sim, proposal = ready_proposal()
    sim.caller(confirmation or '...')
    assert not sim.execute('commit_address', {'proposal_id': proposal})['ok']
    assert sim.version == 0


def test_cannot_commit_before_later_confirmation_or_without_full_readback():
    sim, proposal = ready_proposal()
    assert not sim.execute('commit_address', {'proposal_id': proposal})['ok']
    sim.proposal['read_back_turn'] = None
    sim.caller('Yes')
    assert not sim.execute('commit_address', {'proposal_id': proposal})['ok']


def test_confirmation_cannot_be_reused_after_another_caller_turn():
    sim, proposal = ready_proposal(); sim.caller('Yes'); sim.caller('Yes')
    assert not sim.execute('commit_address', {'proposal_id': proposal})['ok']


def test_proposals_do_not_cross_sessions_and_duplicate_commit_does_not_mutate():
    one, proposal = ready_proposal('one'); two, _ = ready_proposal('two')
    two.caller('Yes')
    assert not two.execute('commit_address', {'proposal_id': proposal})['ok']
    one.caller('Yes')
    assert one.execute('commit_address', {'proposal_id': proposal})['ok']
    assert not one.execute('commit_address', {'proposal_id': proposal})['ok']
    assert one.version == 1 and two.version == 0


@pytest.mark.parametrize('changes', [{'postal_code': 'bad'}, {'street': ''}, {'city': 'Dallas'}, {'extra': 'bad'}])
def test_invalid_or_not_supplied_address_is_not_prepared(changes):
    sim, _ = ready_proposal()
    assert not sim.execute('prepare_address', {**ADDRESS, **changes})['ok']
    assert sim.version == 0


def test_tool_request_and_spoken_success_are_not_execution():
    sim, _ = ready_proposal(); sim.caller('Yes')
    sim.speak('Updated. Your next invoice will use the new address and the amount is unchanged.')
    assert sim.assessment()['status'] == 'unverified'


def test_update_without_stored_state_read_or_final_invoice_explanation_is_incomplete():
    sim, proposal = ready_proposal(); sim.caller('Yes')
    sim.execute('commit_address', {'proposal_id': proposal})
    assert sim.assessment()['status'] == 'unverified'
    sim.execute('read_address', {})
    assert sim.assessment()['status'] == 'unverified'


def test_wrong_final_address_cannot_verify():
    sim = completed_simulation(); sim.address['city'] = 'Dallas'
    assert sim.assessment()['status'] == 'unverified'


def test_negated_or_hallucinated_invoice_disclosure_cannot_verify():
    sim = completed_simulation()
    sim.events[-1]['text'] = "The next invoice will not use your new address; the amount is unchanged."
    assert sim.assessment()['checks']['invoice_effect_explained'] is False


@pytest.mark.parametrize('mutation', ['identity', 'receipt', 'order', 'extra'])
def test_replay_rejects_tampered_or_cross_run_evidence(mutation):
    evidence = completed_simulation().evidence()
    if mutation == 'identity': evidence['session_id'] = 'other'
    elif mutation == 'receipt': evidence['events'][1]['result']['ok'] = False
    elif mutation == 'order': evidence['events'][0], evidence['events'][1] = evidence['events'][1], evidence['events'][0]
    else: evidence['status'] = 'verified'
    with pytest.raises(ValueError):
        replay_evidence(evidence, execution_run_id='exec-test', session_id='session-1')


def test_tool_budget_and_allowlist():
    sim = BillingSimulation('e', 's'); sim.caller('Hi')
    for _ in range(5): assert not sim.execute('delete_all_accounts', {})['ok']
    with pytest.raises(ValueError, match='budget'): sim.execute('read_address', {})


@pytest.mark.parametrize('value', ['Done!', '```json\n{}\n```', '[]', '{"type":"speech","text":""}', '{"type":"speech","text":"Hi","verified":true}', 'x' * 4001])
def test_control_json_is_strict_and_does_not_infer_tools_from_speech(value):
    with pytest.raises(ValueError): parse_control(value)


def test_fresh_simulation_does_not_inherit_another_run():
    completed_simulation()
    new = BillingSimulation('another-run', 'session-1')
    assert not new.verified and new.version == 0 and new.events == []


def test_spoken_identifier_and_address_variants():
    sim = BillingSimulation('e', 's')
    sim.caller('Jordan Lee, two four six eight one zero')
    assert sim.execute('verify_customer', {'full_name': CUSTOMER_NAME, 'verification_code': CODE})['ok']
    sim.caller('Two hundred fourteen Maple Street, Apartment five B, Austin Texas seven eight seven zero four')
    assert sim.execute('prepare_address', ADDRESS)['ok']


def test_partial_street_number_cannot_be_taken_from_another_number():
    sim = BillingSimulation('e', 's'); sim.caller(f'{CUSTOMER_NAME} {CODE}')
    sim.execute('verify_customer', {'full_name': CUSTOMER_NAME, 'verification_code': CODE})
    sim.caller('1214 Maple Street, Apartment 5B, Austin, Texas 78704')
    assert not sim.execute('prepare_address', ADDRESS)['ok']


def test_benchmark_report_includes_context_without_bypassing_semantic_review():
    from app.services.benchmark_service import run_scenario
    sim = completed_simulation()
    report = run_scenario({'suite_id': SUITE, 'scenario_id': SCENARIO,
                           'transcript': '\n'.join(e['text'] for e in sim.events if 'text' in e),
                           'action_trace': sim.action_trace(), 'final_state': sim.final_state()},
                          persist_artifacts=False, synthetic=True, billing_simulation=sim)
    assert report['operational_verification']['label'] == 'Verified'
    assert report['semantic_review_required'] is True
    assert report['verdict'] == 'needs_review'
    assert report['evaluation_scope']['synthetic'] is True
    assert report['vcon_analysis']['body']['operational_verification']['status'] == 'verified'
    from app.services.vcon_evidence import decode_evidence
    body = decode_evidence(report['ietf_vcon_export'])
    assert len(body['tool_events']) == 4
    assert body['synthetic'] is True
    assert body['state_snapshots'][-1]['state']['complete'] is True


def test_imported_success_flag_cannot_create_internal_verification():
    from app.services.benchmark_service import run_scenario
    report = run_scenario({'suite_id': SUITE, 'scenario_id': SCENARIO, 'transcript': 'Agent: Done.',
                           'operational_verification': completed_simulation().assessment()}, persist_artifacts=False)
    assert 'operational_verification' not in report
    assert report['verdict'] != 'pass'


@pytest.mark.parametrize('include_proof', [True, False])
def test_voice_execution_retains_real_mock_receipts_not_text_claims(monkeypatch, include_proof):
    import asyncio
    from app.services import execution_runner as runner
    from app.schemas.execution import ExecutionRunCreateRequest
    from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
    sim = completed_simulation()

    class Transport:
        transport_id = 'pipecat_small_webrtc'
        runtime = {}
        graphs = {}
        def __init__(self, **kwargs): pass
        async def connect(self, session_id, **kwargs): pass
        async def start_recording(self, session_id): pass
        async def disconnect(self, session_id, **kwargs): pass
        async def run_duplex_session(self, session_id, **kwargs):
            assert kwargs['billing_simulation'] is True
            return {'session_id': session_id, 'status': 'completed', 'termination_reason': 'max_turn_pairs',
                    'proof': {'billing_simulation': sim.evidence()} if include_proof else {}, 'turns': []}
        def transcription_turns(self, session_id):
            return [TranscriptionTurn(turn_index=i + 1, speaker='Caller' if e['type'] == 'caller' else 'Agent', text=e['text'])
                    for i, e in enumerate(e for e in sim.events if 'text' in e)]
        def recording_handle(self, session_id): return AudioRecordingHandle(uri='memory://test.wav', sha256='abc', duration_ms=10)
        def latency_marks(self, session_id): return []
        def session_proof(self, session_id): return {'session_id': session_id}

    monkeypatch.setattr(runner, 'ReferencePipecatAgentTransport', Transport)
    monkeypatch.setattr(runner, 'ReferenceMediaServices', lambda _config: None)
    monkeypatch.setattr(runner, 'resolve_reference_completion_provider', lambda _model: None)
    payload = ExecutionRunCreateRequest(suite_id=SUITE, scenario_ids=[SCENARIO], mode='pipecat_webrtc',
                                        audio_transport='pipecat_small_webrtc', tester_id='pipecat_tester')
    result = asyncio.run(runner._execute_pipecat_webrtc(execution_run_id='exec-test', conversation_id='session-1',
                        suite_id=SUITE, scenario_id=SCENARIO, payload=payload))
    assert result['synthetic'] is include_proof
    assert len(result['action_trace']) == (4 if include_proof else 0)
    assert result['final_state']['complete'] is include_proof
    if include_proof:
        assert result['evaluation_report']['operational_verification']['label'] == 'Verified'
        assert result['final_state']['simulation_evidence'] == sim.evidence()
    else:
        assert 'operational_verification' not in result['evaluation_report']
