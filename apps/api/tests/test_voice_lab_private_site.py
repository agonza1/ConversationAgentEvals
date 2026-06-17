from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_private_site_module():
    test_file_path = Path(__file__).resolve()
    script_path = next(
        (
            candidate
            for parent in test_file_path.parents
            for candidate in [parent / 'scripts' / 'voice_lab_private_site.py']
            if candidate.exists()
        ),
        None,
    )
    assert script_path is not None
    spec = importlib.util.spec_from_file_location('voice_lab_private_site_script', script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_materialize_private_site_copies_bundle_and_rewrites_manifest(tmp_path):
    module = _load_private_site_module()
    bundle_root = tmp_path / 'voice-lab-bundle-test'
    scenario_root = bundle_root / 'fixture-scenario'
    captured_root = scenario_root / 'captured-artifacts'
    captured_root.mkdir(parents=True)
    (scenario_root / 'transcript.txt').write_text('Caller: Hello\nAgent: Hi')
    (scenario_root / 'timeline.json').write_text('[]\n')
    (scenario_root / 'result.json').write_text('{"status": "pass"}\n')
    (captured_root / 'proof.log').write_text('runner ok\n')

    manifest_path = bundle_root / 'manifest.json'
    manifest = {
        'bundle_id': 'voice-lab-bundle-test',
        'generated_at': '2026-06-14T12:00:00Z',
        'runner_version': 'test-runner',
        'bundle_root': str(bundle_root),
        'summary': {'scenario_count': 1, 'pass_count': 1, 'blocked_count': 0, 'fail_count': 0},
        'scorecard_summary': {'scenario_count': 1, 'average_overall_score': 100},
        'unsupported_layers': ['live_asr'],
        'scenarios': [
            {
                'scenario_id': 'fixture-scenario',
                'title': 'Fixture Scenario',
                'status': 'pass',
                'verdict': 'pass',
                'summary': 'Fixture completed.',
                'metrics': {'turn_count': 2},
                'final_state': {'state': 'done'},
                'integration_status': {
                    'supported_layers': ['transcript-injected question loop'],
                    'unsupported_layers': ['live_asr'],
                },
                'evidence_paths': {
                    'transcript': str(scenario_root / 'transcript.txt'),
                    'timeline': str(scenario_root / 'timeline.json'),
                    'raw_result': str(scenario_root / 'result.json'),
                    'captured_artifacts': [
                        {'type': 'runner_log', 'path': str(captured_root / 'proof.log'), 'source': 'fixture'},
                    ],
                },
                'timeline': {'path': str(scenario_root / 'timeline.json')},
            }
        ],
    }
    manifest_path.write_text(f'{json.dumps(manifest, indent=2)}\n')

    site_root = tmp_path / 'site'
    module.materialize_private_site(
        manifest_path=manifest_path,
        site_root=site_root,
        access_boundary='Private localhost-only proof site bound to 127.0.0.1.',
    )

    site_manifest = json.loads((site_root / 'manifest.json').read_text())
    assert site_manifest['bundle_root'] == 'bundle'
    assert site_manifest['scenarios'][0]['timeline']['path'] == 'bundle/fixture-scenario/timeline.json'
    assert site_manifest['scenarios'][0]['evidence_paths']['transcript'] == 'bundle/fixture-scenario/transcript.txt'
    assert site_manifest['scenarios'][0]['evidence_paths']['captured_artifacts'][0]['path'] == (
        'bundle/fixture-scenario/captured-artifacts/proof.log'
    )
    assert (site_root / 'bundle' / 'fixture-scenario' / 'transcript.txt').read_text() == 'Caller: Hello\nAgent: Hi'
    assert (site_root / 'bundle' / 'fixture-scenario' / 'captured-artifacts' / 'proof.log').read_text() == 'runner ok\n'
    index_html = (site_root / 'index.html').read_text()
    assert 'Voice Agent Reliability Lab' in index_html
    assert 'Business outcome tested' in index_html
    assert 'Risk behavior proven' in index_html
    assert 'Evidence to trust' in index_html
    assert 'Evidence Appendix' in index_html
    assert 'Text mode' in index_html
    assert 'Visual mode' in index_html
    assert 'Call/session continuity' in index_html
    assert 'Call quality / MOS' in index_html
    assert 'E2E / response latency' in index_html
    assert 'Fixture Scenario' in index_html
    assert 'Source manifest' not in index_html


def test_materialize_private_site_rejects_overlapping_roots(tmp_path):
    module = _load_private_site_module()
    bundle_root = tmp_path / 'bundle'
    scenario_root = bundle_root / 'fixture-scenario'
    scenario_root.mkdir(parents=True)
    (scenario_root / 'transcript.txt').write_text('Caller: Hello\nAgent: Hi')
    (scenario_root / 'timeline.json').write_text('[]\n')
    (scenario_root / 'result.json').write_text('{"status": "pass"}\n')
    manifest_path = bundle_root / 'manifest.json'
    manifest_path.write_text(
        json.dumps(
            {
                'bundle_root': str(bundle_root),
                'summary': {'scenario_count': 1, 'pass_count': 1, 'blocked_count': 0, 'fail_count': 0},
                'scorecard_summary': {'scenario_count': 1, 'average_overall_score': 100},
                'scenarios': [
                    {
                        'scenario_id': 'fixture-scenario',
                        'title': 'Fixture Scenario',
                        'status': 'pass',
                        'verdict': 'pass',
                        'summary': 'Fixture completed.',
                        'metrics': {'turn_count': 2},
                        'final_state': {},
                        'integration_status': {},
                        'evidence_paths': {
                            'transcript': str(scenario_root / 'transcript.txt'),
                            'timeline': str(scenario_root / 'timeline.json'),
                            'raw_result': str(scenario_root / 'result.json'),
                        },
                        'timeline': {'path': str(scenario_root / 'timeline.json')},
                    }
                ],
            },
            indent=2,
        )
        + '\n'
    )

    with pytest.raises(ValueError, match='must not overlap'):
        module.materialize_private_site(
            manifest_path=manifest_path,
            site_root=bundle_root / 'published',
            access_boundary='Private localhost-only proof site bound to 127.0.0.1.',
        )


def test_render_index_html_uses_status_aware_measurements_and_risk_copy():
    module = _load_private_site_module()
    manifest = {
        'generated_at': '2026-06-17T11:00:00Z',
        'summary': {'scenario_count': 2, 'pass_count': 1, 'blocked_count': 1, 'fail_count': 0},
        'scorecard_summary': {'scenario_count': 2, 'average_overall_score': 50},
        'unsupported_layers': ['live_asr'],
        'scenarios': [
            {
                'scenario_id': 'acc-fail-closed-fallback',
                'title': 'Fallback',
                'status': 'blocked',
                'verdict': 'blocked',
                'summary': 'Target repo missing.',
                'metrics': {'latency_mark_count': 0, 'within_budget_marks': {'within_budget': 0, 'over_budget': 0}},
                'final_state': {},
                'integration_status': {'unsupported_layers': ['sip_trunk']},
                'evidence_paths': {},
            },
            {
                'scenario_id': 'deck-grounded-ask-loop',
                'title': 'Ask Loop',
                'status': 'pass',
                'verdict': 'pass',
                'summary': 'Fixture completed.',
                'metrics': {'turn_count': 2},
                'final_state': {},
                'integration_status': {'unsupported_layers': []},
                'evidence_paths': {},
            },
        ],
    }

    html = module.render_index_html(manifest)

    assert 'Fail-closed escalation was not proven in this run' in html
    assert '0/0 marks in budget' not in html
    assert 'Blocked' in html
    assert 'Completed' in html  # passing scenario still renders its successful continuity state


def test_render_index_html_does_not_mark_missing_blocked_evidence_as_pass():
    module = _load_private_site_module()
    measurements = module._scenario_measurements(
        {
            'status': 'blocked',
            'metrics': {},
            'integration_status': {},
            'final_state': {},
            'transcript': '',
        }
    )

    conversation = next(item for item in measurements if item['label'] == 'Conversation evidence')
    assert conversation['state'] == 'warn'
    assert conversation['value'] == 'Not captured before blocker'


def test_private_site_main_returns_nonzero_when_bundle_summary_has_failures(tmp_path, monkeypatch):
    module = _load_private_site_module()
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_text(json.dumps({'summary': {'fail_count': 0, 'blocked_count': 1}}))

    monkeypatch.setattr(module, 'build_private_site', lambda **_kwargs: (manifest_path, tmp_path / 'site'))

    assert module.main(['build']) == 1

    manifest_path.write_text(json.dumps({'summary': {'fail_count': 0, 'blocked_count': 0}}))

    assert module.main(['build']) == 0
