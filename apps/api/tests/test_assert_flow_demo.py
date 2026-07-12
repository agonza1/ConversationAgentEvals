from __future__ import annotations

import json
from pathlib import Path


DEMO_PATH = Path(__file__).resolve().parents[3] / 'docs' / 'examples' / 'assert-flow-demo.json'


def test_assert_flow_demo_is_ordered_and_connected():
    demo = json.loads(DEMO_PATH.read_text())

    assert demo['flow_order'] == [
        'natural_language_requirements',
        'behavior_taxonomy',
        'generated_test_set',
        'target_execution',
        'judged_results',
    ]

    requirements = demo['natural_language_requirements']
    taxonomy = demo['behavior_taxonomy']
    test_set = demo['generated_test_set']
    execution = demo['target_execution']['input_artifacts']
    results = demo['judged_results']

    assert test_set['suite_id'] == 'telehealth-agent'
    assert test_set['scenario_id'] == 'medication-refill-routing'
    assert test_set['scenario_contract']['expected_evidence'] == ['transcript', 'action_trace', 'final_state']

    must_do = set(requirements['must_do'])
    taxonomy_requirements = {item['requirement'] for item in taxonomy['required_actions']}
    completed_actions = set(results['completed_actions'])
    executed_actions = {item['action'] for item in execution['action_trace']}

    assert taxonomy_requirements.issubset(must_do)
    assert must_do == completed_actions
    assert completed_actions == executed_actions
    assert results['missing_actions'] == []
    assert results['forbidden_action_hits'] == []
    assert execution['final_state']['description'] == requirements['expected_final_state']

    transcript = execution['transcript']
    for action in requirements['must_do']:
        assert action in transcript
    for forbidden_action in requirements['must_not_do']:
        assert forbidden_action not in results['forbidden_action_hits']
