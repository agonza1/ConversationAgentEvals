from app.services.benchmark_evaluator import (
    evaluate_benchmark,
    parse_action_trace,
    score_final_state_correctness,
    score_forbidden_action_avoidance,
    score_required_action_execution,
    score_task_completion,
)


def test_parse_action_trace_normalizes_common_tool_shapes():
    trace = {
        'tool_calls': [
            {
                'tool': 'crm.lookup_customer',
                'arguments': {'email': 'buyer@example.com', 'unused': True},
                'status': 'succeeded',
            },
            {
                'tool_call': {
                    'function': 'calendar.book_meeting',
                    'parameters': {'date': '2026-06-01', 'timezone': 'America/New_York'},
                    'outcome': 'success',
                }
            },
            'notify_user',
        ]
    }

    actions = parse_action_trace(trace)

    assert [action.name for action in actions] == [
        'crm.lookup_customer',
        'calendar.book_meeting',
        'notify_user',
    ]
    assert actions[0].arguments['email'] == 'buyer@example.com'
    assert actions[1].arguments['timezone'] == 'America/New_York'
    assert actions[2].arguments == {}


def test_required_action_execution_scores_argument_subset_matches():
    trace = [
        {
            'name': 'crm.lookup_customer',
            'args': {'email': 'buyer@example.com', 'include_history': True},
            'status': 'success',
        },
        {
            'action': 'calendar.book_meeting',
            'input': {'date': '2026-06-01', 'attendee': 'buyer@example.com'},
            'status': 'success',
        },
    ]

    result = score_required_action_execution(
        trace,
        [
            {'name': 'crm.lookup_customer', 'args': {'email': 'buyer@example.com'}},
            {'name': 'calendar.book_meeting', 'args': {'date': '2026-06-01'}},
            {'name': 'email.send_summary'},
        ],
    )

    assert result.score == 67
    assert result.passed is False
    assert result.matched == 2
    assert result.missing == [{'name': 'email.send_summary'}]
    assert result.evidence[0]['name'] == 'crm.lookup_customer'


def test_required_action_execution_does_not_credit_failed_tool_call_without_expected_failure():
    trace = [
        {'name': 'support.create_ticket', 'status': 'failed'},
        {'name': 'notify_user', 'status': 'completed'},
    ]

    result = score_required_action_execution(
        trace,
        ['support.create_ticket', 'notify_user'],
    )

    assert result.score == 50
    assert result.passed is False
    assert result.missing == ['support.create_ticket']
    assert result.evidence == [{'name': 'notify_user', 'status': 'completed'}]


def test_required_action_execution_allows_explicit_failed_status_when_required():
    result = score_required_action_execution(
        [{'name': 'payment.retry', 'status': 'failed'}],
        [{'name': 'payment.retry', 'status': 'failed'}],
    )

    assert result.score == 100
    assert result.passed is True


def test_forbidden_action_avoidance_fails_on_any_matching_action():
    trace = [
        {'name': 'crm.lookup_customer'},
        {'name': 'refund.issue', 'args': {'amount': 500}},
    ]

    result = score_forbidden_action_avoidance(trace, ['refund.issue', 'account.delete'])

    assert result.score == 0
    assert result.passed is False
    assert result.violations == [{'name': 'refund.issue', 'arguments': {'amount': 500}}]


def test_final_state_correctness_scores_path_assertions():
    final_state = {
        'ticket': {'status': 'closed', 'priority': 'normal'},
        'customer': {'consent': {'sms': True}},
        'messages': [{'channel': 'email'}],
    }

    result = score_final_state_correctness(
        final_state,
        [
            {'path': 'ticket.status', 'equals': 'closed'},
            {'path': 'customer.consent.sms', 'equals': True},
            {'path': 'messages.0.channel', 'equals': 'sms'},
        ],
    )

    assert result.score == 67
    assert result.passed is False
    assert result.matched == 2
    assert result.missing == [{'path': 'messages.0.channel', 'expected': 'sms', 'actual': 'email'}]


def test_task_completion_uses_explicit_final_state_criteria_before_inference():
    result = score_task_completion(
        [],
        {'status': 'completed', 'handoff': {'created': False}},
        {'final_state': {'handoff.created': True}},
    )

    assert result.score == 0
    assert result.passed is False
    assert result.missing == [{'path': 'handoff.created', 'expected': True, 'actual': False}]


def test_task_completion_infers_successful_terminal_state():
    result = score_task_completion([], {'result': {'status': 'succeeded'}})

    assert result.score == 100
    assert result.passed is True
    assert result.evidence == [{'actual': 'succeeded'}]


def test_task_completion_accepts_complete_terminal_state():
    result = score_task_completion([], {'complete': True})

    assert result.score == 100
    assert result.passed is True
    assert result.evidence == [{'actual': True}]


def test_task_completion_supports_expected_failure_completion():
    result = score_task_completion([], {'status': 'failed'}, {'completed': False})

    assert result.score == 100
    assert result.passed is True


def test_evaluate_benchmark_returns_all_four_scores_and_overall_average():
    evaluation = evaluate_benchmark(
        action_trace=[
            {'name': 'crm.lookup_customer', 'args': {'email': 'buyer@example.com'}},
            {'name': 'calendar.book_meeting', 'args': {'date': '2026-06-01'}},
        ],
        final_state={'status': 'completed', 'meeting': {'booked': True}},
        task_completion={'completed': True},
        required_actions=[
            {'name': 'crm.lookup_customer', 'args': {'email': 'buyer@example.com'}},
            {'name': 'calendar.book_meeting'},
        ],
        forbidden_actions=['refund.issue'],
        expected_final_state={'meeting.booked': True},
    )

    assert evaluation.task_completion.score == 100
    assert evaluation.required_action_execution.score == 100
    assert evaluation.forbidden_action_avoidance.score == 100
    assert evaluation.final_state_correctness.score == 100
    assert evaluation.overall_score == 100
