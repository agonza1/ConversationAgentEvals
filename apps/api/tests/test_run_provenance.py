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
