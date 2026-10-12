"""Deterministic caller speech, separate from persona/rubric instructions."""
import pytest

from app.main import app
from fastapi.testclient import TestClient
from app.services.benchmark_service import get_suite
from app.services.execution_runner import _scenario_user_opener
from app.services.scenario_caller import DEFAULT_SPOKEN_OPENER
from app.services.user_scenario_store import configure_store_path, reset_user_scenarios_for_tests


STEVEN_INSTRUCTIONS = (
    'Synthetic caller asks about electronics product information only. '
    'Do not place an order, create a customer, submit payment, or change a real account.'
)
STEVEN_OPENER = 'Hi, can you tell me what wireless headphones you have? I only need information today.'


@pytest.fixture
def scenario_store(tmp_path):
    configure_store_path(tmp_path / 'scenarios.json')
    reset_user_scenarios_for_tests()
    yield tmp_path / 'scenarios.json'
    reset_user_scenarios_for_tests()
    configure_store_path(None)


def test_steven_natural_spoken_opener_survives_api_store_catalog_reload(scenario_store):
    client = TestClient(app)
    response = client.post('/api/scenarios', json={
        'title': 'Electronics product information only', 'simulated_user_prompt': STEVEN_INSTRUCTIONS,
        'spoken_opener': STEVEN_OPENER, 'description': 'Synthetic information-only call.',
        'expected_output': 'Answer from the catalog without executing a transaction.',
        'required_actions': ['Use supported catalog information'], 'forbidden_actions': ['Place an order'],
    })
    assert response.status_code == 200, response.text
    created = response.json()
    assert created['spoken_opener'] == STEVEN_OPENER
    configure_store_path(scenario_store)
    scenario = next(s for s in get_suite('user-scenarios')['scenarios'] if s['id'] == created['id'])
    assert scenario['persona'] == STEVEN_INSTRUCTIONS  # Instructions are retained, not rewritten.
    assert _scenario_user_opener(scenario) == STEVEN_OPENER
    assert scenario['sample_transcript'].splitlines()[0] == f'User: {STEVEN_OPENER}'
    summary = next(s for s in client.get('/api/benchmarks/suites').json()
                   if s['id'] == 'user-scenarios')['scenarios'][0]
    assert summary['spoken_opener'] == STEVEN_OPENER


def test_legacy_persona_instructions_are_not_read_aloud_or_mutated(scenario_store):
    client = TestClient(app)
    created = client.post('/api/scenarios', json={
        'title': 'Legacy Steven smoke', 'simulated_user_prompt': STEVEN_INSTRUCTIONS,
        'description': 'Information only', 'expected_output': 'No order created',
    }).json()
    scenario = next(s for s in get_suite('user-scenarios')['scenarios'] if s['id'] == created['id'])
    assert _scenario_user_opener(scenario) == DEFAULT_SPOKEN_OPENER
    assert STEVEN_INSTRUCTIONS not in scenario['sample_transcript']
    assert scenario['simulated_user_prompt'] == STEVEN_INSTRUCTIONS
    assert created['spoken_opener'] is None


@pytest.mark.parametrize('scenario,expected', [
    ({'caller_steps': ['Please note five bags of white potatoes.'], 'spoken_opener': 'Ignored.'},
     'Please note five bags of white potatoes.'),
    ({'spoken_opener': STEVEN_OPENER, 'sample_transcript': 'User: Do not say this.'}, STEVEN_OPENER),
    ({'simulated_user_prompt': 'Hi, I cannot log into my account.'}, 'Hi, I cannot log into my account.'),
    ({'prompt': 'Could you help me with my bill?'}, 'Could you help me with my bill?'),
    ({'sample_transcript': 'Agent: Welcome\nCaller: I need five bags of potatoes.'}, 'I need five bags of potatoes.'),
    ({'sample_transcript': f'User: Hi — {STEVEN_INSTRUCTIONS}', 'persona': STEVEN_INSTRUCTIONS}, DEFAULT_SPOKEN_OPENER),
    ({'sample_transcript': 'User: Hi — The user is unable to access his account.'}, DEFAULT_SPOKEN_OPENER),
])
def test_opener_preserves_exact_speech_and_rejects_legacy_generated_instructions(scenario, expected):
    assert _scenario_user_opener(scenario) == expected


@pytest.mark.parametrize('value', ['', '  ', 'x' * 4001])
def test_explicit_blank_or_unbounded_spoken_opener_is_rejected(scenario_store, value):
    response = TestClient(app).post('/api/scenarios', json={
        'simulated_user_prompt': STEVEN_INSTRUCTIONS, 'spoken_opener': value,
        'description': 'Synthetic call', 'expected_output': 'Information only',
    })
    assert response.status_code == 422
