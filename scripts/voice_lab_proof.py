from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'apps' / 'api'))

from app.services.voice_lab import build_default_voice_lab_runner, seeded_voice_lab_scenarios


BUNDLE_SCHEMA_VERSION = 'voice-lab-evidence-bundle-v1'


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    runner = build_default_voice_lab_runner(PROJECT_ROOT)
    report = runner.run(seeded_voice_lab_scenarios())

    artifact_dir = (PROJECT_ROOT / args.artifact_root).resolve() if not args.artifact_root.is_absolute() else args.artifact_root
    manifest_path = write_evidence_bundle(report, artifact_dir)

    print(f'Saved voice lab proof manifest: {manifest_path}')
    print(json.dumps(report['summary'], indent=2))
    summary = report.get('summary') if isinstance(report.get('summary'), dict) else {}
    if summary.get('fail_count', 0) or summary.get('blocked_count', 0):
        return 1
    return 0


def write_evidence_bundle(report: dict, artifact_root: Path) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    bundle_id = f'voice-lab-bundle-{timestamp}'
    bundle_dir = artifact_root / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    scenario_entries = []
    for result in report.get('results', []):
        scenario_id = result.get('scenario_id', 'unknown-scenario')
        scenario_dir = bundle_dir / scenario_id
        scenario_dir.mkdir(parents=True, exist_ok=True)

        transcript_path = scenario_dir / 'transcript.txt'
        transcript_path.write_text(result.get('transcript') or '')

        timeline_path = scenario_dir / 'timeline.json'
        _write_json(timeline_path, result.get('call_events', []))

        raw_result_path = scenario_dir / 'result.json'
        _write_json(raw_result_path, result)

        call_events = result.get('call_events') if isinstance(result.get('call_events'), list) else []
        scorecard = result.get('scores') if isinstance(result.get('scores'), dict) else {}
        scenario_entries.append(
            {
                'scenario_id': scenario_id,
                'title': result.get('title'),
                'adapter': result.get('adapter'),
                'status': result.get('status'),
                'verdict': result.get('verdict'),
                'summary': result.get('summary'),
                'started_at': result.get('started_at'),
                'completed_at': result.get('completed_at'),
                'scenario_metadata': result.get('scenario', {}),
                'integration_status': result.get('integration_status', {}),
                'scorecard_result': {
                    'status': result.get('status'),
                    'verdict': result.get('verdict'),
                    'overall_score': scorecard.get('overall'),
                    'task_completion_score': scorecard.get('task_completion'),
                    'evidence_completeness_score': scorecard.get('evidence_completeness'),
                },
                'timeline': {
                    'event_count': len(call_events),
                    'first_event_at': call_events[0].get('timestamp') if call_events else None,
                    'last_event_at': call_events[-1].get('timestamp') if call_events else None,
                    'path': str(timeline_path),
                },
                'evidence_paths': {
                    'transcript': str(transcript_path),
                    'timeline': str(timeline_path),
                    'raw_result': str(raw_result_path),
                    'captured_artifacts': _bundle_captured_artifacts(
                        scenario_dir,
                        [artifact for artifact in result.get('artifacts', []) if isinstance(artifact, dict)],
                    ),
                },
                'metrics': result.get('metrics', {}),
                'final_state': result.get('final_state', {}),
            }
        )

    manifest = {
        'bundle_id': bundle_id,
        'bundle_schema_version': BUNDLE_SCHEMA_VERSION,
        'generated_at': report.get('generated_at'),
        'runner_version': report.get('runner_version'),
        'artifact_root': str(artifact_root),
        'bundle_root': str(bundle_dir),
        'summary': report.get('summary', {}),
        'scorecard_summary': _scorecard_summary(scenario_entries),
        'unsupported_layers': _collect_unsupported_layers(scenario_entries),
        'next_integration_steps': _collect_next_steps(scenario_entries),
        'scenarios': scenario_entries,
    }

    manifest_path = bundle_dir / 'manifest.json'
    _write_json(manifest_path, manifest)

    timestamped_manifest_path = artifact_root / f'voice-lab-proof-{timestamp}.json'
    latest_manifest_path = artifact_root / 'voice-lab-proof-latest.json'
    _write_json(timestamped_manifest_path, manifest)
    _write_json(latest_manifest_path, manifest)
    return manifest_path


def _bundle_captured_artifacts(scenario_dir: Path, artifacts: list[dict]) -> list[dict]:
    bundled: list[dict] = []
    bundled_dir = scenario_dir / 'captured-artifacts'
    for artifact in artifacts:
        artifact_entry = dict(artifact)
        artifact_path = artifact_entry.get('path')
        if not isinstance(artifact_path, str) or not artifact_path.strip():
            bundled.append(artifact_entry)
            continue

        source_path = Path(artifact_path)
        if not source_path.is_file():
            artifact_entry['path'] = None
            bundled.append(artifact_entry)
            continue

        bundled_dir.mkdir(parents=True, exist_ok=True)
        destination = _unique_artifact_destination(bundled_dir, source_path.name)
        destination.write_bytes(source_path.read_bytes())
        artifact_entry['path'] = str(destination)
        bundled.append(artifact_entry)
    return bundled


def _unique_artifact_destination(bundled_dir: Path, filename: str) -> Path:
    destination = bundled_dir / filename
    if not destination.exists():
        return destination

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        candidate = bundled_dir / f'{stem}-{counter}{suffix}'
        if not candidate.exists():
            return candidate
        counter += 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the voice lab progressive proof bundle.')
    parser.add_argument(
        '--artifact-root',
        type=Path,
        default=Path('artifacts/voice-lab'),
        help='Artifact directory where the bundle manifest and scenario files should be written.',
    )
    return parser.parse_args(argv)


def _scorecard_summary(scenarios: list[dict]) -> dict:
    overall_scores = [
        scenario.get('scorecard_result', {}).get('overall_score')
        for scenario in scenarios
        if isinstance(scenario.get('scorecard_result', {}).get('overall_score'), (int, float))
    ]
    average_score = round(sum(overall_scores) / len(overall_scores), 2) if overall_scores else None
    return {
        'scenario_count': len(scenarios),
        'average_overall_score': average_score,
        'pass_scenarios': [scenario['scenario_id'] for scenario in scenarios if scenario.get('status') == 'pass'],
        'blocked_scenarios': [scenario['scenario_id'] for scenario in scenarios if scenario.get('status') == 'blocked'],
        'failed_scenarios': [scenario['scenario_id'] for scenario in scenarios if scenario.get('status') not in {'pass', 'blocked'}],
    }


def _collect_unsupported_layers(scenarios: list[dict]) -> list[str]:
    collected: set[str] = set()
    for scenario in scenarios:
        integration_status = scenario.get('integration_status', {})
        for item in integration_status.get('unsupported_layers', []):
            if isinstance(item, str) and item:
                collected.add(item)
    return sorted(collected)


def _collect_next_steps(scenarios: list[dict]) -> list[str]:
    ordered: list[str] = []
    for scenario in scenarios:
        integration_status = scenario.get('integration_status', {})
        next_step = integration_status.get('next_integration_step')
        if isinstance(next_step, str) and next_step and next_step not in ordered:
            ordered.append(next_step)
    return ordered


def _write_json(path: Path, payload: object) -> None:
    path.write_text(f'{json.dumps(payload, indent=2)}\n')


if __name__ == '__main__':
    raise SystemExit(main())
