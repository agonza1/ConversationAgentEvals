from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.benchmark_service import get_suite, list_suites, simulate_scenario
from app.services.user_scenario_store import (
    USER_SCENARIOS_SUITE_ID,
    configure_store_path,
    reset_user_scenarios_for_tests,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path):
    configure_store_path(tmp_path / 'user_scenarios.json')
    reset_user_scenarios_for_tests()
    yield
    reset_user_scenarios_for_tests()
    configure_store_path(None)


SAMPLE_PROMPT = (
    'The user is unable to access his account. He recently changed his password, '
    'but when he tries to log in, the systems says that the password is incorrect.'
)
SAMPLE_EXPECTED = (
    'The agent gets customer information and any other relevant details, makes a report '
    'and tell the user that he will be transfered to another department.'
)


def test_create_and_list_scenarios_via_api():
    create = client.post(
        '/api/scenarios',
        json={
            'title': 'Account access issue',
            'simulated_user_prompt': SAMPLE_PROMPT,
            'expected_output': SAMPLE_EXPECTED,
            'description': SAMPLE_PROMPT,
        },
    )

    assert create.status_code == 200, create.text
    created = create.json()
    assert created['type'] == 'scenario'
    assert created['suite_id'] == USER_SCENARIOS_SUITE_ID
    assert created['simulated_user_prompt'] == SAMPLE_PROMPT
    assert created['expected_output'] == SAMPLE_EXPECTED
    assert created['description'] == SAMPLE_PROMPT
    assert created['title'] == 'Account access issue'

    listed = client.get('/api/scenarios')
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload['suite_id'] == USER_SCENARIOS_SUITE_ID
    assert len(payload['scenarios']) == 1
    assert payload['scenarios'][0]['id'] == created['id']

    detail = client.get(f"/api/scenarios/{created['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()['id'] == created['id']


def test_create_scenario_rejects_missing_fields():
    response = client.post(
        '/api/scenarios',
        json={'simulated_user_prompt': SAMPLE_PROMPT, 'expected_output': SAMPLE_EXPECTED},
    )

    assert response.status_code == 422


def test_created_scenario_is_selectable_in_benchmark_catalog_and_simulatable():
    create = client.post(
        '/api/scenarios',
        json={
            'simulated_user_prompt': SAMPLE_PROMPT,
            'expected_output': SAMPLE_EXPECTED,
            'description': SAMPLE_PROMPT,
        },
    )
    assert create.status_code == 200, create.text
    scenario_id = create.json()['id']

    suite_ids = {suite['id'] for suite in list_suites()}
    assert USER_SCENARIOS_SUITE_ID in suite_ids

    suite = get_suite(USER_SCENARIOS_SUITE_ID)
    assert suite is not None
    assert any(item['id'] == scenario_id for item in suite['scenarios'])
    scenario = next(item for item in suite['scenarios'] if item['id'] == scenario_id)
    assert scenario['persona'] == SAMPLE_PROMPT
    assert scenario['expected_final_state'] == SAMPLE_EXPECTED
    assert scenario['sample_transcript']

    catalog = client.get('/api/benchmarks/suites/user-scenarios/scenarios')
    assert catalog.status_code == 200, catalog.text
    assert any(item['id'] == scenario_id for item in catalog.json()['scenarios'])

    simulation = simulate_scenario(
        {
            'suite_id': USER_SCENARIOS_SUITE_ID,
            'scenario_id': scenario_id,
            'agent_profile': 'custom scenario mock agent',
        }
    )
    assert simulation['suite_id'] == USER_SCENARIOS_SUITE_ID
    assert simulation['scenario_id'] == scenario_id
    assert simulation['benchmark_report']['scenario_id'] == scenario_id
    assert 'custom scenario mock agent' in simulation['transcript']


def test_created_scenarios_persist_across_store_reload(tmp_path: Path):
    create = client.post(
        '/api/scenarios',
        json={
            'title': 'Persisted scenario',
            'simulated_user_prompt': SAMPLE_PROMPT,
            'expected_output': SAMPLE_EXPECTED,
            'description': 'Persisted description',
        },
    )
    assert create.status_code == 200, create.text
    scenario_id = create.json()['id']

    store_path = tmp_path / 'user_scenarios.json'
    assert store_path.exists()

    configure_store_path(store_path)
    reloaded = client.get(f'/api/scenarios/{scenario_id}')
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()['title'] == 'Persisted scenario'
