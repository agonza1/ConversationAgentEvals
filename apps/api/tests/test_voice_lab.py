from __future__ import annotations

from pathlib import Path

from app.services.voice_lab import (
    AgenticContactCenterAdapter,
    TranscriptInjectionAdapter,
    VoiceLabRunner,
    VoiceLabScenario,
    seeded_voice_lab_scenarios,
)


def test_contact_center_adapter_normalizes_proof_bundle(tmp_path, monkeypatch):
    adapter = AgenticContactCenterAdapter(
        repo_root=Path('/workspace/projects/agentic-contact-center'),
        artifact_dir=tmp_path,
    )
    adapter._cached_bundle_path = tmp_path / 'proof.json'

    bundle = {
        'generatedAt': '2026-06-12T12:00:00Z',
        'provider': 'local-test',
        'scripted': {
            'callId': 'demo-call-0001',
            'outcome': 'scripted_wrap_complete',
            'checkpoints': {
                'wrapped': {
                    'flowState': 'wrap',
                    'demoFallback': {'armed': False},
                    'operatorSteer': {'pending': False},
                    'pipecatFlow': {'script': {'completed': True}},
                    'transcript': [
                        {'speaker': 'caller', 'text': 'I want to cancel today.', 'timestamp': '2026-06-12T12:00:01Z'},
                        {'speaker': 'agent', 'text': 'I can review safe options.', 'timestamp': '2026-06-12T12:00:02Z'},
                    ],
                    'events': [
                        {'type': 'call_bootstrapped', 'at': '2026-06-12T12:00:00Z', 'detail': {'mode': 'mocked_telephony'}},
                    ],
                    'latencyMarks': [
                        {'stage': 'caller_turn_received', 'recordedAt': '2026-06-12T12:00:01Z', 'elapsedMs': 10, 'budgetMs': 50},
                    ],
                }
            },
        },
        'fallback': {
            'callId': 'demo-call-0002',
            'outcome': 'fail_closed_handoff',
            'checkpoint': {
                'flowState': 'wrap',
                'demoFallback': {'armed': True},
                'operatorSteer': {'pending': False},
                'pipecatFlow': {'script': {'completed': False}},
                'transcript': [
                    {'speaker': 'agent', 'text': 'The tool timed out, so I am escalating.', 'timestamp': '2026-06-12T12:01:00Z'},
                ],
                'events': [
                    {'type': 'demo_fallback_triggered', 'at': '2026-06-12T12:01:00Z', 'detail': {'mode': 'tool_timeout'}},
                ],
                'latencyMarks': [],
            },
        },
    }

    monkeypatch.setattr(adapter, '_load_proof_bundle', lambda: bundle)

    scenario = VoiceLabScenario(
        scenario_id='acc-scripted-wrap',
        adapter='agentic-contact-center',
        title='Deterministic Cancellation Rescue Script',
        prompt='Run the scripted local target.',
        metadata={'expected_outcome': 'scripted_wrap_complete'},
    )
    result = adapter.run_scenario(scenario)

    assert result['status'] == 'pass'
    assert result['verdict'] == 'pass'
    assert result['scores']['overall'] == 100
    assert result['call_id'] == 'demo-call-0001'
    assert result['final_state']['flow_state'] == 'wrap'
    assert any(event['event_type'] == 'transcript_turn' for event in result['call_events'])
    assert any(event['event_type'] == 'latency_mark' for event in result['call_events'])
    assert 'Caller: I want to cancel today.' in result['transcript']


def test_transcript_injection_adapter_runs_local_ask_loop():
    scenario = next(item for item in seeded_voice_lab_scenarios() if item.scenario_id == 'deck-grounded-ask-loop')

    result = TranscriptInjectionAdapter().run_scenario(scenario)

    assert result['status'] == 'pass'
    assert result['verdict'] == 'pass'
    assert result['scores']['task_completion'] == 100
    assert result['call_id']
    assert 'voice lab proof loop' in result['summary'].lower()
    assert any(event['event_type'] == 'question_asked' for event in result['call_events'])
    assert any(event.get('speaker') == 'agent' for event in result['call_events'])


def test_runner_counts_pass_and_blocked_results():
    scenarios = [
        VoiceLabScenario(
            scenario_id='missing-scenario',
            adapter='missing-adapter',
            title='Missing adapter',
            prompt='This should block cleanly.',
        )
    ]

    report = VoiceLabRunner(adapters=[]).run(scenarios)

    assert report['summary'] == {
        'scenario_count': 1,
        'pass_count': 0,
        'blocked_count': 1,
        'fail_count': 0,
    }
    assert report['results'][0]['status'] == 'blocked'
    assert report['results'][0]['verdict'] == 'blocked'
    assert report['results'][0]['scores']['overall'] == 0
