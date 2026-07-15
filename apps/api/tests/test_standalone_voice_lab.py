from __future__ import annotations

from pathlib import Path

from app.services.standalone_voice_lab import OfflineAgenticContactCenterAdapter, build_standalone_voice_lab_runner
from app.services.voice_lab import seeded_voice_lab_scenarios


def _project_root() -> Path:
    test_path = Path(__file__).resolve()
    return next(parent for parent in test_path.parents if (parent / 'docs' / 'examples').is_dir())


def test_offline_acc_adapter_uses_checked_in_fixtures_without_sibling_repo():
    project_root = _project_root()
    adapter = OfflineAgenticContactCenterAdapter(project_root)
    scripted = next(item for item in seeded_voice_lab_scenarios() if item.scenario_id == 'acc-scripted-wrap')
    fallback = next(item for item in seeded_voice_lab_scenarios() if item.scenario_id == 'acc-fail-closed-fallback')

    scripted_result = adapter.run_scenario(scripted)
    fallback_result = adapter.run_scenario(fallback)

    assert scripted_result['status'] == 'pass'
    assert scripted_result['verdict'] == 'pass'
    assert scripted_result['final_state']['outcome'] == 'scripted_wrap_complete'
    assert 'Caller: I want to cancel' in scripted_result['transcript']
    assert scripted_result['evidence']['standalone'] is True

    assert fallback_result['status'] == 'pass'
    assert fallback_result['verdict'] == 'pass'
    assert fallback_result['final_state']['outcome'] == 'fail_closed_handoff'
    assert fallback_result['final_state']['demo_fallback']['mode'] == 'tool_timeout'
    assert any(event['event_type'] == 'human_handoff_started' for event in fallback_result['call_events'])
    assert fallback_result['artifacts'][0]['type'] == 'offline_target_fixture'


def test_blessed_voice_lab_runner_passes_without_acc_checkout_or_process():
    project_root = _project_root()
    runner = build_standalone_voice_lab_runner(project_root)

    report = runner.run(seeded_voice_lab_scenarios())

    assert report['summary'] == {
        'scenario_count': 3,
        'pass_count': 3,
        'blocked_count': 0,
        'fail_count': 0,
    }
    results = {result['scenario_id']: result for result in report['results']}
    assert results['acc-scripted-wrap']['evidence']['provider'] == 'checked-in-offline-target-fixture'
    assert results['acc-fail-closed-fallback']['evidence']['provider'] == 'checked-in-offline-target-fixture'
    assert results['deck-grounded-ask-loop']['status'] == 'pass'
