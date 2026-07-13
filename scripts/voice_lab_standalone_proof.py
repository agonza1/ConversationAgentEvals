from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'apps' / 'api'))
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))

from app.services.voice_lab import TranscriptInjectionAdapter, VoiceLabRunner, VoiceLabScenario, seeded_voice_lab_scenarios
from voice_lab_proof import write_evidence_bundle


EXTERNAL_TARGET_ADAPTERS = {'agentic-contact-center'}


def standalone_voice_lab_scenarios() -> list[VoiceLabScenario]:
    """Return only CAE-owned scenarios that need no external target checkout."""

    return [
        scenario
        for scenario in seeded_voice_lab_scenarios()
        if scenario.adapter not in EXTERNAL_TARGET_ADAPTERS
    ]


def build_standalone_voice_lab_runner() -> VoiceLabRunner:
    """Build the default proof runner without optional external target adapters."""

    return VoiceLabRunner(adapters=[TranscriptInjectionAdapter()])


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    scenarios = standalone_voice_lab_scenarios()
    if not scenarios:
        print(json.dumps({'ok': False, 'error': 'standalone_voice_lab_scenarios_missing'}, indent=2), file=sys.stderr)
        return 1

    report = build_standalone_voice_lab_runner().run(scenarios)
    artifact_dir = (
        (PROJECT_ROOT / args.artifact_root).resolve()
        if not args.artifact_root.is_absolute()
        else args.artifact_root
    )
    manifest_path = write_evidence_bundle(report, artifact_dir)

    summary = report.get('summary') if isinstance(report.get('summary'), dict) else {}
    output = {
        'ok': not summary.get('fail_count', 0) and not summary.get('blocked_count', 0),
        'mode': 'conversation-agent-evals-standalone',
        'external_targets_required': False,
        'scenario_ids': [scenario.scenario_id for scenario in scenarios],
        'manifest_path': str(manifest_path),
        'summary': summary,
    }
    print(json.dumps(output, indent=2))
    return 0 if output['ok'] else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Run the CAE-owned voice-lab proof without optional external target repositories.'
    )
    parser.add_argument(
        '--artifact-root',
        type=Path,
        default=Path('artifacts/voice-lab-standalone'),
        help='Artifact directory where the standalone proof bundle is written.',
    )
    return parser.parse_args(argv)


if __name__ == '__main__':
    raise SystemExit(main())
