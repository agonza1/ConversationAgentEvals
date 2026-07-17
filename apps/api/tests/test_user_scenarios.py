from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.benchmark_service import get_suite, list_suites, run_scenario, simulate_scenario
from app.schemas.benchmarks import BenchmarkRunRequest
from app.services.user_scenario_store import (
    USER_SCENARIOS_SUITE_ID,
    configure_store_path,
    default_store_path,
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


def test_delete_user_scenario_removes_it_from_store_and_benchmark_catalog(tmp_path: Path):
    create = client.post(
        '/api/scenarios',
        json={
            'title': 'Temporary scenario',
            'simulated_user_prompt': SAMPLE_PROMPT,
            'expected_output': SAMPLE_EXPECTED,
            'description': SAMPLE_PROMPT,
        },
    )
    assert create.status_code == 200, create.text
    scenario_id = create.json()['id']

    deleted = client.delete(f'/api/scenarios/{scenario_id}')

    assert deleted.status_code == 204, deleted.text
    assert client.get(f'/api/scenarios/{scenario_id}').status_code == 404
    assert all(item['id'] != scenario_id for item in client.get('/api/scenarios').json()['scenarios'])
    suite = get_suite(USER_SCENARIOS_SUITE_ID)
    assert suite is not None
    assert all(item['id'] != scenario_id for item in suite['scenarios'])

    configure_store_path(tmp_path / 'user_scenarios.json')
    assert client.get(f'/api/scenarios/{scenario_id}').status_code == 404


def test_delete_scenario_rejects_built_in_and_unknown_ids():
    built_in = client.delete('/api/scenarios/billing-address-change')
    unknown = client.delete('/api/scenarios/does-not-exist')

    assert built_in.status_code == 404
    assert built_in.json()['detail'] == 'User-created scenario not found'
    assert unknown.status_code == 404


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


def test_starter_sample_transcript_uses_caller_facing_language():
    suite = get_suite('call-center-voice-ai')
    assert suite is not None
    scenario = next(item for item in suite['scenarios'] if item['id'] == 'billing-address-change')
    transcript = scenario['sample_transcript']

    assert 'I moved recently and need to update my billing address' in transcript
    assert "I'll greet caller" not in transcript
    assert "I'll collect new billing address" not in transcript
    assert 'Okay, continue.' not in transcript
    assert 'What is the new billing address?' in transcript

    report = run_scenario(
        BenchmarkRunRequest(
            suite_id='call-center-voice-ai',
            scenario_id='billing-address-change',
            transcript=transcript,
            action_trace=scenario['sample_action_trace'],
            final_state=scenario['sample_final_state'],
        )
    )
    assert report['verdict'] == 'pass'


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


def test_default_store_path_uses_storage_volume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv('USER_SCENARIOS_PATH', raising=False)
    path = default_store_path()
    assert path.name == 'user_scenarios.json'
    assert path.parent.name == 'storage'

    override = tmp_path / 'custom' / 'scenarios.json'
    monkeypatch.setenv('USER_SCENARIOS_PATH', str(override))
    assert default_store_path() == override


def test_legacy_store_migrates_into_storage_path(tmp_path: Path):
    legacy = tmp_path / 'legacy' / 'user_scenarios.json'
    durable = tmp_path / 'storage' / 'user_scenarios.json'
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                'version': 1,
                'suite_id': USER_SCENARIOS_SUITE_ID,
                'scenarios': [
                    {
                        'id': 'legacy-scenario',
                        'title': 'Legacy scenario',
                        'simulated_user_prompt': SAMPLE_PROMPT,
                        'expected_output': SAMPLE_EXPECTED,
                        'description': SAMPLE_PROMPT,
                    }
                ],
            }
        ),
        encoding='utf-8',
    )

    import app.services.user_scenario_store as store

    store._LEGACY_STORE_PATH = legacy
    configure_store_path(durable)
    listed = client.get('/api/scenarios')
    assert listed.status_code == 200, listed.text
    assert any(item['id'] == 'legacy-scenario' for item in listed.json()['scenarios'])
    assert durable.exists()
