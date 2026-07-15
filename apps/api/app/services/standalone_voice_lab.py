from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.voice_lab import (
    AgenticContactCenterAdapter,
    TranscriptInjectionAdapter,
    VoiceLabRunner,
    VoiceLabScenario,
    _normalize_contact_center_snapshot,
    _transcript_text,
    _utc_now,
    _with_report_fields,
    _within_budget_marks,
)


DEFAULT_WRAP_FIXTURE = Path('docs/examples/agentic-contact-center-run-fixture.json')
DEFAULT_FALLBACK_FIXTURE = Path('docs/examples/agentic-contact-center-fail-closed-run-fixture.json')


class OfflineAgenticContactCenterAdapter:
    """Fixture-backed ACC-shaped target adapter for standalone voice-lab proof.

    The adapter intentionally uses the same public adapter name as the optional live
    sibling-repo adapter, so existing seeded scenarios remain unchanged. It never
    shells out and never requires an ACC checkout or process.
    """

    name = 'agentic-contact-center'

    def __init__(
        self,
        project_root: Path,
        *,
        fixtures: dict[str, Path] | None = None,
    ):
        self.project_root = Path(project_root)
        configured = fixtures or {
            'acc-scripted-wrap': DEFAULT_WRAP_FIXTURE,
            'acc-fail-closed-fallback': DEFAULT_FALLBACK_FIXTURE,
        }
        self.fixtures = {
            scenario_id: path if path.is_absolute() else self.project_root / path
            for scenario_id, path in configured.items()
        }
        self._last_fixture: Path | None = None

    def supports(self, scenario: VoiceLabScenario) -> bool:
        return scenario.scenario_id in self.fixtures

    def diagnostic_artifacts(self) -> list[dict[str, Any]]:
        if self._last_fixture is None:
            return []
        return [
            {
                'type': 'offline_target_fixture',
                'path': str(self._last_fixture),
                'source': self.name,
            }
        ]

    def run_scenario(self, scenario: VoiceLabScenario) -> dict[str, Any]:
        fixture_path = self.fixtures.get(scenario.scenario_id)
        if fixture_path is None:
            raise RuntimeError(f'No standalone target fixture registered for {scenario.scenario_id}.')
        if not fixture_path.is_file():
            raise RuntimeError(f'Standalone target fixture is missing: {fixture_path}')

        self._last_fixture = fixture_path
        payload = json.loads(fixture_path.read_text())
        if not isinstance(payload, dict):
            raise RuntimeError(f'Standalone target fixture must contain a JSON object: {fixture_path}')

        checkpoint = payload.get('call')
        if not isinstance(checkpoint, dict):
            raise RuntimeError(f'Standalone target fixture is missing call evidence: {fixture_path}')

        call_events = _normalize_contact_center_snapshot(checkpoint)
        transcript_text = _transcript_text(call_events)
        completed_at = max((event['timestamp'] for event in call_events), default=_utc_now())
        last_agent_text = next(
            (
                event['text']
                for event in reversed(call_events)
                if event.get('speaker') == 'agent' and event.get('text')
            ),
            '',
        )
        outcome = str(payload.get('outcome') or 'unknown')
        expected_outcome = scenario.metadata.get('expected_outcome')
        status = 'pass' if expected_outcome in {None, outcome} else 'fail'
        session = checkpoint.get('session') if isinstance(checkpoint.get('session'), dict) else {}

        return _with_report_fields(
            {
                'scenario_id': scenario.scenario_id,
                'title': scenario.title,
                'adapter': self.name,
                'status': status,
                'started_at': _first_event_at(call_events) or _utc_now(),
                'completed_at': completed_at,
                'summary': last_agent_text or outcome,
                'call_id': session.get('callId') or payload.get('callId'),
                'call_events': call_events,
                'transcript': transcript_text,
                'final_state': {
                    'flow_state': checkpoint.get('flowState'),
                    'outcome': outcome,
                    'demo_fallback': checkpoint.get('demoFallback'),
                    'operator_steer': checkpoint.get('operatorSteer'),
                    'pipecat_flow': checkpoint.get('pipecatFlow'),
                    'runtime_mode_labels': session.get('runtimeModeLabels'),
                },
                'metrics': {
                    'turn_count': len(checkpoint.get('transcript', [])),
                    'platform_event_count': len(checkpoint.get('events', [])),
                    'latency_mark_count': len(checkpoint.get('latencyMarks', [])),
                    'within_budget_marks': _within_budget_marks(checkpoint.get('latencyMarks', [])),
                },
                'artifacts': self.diagnostic_artifacts(),
                'evidence': {
                    'target_snapshot': checkpoint,
                    'native_outcome': outcome,
                    'provider': 'checked-in-offline-target-fixture',
                    'fixture_path': str(fixture_path),
                    'standalone': True,
                },
            }
        )


class StandaloneVoiceLabRunner(VoiceLabRunner):
    """Voice-lab runner that rewrites ACC-shaped fixture claims explicitly."""

    def run(self, scenarios: list[VoiceLabScenario]) -> dict[str, Any]:
        report = super().run(scenarios)
        for result in report.get('results', []):
            evidence = result.get('evidence') if isinstance(result.get('evidence'), dict) else {}
            if evidence.get('standalone') is not True:
                continue
            scenario_metadata = result.get('scenario') if isinstance(result.get('scenario'), dict) else {}
            scenario_metadata['execution_mode'] = 'offline_target_fixture'
            result['scenario'] = scenario_metadata
            result['integration_status'] = {
                'proof_stage': 'standalone_fixture_baseline',
                'supported_layers': [
                    'checked-in target-shaped evidence replay',
                    'structured transcript capture',
                    'platform event trail capture',
                    'latency mark capture',
                    'artifact bundle persistence',
                ],
                'unsupported_layers': [
                    'live_target_execution',
                    'live_asr',
                    'live_tts',
                    'sip_trunk',
                    'webrtc_media',
                    'recorded_audio_waveforms',
                ],
                'next_integration_step': (
                    'Run the same scenario through an explicitly configured target adapter; '
                    'the standalone product does not require that integration.'
                ),
            }
        return report


def build_standalone_voice_lab_runner(project_root: Path) -> VoiceLabRunner:
    """Build the blessed voice-lab runner with no external target dependency."""

    return StandaloneVoiceLabRunner(
        adapters=[
            OfflineAgenticContactCenterAdapter(project_root=project_root),
            TranscriptInjectionAdapter(),
        ]
    )


def build_live_acc_voice_lab_runner(
    project_root: Path,
    *,
    acc_repo_root: Path,
) -> VoiceLabRunner:
    """Build the explicitly requested sibling-repo runner for integration work."""

    return VoiceLabRunner(
        adapters=[
            AgenticContactCenterAdapter(
                repo_root=acc_repo_root,
                artifact_dir=project_root / 'artifacts' / 'voice-lab',
            ),
            TranscriptInjectionAdapter(),
        ]
    )


def _first_event_at(call_events: list[dict[str, Any]]) -> str | None:
    timestamps = [str(event.get('timestamp')) for event in call_events if event.get('timestamp')]
    return min(timestamps) if timestamps else None
