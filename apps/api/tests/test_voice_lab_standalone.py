from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_standalone_module():
    test_file_path = Path(__file__).resolve()
    script_path = next(
        candidate
        for parent in test_file_path.parents
        for candidate in [parent / 'scripts' / 'voice_lab_standalone_proof.py']
        if candidate.exists()
    )
    spec = importlib.util.spec_from_file_location('voice_lab_standalone_proof_script', script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_scenarios_exclude_optional_external_targets():
    module = _load_standalone_module()

    scenarios = module.standalone_voice_lab_scenarios()

    assert scenarios
    assert all(scenario.adapter != 'agentic-contact-center' for scenario in scenarios)
    assert {scenario.scenario_id for scenario in scenarios} == {'deck-grounded-ask-loop'}


def test_standalone_runner_passes_without_acc_checkout():
    module = _load_standalone_module()

    report = module.build_standalone_voice_lab_runner().run(module.standalone_voice_lab_scenarios())

    assert report['summary']['scenario_count'] == 1
    assert report['summary']['pass_count'] == 1
    assert report['summary']['blocked_count'] == 0
    assert report['summary']['fail_count'] == 0
    result = report['results'][0]
    assert result['adapter'] == 'session-ask'
    assert result['scenario']['execution_mode'] == 'transcript_injection'
