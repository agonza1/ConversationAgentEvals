import pytest

from app.services.design_enforcement import evaluate_design


def scenario(labels=(), requirements=()):
    return {'deterministic_checks': [{'id': f'check-{i}', 'label': label} for i, label in enumerate(labels)],
            'evidence_requirements': list(requirements), 'target_behavior_id': 'refund',
            'behaviors': [{'id': 'refund', 'label': 'No refunds', 'kind': 'forbidden'},
                          {'id': 'other', 'label': 'Other', 'kind': 'required'}]}


def test_supported_checks_pass_with_citations_without_proving_policy():
    design = scenario(['transcript_present', 'action_trace_present', 'final_state_present', 'final_state_complete',
                       'tool_succeeded:lookup', 'transcript_contains:account'], ['transcript', 'action_trace', 'final_state'])
    result = evaluate_design(design, {'action_trace': [{'name': 'lookup', 'status': 'completed'}],
                                     'final_state': {'complete': True}}, 'Agent: Your account is verified.')
    assert all(row['status'] == 'pass' and row['citations'] for row in result['programmatic_check_results'])
    assert all(row['status'] == 'pass' for row in result['evidence_requirement_results'])
    assert result['design_enforcement']['blocked'] is False
    assert result['behavior_results'][0]['status'] == 'insufficient_evidence'
    assert len(result['behavior_results']) == 1


@pytest.mark.parametrize('payload', [{}, {'action_trace': [], 'final_state': {}}, {'action_trace': 'bad', 'final_state': True}])
def test_missing_or_malformed_evidence_cannot_pass(payload):
    result = evaluate_design(scenario(['final_state_complete', 'tool_succeeded:lookup'], ['action_trace', 'final_state']), payload, '')
    assert all(row['status'] == 'insufficient_evidence' for row in result['programmatic_check_results'])
    assert result['design_enforcement']['blocked']
    assert not result['design_enforcement']['failed']


def test_unknown_prose_is_not_executed_or_silently_ignored():
    result = evaluate_design(scenario(['__import__("os").system("echo bad")'], ['audio proves intent']), {}, 'hello')
    assert result['design_enforcement']['blocked']
    assert result['programmatic_check_results'][0]['supported'] is False
    assert result['evidence_requirement_results'][0]['supported'] is False


@pytest.mark.parametrize('status', ['requested', 'pending', 'running', 'unknown', 'observed', None])
def test_tool_invocations_without_terminal_status_are_insufficient(status):
    result = evaluate_design(scenario(['tool_succeeded:lookup']), {'action_trace': [{'name': 'lookup', 'status': status}]}, '')
    assert result['programmatic_check_results'][0]['status'] == 'insufficient_evidence'
    assert result['design_enforcement']['blocked'] is True


def test_failure_has_evidence_and_retry_can_satisfy_at_least_one_success():
    design = scenario(['tool_succeeded:lookup', 'final_state_complete', 'transcript_contains:verified'])
    payload = {'action_trace': [{'tool': 'lookup', 'status': 'failed'}], 'final_state': {'complete': False}}
    result = evaluate_design(design, payload, 'Agent: The account could not be found.')
    assert all(row['status'] == 'fail' and row['citations'] for row in result['programmatic_check_results'])
    payload['action_trace'].append({'tool': 'lookup', 'status': 'completed'})
    assert evaluate_design(design, payload, '')['programmatic_check_results'][0]['status'] == 'pass'


def test_evidence_alternatives_and_id_linked_violation():
    design = scenario([], ['final_state_or_action_trace'])
    result = evaluate_design(design, {'action_trace': [{'name': 'pay', 'behavior_id': 'refund', 'status': 'completed'}]}, '')
    assert result['evidence_requirement_results'][0]['status'] == 'pass'
    assert result['behavior_results'][0]['status'] == 'fail'
    assert result['behavior_results'][0]['citations'][0]['path'] == 'events[0]'


def test_observed_forbidden_action_is_not_confused_with_tool_success():
    result = evaluate_design(scenario(['tool_succeeded:pay']), {
        'action_trace': [{'name': 'pay', 'behavior_id': 'refund', 'status': 'observed'}]}, '')
    assert result['programmatic_check_results'][0]['status'] == 'insufficient_evidence'
    assert result['behavior_results'][0]['status'] == 'fail'


def test_tool_retry_citations_point_to_the_success_that_satisfied_the_check():
    trace = [{'name': 'lookup', 'status': 'observed'} for _ in range(3)]
    trace.append({'name': 'lookup', 'status': 'completed'})
    row = evaluate_design(scenario(['tool_succeeded:lookup']), {'action_trace': trace}, '')['programmatic_check_results'][0]
    assert row['status'] == 'pass'
    assert row['citations'][0]['path'] == 'events[3]'
    assert row['citations'][0]['text'] == 'lookup: completed'
