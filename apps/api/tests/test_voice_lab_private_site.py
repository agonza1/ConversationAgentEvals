from __future__ import annotations

import importlib.util
import json
from pathlib import Path


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
    scenario_root.mkdir(parents=True)
    (scenario_root / 'transcript.txt').write_text('Caller: Hello\nAgent: Hi')
    (scenario_root / 'timeline.json').write_text('[]\n')
    (scenario_root / 'result.json').write_text('{"status": "pass"}\n')

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
    assert (site_root / 'bundle' / 'fixture-scenario' / 'transcript.txt').read_text() == 'Caller: Hello\nAgent: Hi'
    index_html = (site_root / 'index.html').read_text()
    assert 'Voice Agent Reliability Lab' in index_html
    assert 'Business outcome tested' in index_html
    assert 'Risk behavior proven' in index_html
    assert 'Evidence to trust' in index_html
    assert 'Evidence Appendix' in index_html
    assert 'Fixture Scenario' in index_html
    assert 'Source manifest' not in index_html
