import pytest

from app.services.run_provenance import build_run_provenance


def _text_provenance(*, agent: dict, model_name: str | None = None):
    return build_run_provenance(
        agent=agent,
        agent_target=agent['target'],
        tester_id='scenario_simulator',
        executor_id='local_async_runner',
        mode='text_callable',
        model_name=model_name,
    )


def test_ollama_execution_is_local_and_advertises_latency_marks():
    provenance = _text_provenance(
        agent={'id': 'ollama-agent', 'target': 'openai_codex', 'channel': 'text'},
        model_name='ollama/qwen3:8b',
    )

    assert provenance.target_environment == 'local'
    assert provenance.evidence_capabilities == [
        'transcript',
        'current_run_response',
        'latency_marks',
        'provider_model_response',
    ]


@pytest.mark.parametrize(
    'base_url',
    [
        'http://localhost:11434',
        'http://localhost.:11434',
        'http://127.0.0.1:11434',
        'http://[::1]:11434',
        'http://192.168.1.20:11434',
        'http://10.0.0.5:11434',
        'http://172.16.4.2:11434',
        'http://169.254.1.5:11434',
        'http://[fc00::20]:11434',
        'http://[fe80::1]:11434',
    ],
)
def test_configured_provider_private_and_loopback_urls_are_local(base_url: str):
    provenance = build_run_provenance(
        agent={'id': 'configured-agent', 'target': 'openai_codex', 'channel': 'text'},
        agent_target='openai_codex',
        tester_id='scenario_simulator',
        executor_id='local_async_runner',
        mode='text_callable',
        model_name='gpt-compatible',
        completion_provider_id='openai_compatible',
        completion_provider_base_url=base_url,
    )

    assert provenance.target_environment == 'local'


@pytest.mark.parametrize(
    ('provider_id', 'model_name', 'base_url'),
    [
        ('ollama', 'ollama/qwen3:8b', 'https://ollama.example.com'),
        ('openai_compatible', 'gpt-compatible', 'https://api.example.com/v1'),
        ('openai_compatible', 'gpt-compatible', 'not a valid URL'),
        ('openai_compatible', 'gpt-compatible', ''),
    ],
)
def test_configured_provider_public_remote_or_invalid_urls_are_external(
    provider_id: str,
    model_name: str,
    base_url: str,
):
    provenance = build_run_provenance(
        agent={'id': 'configured-agent', 'target': 'openai_codex', 'channel': 'text'},
        agent_target='openai_codex',
        tester_id='scenario_simulator',
        executor_id='local_async_runner',
        mode='text_callable',
        model_name=model_name,
        completion_provider_id=provider_id,
        completion_provider_base_url=base_url,
    )

    assert provenance.target_environment == 'external_public'


def test_loopback_http_endpoint_execution_is_local():
    provenance = _text_provenance(
        agent={
            'id': 'local-http-agent',
            'target': 'http_endpoint',
            'channel': 'text',
            'connection': {'endpoint_url': 'http://127.0.0.1:9000/chat'},
        },
    )

    assert provenance.target_environment == 'local'


@pytest.mark.parametrize(
    'endpoint_url',
    [
        'http://192.168.1.20/chat',
        'http://10.0.0.5/chat',
        'http://172.31.4.2/chat',
        'http://[fd00::5]/chat',
    ],
)
def test_private_http_endpoint_execution_is_local(endpoint_url: str):
    provenance = _text_provenance(
        agent={
            'id': 'private-http-agent',
            'target': 'http_endpoint',
            'channel': 'text',
            'connection': {'endpoint_url': endpoint_url},
        },
    )

    assert provenance.target_environment == 'local'


def test_public_http_endpoint_execution_is_external():
    provenance = _text_provenance(
        agent={
            'id': 'public-http-agent',
            'target': 'http_endpoint',
            'channel': 'text',
            'connection': {'endpoint_url': 'https://support.example.com/chat'},
        },
    )

    assert provenance.target_environment == 'external_public'


def test_saved_replay_advertises_latency_marks():
    provenance = build_run_provenance(
        agent={'id': 'saved-run', 'target': 'offline_acc_fixture', 'channel': 'text'},
        agent_target='offline_acc_fixture',
        tester_id='fixture_replay',
        executor_id='evidence_replay',
        mode='text_callable',
    )

    assert provenance.evidence_capabilities == [
        'transcript',
        'latency_marks',
        'saved_artifact_replay',
    ]
