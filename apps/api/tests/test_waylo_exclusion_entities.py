"""Excluded backend items still need independent spoken-recognition evidence."""
from copy import deepcopy

import pytest

from app.services.waylo_assessment import assess_speech, compare_notes
from app.services.waylo_mike_cases import WAYLO_MIKE_SUITE


def speech_diagnostics(case_id, first_receipt=None):
    case = next(case for case in WAYLO_MIKE_SUITE['scenarios'] if case['id'] == case_id)
    receipts = list(case['caller_steps'])
    if first_receipt is not None:
        receipts[0] = first_receipt
    capture = {'transcripts': [
        {'id': f'asr-{index}', 'role': 'user', 'text': text}
        for index, text in enumerate(receipts, 1)
    ]}
    references = [
        {'event_type': 'tester.audio.sent', 'turn_id': f'caller-{index}',
         'reference_text': text, 'intended_samples': 100, 'accepted_samples': 100}
        for index, text in enumerate(case['caller_steps'], 1)
    ]
    return case, capture, references, assess_speech(
        capture, references, case['waylo_test']['expected_entities_by_turn'])


@pytest.mark.parametrize(('case_id', 'spoken_entities'), [
    ('mike-unavailable', [
        {'product_name': 'Old Pears', 'quantity': 2, 'unit': 'boxes'},
        {'product_name': 'dragonfruit', 'quantity': 3, 'unit': 'boxes'},
    ]),
    ('mike-lookup-failure', [
        {'product_name': 'potatoes', 'quantity': 5, 'unit': 'bags'},
    ]),
])
def test_excluded_items_have_spoken_checks_not_backend_expectations(case_id, spoken_entities):
    case, _, _, diagnostics = speech_diagnostics(case_id)
    assert case['waylo_test']['expected_requests'] == []
    assert case['waylo_test']['expected_entities_by_turn'] == [spoken_entities, []]
    assert diagnostics['status'] == 'needs_review'
    first, final = diagnostics['comparisons']
    assert [entity['expected'] for entity in first['critical_entities']] == spoken_entities
    assert all(entity['recognition'] == {'product': True, 'quantity': True, 'unit': True}
               for entity in first['critical_entities'])
    assert final['critical_entities'] == []
    assert first['scoreable'] is False
    assert first['agent_interpretation'] == 'requires_tool_and_backend_comparison'
    backend = compare_notes([], case['waylo_test']['expected_requests'])
    assert backend['status'] == 'observed_agreement'
    assert backend['resolution_verified'] is False


@pytest.mark.parametrize(('case_id', 'receipt', 'entity_index', 'field'), [
    ('mike-unavailable', 'two boxes of apples and three boxes of dragonfruit', 0, 'product'),
    ('mike-unavailable', 'two boxes of old pears and three boxes of apples', 1, 'product'),
    ('mike-unavailable', 'one box of old pears and three boxes of dragonfruit', 0, 'quantity'),
    ('mike-unavailable', 'two boxes of old pears and four boxes of dragonfruit', 1, 'quantity'),
    ('mike-unavailable', 'two bags of old pears and three bags of dragonfruit', 0, 'unit'),
    ('mike-lookup-failure', 'five bags of carrots', 0, 'product'),
    ('mike-lookup-failure', 'four bags of potatoes', 0, 'quantity'),
    ('mike-lookup-failure', 'five boxes of potatoes', 0, 'unit'),
])
def test_empty_backend_does_not_hide_spoken_entity_errors(case_id, receipt, entity_index, field):
    case, _, _, diagnostics = speech_diagnostics(case_id, receipt)
    first = diagnostics['comparisons'][0]
    assert first['critical_entities'][entity_index]['recognition'][field] is False
    assert diagnostics['status'] == 'needs_review'
    assert first['scoreable'] is False
    assert first['receiver_drop'] == 'unknown'
    backend = compare_notes([], case['waylo_test']['expected_requests'])
    assert backend['status'] == 'observed_agreement'
    assert backend['resolution_verified'] is False


@pytest.mark.parametrize('case_id', ['mike-unavailable', 'mike-lookup-failure'])
def test_exclusion_checks_do_not_force_alignment_for_split_asr(case_id):
    case, capture, references, _ = speech_diagnostics(case_id)
    extra = deepcopy(capture['transcripts'][0])
    extra['id'] = 'split-asr'
    capture['transcripts'].append(extra)
    diagnostics = assess_speech(capture, references, case['waylo_test']['expected_entities_by_turn'])
    assert diagnostics['status'] == 'unknown'
    assert diagnostics['comparisons'] == []
