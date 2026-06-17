from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.services.voice_lab import (
    AgenticContactCenterAdapter,
    TranscriptInjectionAdapter,
    VoiceLabRunner,
    VoiceLabScenario,
    seeded_voice_lab_scenarios,
)


def _load_voice_lab_proof_module():
    test_file_path = Path(__file__).resolve()
    script_path = next(
        (
            candidate
            for parent in test_file_path.parents
            for candidate in [parent / 'scripts' / 'voice_lab_proof.py']
            if candidate.exists()
        ),
        None,
    )
    assert script_path is not None
    spec = importlib.util.spec_from_file_location('voice_lab_proof_script', script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contact_center_adapter_normalizes_proof_bundle(tmp_path, monkeypatch):
    adapter = AgenticContactCenterAdapter(
        repo_root=Path('/workspace/projects/agentic-contact-center'),
        artifact_dir=tmp_path,
    )
    adapter._cached_bundle_path = tmp_path / 'proof.json'
    adapter._cached_log_path = tmp_path / 'proof.log'

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
    result = VoiceLabRunner(adapters=[adapter]).run([scenario])['results'][0]

    assert result['status'] == 'pass'
    assert result['verdict'] == 'pass'
    assert result['scores']['overall'] == 100
    assert result['call_id'] == 'demo-call-0001'
    assert result['final_state']['flow_state'] == 'wrap'
    assert any(event['event_type'] == 'transcript_turn' for event in result['call_events'])
    assert any(event['event_type'] == 'latency_mark' for event in result['call_events'])
    assert 'Caller: I want to cancel today.' in result['transcript']
    assert result['scenario']['execution_mode'] == 'deterministic_fixture'
    assert 'live_asr' in result['integration_status']['unsupported_layers']
    assert any(artifact['type'] == 'runner_log' for artifact in result['artifacts'])


def test_transcript_injection_adapter_runs_local_ask_loop():
    scenario = next(item for item in seeded_voice_lab_scenarios() if item.scenario_id == 'deck-grounded-ask-loop')

    result = VoiceLabRunner(adapters=[TranscriptInjectionAdapter()]).run([scenario])['results'][0]

    assert result['status'] == 'pass'
    assert result['verdict'] == 'pass'
    assert result['scores']['task_completion'] == 100
    assert result['call_id']
    assert 'voice lab proof loop' in result['summary'].lower()
    assert any(event['event_type'] == 'question_asked' for event in result['call_events'])
    assert any(event.get('speaker') == 'agent' for event in result['call_events'])
    assert result['scenario']['execution_mode'] == 'transcript_injection'
    assert any(artifact['type'] == 'session_transcript' for artifact in result['artifacts'])


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
    assert report['results'][0]['scenario']['scenario_id'] == 'missing-scenario'


def test_voice_lab_proof_script_writes_bundle_manifest(tmp_path):
    module = _load_voice_lab_proof_module()
    source_artifact = tmp_path / 'fixture.log'
    source_artifact.write_text('fixture log\n')
    report = {
        'generated_at': '2026-06-13T12:00:00Z',
        'runner_version': 'test-runner',
        'summary': {'scenario_count': 1, 'pass_count': 1, 'blocked_count': 0, 'fail_count': 0},
        'results': [
            {
                'scenario_id': 'fixture-scenario',
                'title': 'Fixture Scenario',
                'adapter': 'session-ask',
                'status': 'pass',
                'verdict': 'pass',
                'summary': 'Fixture completed.',
                'started_at': '2026-06-13T12:00:00Z',
                'completed_at': '2026-06-13T12:00:01Z',
                'call_events': [
                    {'event_id': 'evt-1', 'event_type': 'transcript_turn', 'timestamp': '2026-06-13T12:00:00Z', 'source': 'fixture', 'speaker': 'caller', 'text': 'Hello', 'detail': {}},
                    {'event_id': 'evt-2', 'event_type': 'transcript_turn', 'timestamp': '2026-06-13T12:00:01Z', 'source': 'fixture', 'speaker': 'agent', 'text': 'Hi', 'detail': {}},
                ],
                'transcript': 'Caller: Hello\nAgent: Hi',
                'final_state': {'state': 'done'},
                'metrics': {'turn_count': 2},
                'artifacts': [{'type': 'runner_log', 'path': str(source_artifact), 'source': 'fixture'}],
                'scores': {'overall': 100, 'task_completion': 100, 'evidence_completeness': 100},
                'scenario': {'scenario_id': 'fixture-scenario', 'execution_mode': 'transcript_injection'},
                'integration_status': {'unsupported_layers': ['live_asr'], 'next_integration_step': 'Add live ASR.'},
            }
        ],
    }

    manifest_path = module.write_evidence_bundle(report, tmp_path)
    manifest = json.loads(manifest_path.read_text())

    assert manifest['bundle_schema_version'] == 'voice-lab-evidence-bundle-v1'
    assert manifest['scorecard_summary']['scenario_count'] == 1
    assert manifest['unsupported_layers'] == ['live_asr']
    scenario_entry = manifest['scenarios'][0]
    transcript_path = Path(scenario_entry['evidence_paths']['transcript'])
    timeline_path = Path(scenario_entry['timeline']['path'])
    raw_result_path = Path(scenario_entry['evidence_paths']['raw_result'])
    captured_artifact_path = Path(scenario_entry['evidence_paths']['captured_artifacts'][0]['path'])
    assert transcript_path.exists()
    assert timeline_path.exists()
    assert raw_result_path.exists()
    assert captured_artifact_path.exists()
    assert transcript_path.read_text() == 'Caller: Hello\nAgent: Hi'
    assert captured_artifact_path.read_text() == 'fixture log\n'


def test_voice_lab_proof_script_returns_nonzero_for_failed_or_blocked_summary(tmp_path, monkeypatch):
    module = _load_voice_lab_proof_module()

    class StubRunner:
        def __init__(self, report):
            self._report = report

        def run(self, _scenarios):
            return self._report

    monkeypatch.setattr(module, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(module, 'seeded_voice_lab_scenarios', lambda: ['scenario'])

    monkeypatch.setattr(
        module,
        'build_default_voice_lab_runner',
        lambda _project_root: StubRunner({'summary': {'fail_count': 0, 'blocked_count': 0}, 'results': []}),
    )
    assert module.main([]) == 0

    monkeypatch.setattr(
        module,
        'build_default_voice_lab_runner',
        lambda _project_root: StubRunner({'summary': {'fail_count': 1, 'blocked_count': 0}, 'results': []}),
    )
    assert module.main([]) == 1

    monkeypatch.setattr(
        module,
        'build_default_voice_lab_runner',
        lambda _project_root: StubRunner({'summary': {'fail_count': 0, 'blocked_count': 1}, 'results': []}),
    )
    assert module.main([]) == 1
