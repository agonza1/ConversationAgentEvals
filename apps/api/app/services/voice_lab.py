from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.entities import Deck, Slide
from app.services.session_service import create_session, set_session_status


RUNNER_VERSION = 'voice-lab-day2-v1'


@dataclass(slots=True)
class VoiceLabScenario:
    scenario_id: str
    adapter: str
    title: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VoiceLabEvent:
    event_id: str
    event_type: str
    timestamp: str
    source: str
    speaker: str | None = None
    text: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class VoiceTargetAdapter(Protocol):
    name: str

    def supports(self, scenario: VoiceLabScenario) -> bool:
        ...

    def run_scenario(self, scenario: VoiceLabScenario) -> dict[str, Any]:
        ...

    def diagnostic_artifacts(self) -> list[dict[str, Any]]:
        ...


class VoiceLabRunner:
    def __init__(self, adapters: list[VoiceTargetAdapter]):
        self._adapters = {adapter.name: adapter for adapter in adapters}

    def run(self, scenarios: list[VoiceLabScenario]) -> dict[str, Any]:
        started_at = _utc_now()
        results: list[dict[str, Any]] = []

        for scenario in scenarios:
            adapter = self._adapters.get(scenario.adapter)
            if adapter is None:
                results.append(
                    _enrich_result(
                        _blocked_result(
                            scenario=scenario,
                            blocker=f'Adapter "{scenario.adapter}" is not registered.',
                        ),
                        scenario,
                    )
                )
                continue

            if not adapter.supports(scenario):
                results.append(
                    _enrich_result(
                        _blocked_result(
                            scenario=scenario,
                            blocker=f'Adapter "{scenario.adapter}" does not support scenario "{scenario.scenario_id}".',
                        ),
                        scenario,
                    )
                )
                continue

            try:
                results.append(_enrich_result(adapter.run_scenario(scenario), scenario))
            except Exception as exc:
                artifacts = adapter.diagnostic_artifacts() if hasattr(adapter, 'diagnostic_artifacts') else []
                results.append(
                    _enrich_result(
                        _blocked_result(
                            scenario=scenario,
                            blocker=str(exc),
                            artifacts=artifacts,
                        ),
                        scenario,
                    )
                )

        summary = {
            'scenario_count': len(results),
            'pass_count': sum(1 for result in results if result['status'] == 'pass'),
            'blocked_count': sum(1 for result in results if result['status'] == 'blocked'),
        }
        summary['fail_count'] = max(summary['scenario_count'] - summary['pass_count'] - summary['blocked_count'], 0)

        return {
            'generated_at': started_at,
            'runner_version': RUNNER_VERSION,
            'results': results,
            'summary': summary,
        }


class AgenticContactCenterAdapter:
    name = 'agentic-contact-center'

    def __init__(self, repo_root: Path, artifact_dir: Path | None = None):
        self.repo_root = Path(repo_root)
        self.artifact_dir = Path(artifact_dir) if artifact_dir else self.repo_root / 'artifacts' / 'voice-lab'
        self._cached_bundle: dict[str, Any] | None = None
        self._cached_bundle_path: Path | None = None
        self._cached_log_path: Path | None = None

    def supports(self, scenario: VoiceLabScenario) -> bool:
        return scenario.scenario_id in {
            'acc-scripted-wrap',
            'acc-fail-closed-fallback',
        }

    def diagnostic_artifacts(self) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        if self._cached_bundle_path is not None:
            artifacts.append(
                {
                    'type': 'proof_bundle',
                    'path': str(self._cached_bundle_path),
                    'source': self.name,
                }
            )
        if self._cached_log_path is not None:
            artifacts.append(
                {
                    'type': 'runner_log',
                    'path': str(self._cached_log_path),
                    'source': self.name,
                }
            )
        return artifacts

    def run_scenario(self, scenario: VoiceLabScenario) -> dict[str, Any]:
        bundle = self._load_proof_bundle()
        scenario_payload = self._bundle_slice(bundle, scenario.scenario_id)
        checkpoint = scenario_payload['checkpoint']
        call_events = _normalize_contact_center_snapshot(checkpoint)
        transcript_text = _transcript_text(call_events)
        completed_at = max((event['timestamp'] for event in call_events), default=_utc_now())
        last_agent_text = next((event['text'] for event in reversed(call_events) if event.get('speaker') == 'agent' and event.get('text')), '')

        outcome = scenario_payload['outcome']
        expected_outcome = scenario.metadata.get('expected_outcome')
        status = 'pass' if expected_outcome in {None, outcome} else 'fail'

        captured_artifacts = [
            artifact
            for artifact in self.diagnostic_artifacts()
            if artifact.get('type') == 'proof_bundle' or artifact.get('path')
        ]

        return _with_report_fields({
            'scenario_id': scenario.scenario_id,
            'title': scenario.title,
            'adapter': self.name,
            'status': status,
            'started_at': bundle.get('generatedAt', _utc_now()),
            'completed_at': completed_at,
            'summary': last_agent_text or outcome,
            'call_id': scenario_payload['callId'],
            'call_events': call_events,
            'transcript': transcript_text,
            'final_state': {
                'flow_state': checkpoint.get('flowState'),
                'demo_fallback': checkpoint.get('demoFallback'),
                'operator_steer': checkpoint.get('operatorSteer'),
                'pipecat_flow': checkpoint.get('pipecatFlow'),
            },
            'metrics': {
                'turn_count': len(checkpoint.get('transcript', [])),
                'platform_event_count': len(checkpoint.get('events', [])),
                'latency_mark_count': len(checkpoint.get('latencyMarks', [])),
                'within_budget_marks': _within_budget_marks(checkpoint.get('latencyMarks', [])),
            },
            'artifacts': captured_artifacts,
            'evidence': {
                'target_snapshot': checkpoint,
                'native_outcome': outcome,
                'provider': bundle.get('provider'),
            },
        })

    def _load_proof_bundle(self) -> dict[str, Any]:
        if self._cached_bundle is not None:
            return self._cached_bundle

        if not self.repo_root.exists():
            raise RuntimeError(f'Missing local target repo: {self.repo_root}')
        if shutil.which('npm') is None:
            raise RuntimeError('npm is not available in this runtime.')

        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
        proof_path = self.artifact_dir / f'agentic-contact-center-proof-{timestamp}.json'
        latest_path = self.artifact_dir / 'agentic-contact-center-proof-latest.json'
        log_path = self.artifact_dir / f'agentic-contact-center-proof-{timestamp}.log'
        command = ['npm', 'run', 'proof', '--', '--out', str(proof_path), '--latest-out', str(latest_path)]
        completed = subprocess.run(command, cwd=self.repo_root, check=False, capture_output=True, text=True)
        log_path.write_text(
            '\n'.join(
                [
                    f'command: {" ".join(command)}',
                    f'cwd: {self.repo_root}',
                    '--- stdout ---',
                    completed.stdout.strip(),
                    '--- stderr ---',
                    completed.stderr.strip(),
                    '',
                ]
            )
        )
        self._cached_log_path = log_path
        if proof_path.exists():
            self._cached_bundle_path = proof_path
        if completed.returncode != 0:
            raise RuntimeError(
                f'agentic-contact-center proof command failed with exit code {completed.returncode}; see {log_path}'
            )
        if not proof_path.exists():
            raise RuntimeError(f'agentic-contact-center proof command did not write expected artifact: {proof_path}')
        self._cached_bundle = json.loads(proof_path.read_text())
        return self._cached_bundle

    def _bundle_slice(self, bundle: dict[str, Any], scenario_id: str) -> dict[str, Any]:
        if scenario_id == 'acc-scripted-wrap':
            scripted = bundle['scripted']
            return {
                'callId': scripted['callId'],
                'outcome': scripted['outcome'],
                'checkpoint': scripted['checkpoints']['wrapped'],
            }
        if scenario_id == 'acc-fail-closed-fallback':
            fallback = bundle['fallback']
            return {
                'callId': fallback['callId'],
                'outcome': fallback['outcome'],
                'checkpoint': fallback['checkpoint'],
            }
        raise RuntimeError(f'Unsupported contact-center scenario: {scenario_id}')


class TranscriptInjectionAdapter:
    name = 'session-ask'

    def supports(self, scenario: VoiceLabScenario) -> bool:
        return scenario.scenario_id == 'deck-grounded-ask-loop'

    def run_scenario(self, scenario: VoiceLabScenario) -> dict[str, Any]:
        slides = scenario.metadata.get('slides') or []
        questions = scenario.metadata.get('questions') or []
        expected_terms = [term.lower() for term in scenario.metadata.get('expected_terms', [])]
        if not slides or not questions:
            raise RuntimeError('Transcript injection scenario requires slides and questions.')

        created_at = _utc_now()
        session_id: str | None = None
        ask_payloads: list[dict[str, Any]] = []

        db = SessionLocal()
        try:
            deck = _create_lab_deck(db, title=scenario.title, slides=slides)
            session = create_session(db, deck.id)
            session = set_session_status(db, session.id, 'presenting')
            session_id = session.id

            with patch('app.services.grounding_service.settings', SimpleNamespace(openai_api_key=None)):
                with TestClient(app) as client:
                    for question in questions:
                        response = client.post(f'/api/sessions/{session.id}/ask', json={'question': question})
                        response.raise_for_status()
                        ask_payloads.append(response.json())

                    transcript_payload = client.get(f'/api/sessions/{session.id}/transcript').json()
                    events_payload = client.get(f'/api/sessions/{session.id}/events').json()
                    session_payload = client.get(f'/api/sessions/{session.id}').json()

            call_events = _normalize_session_payload(transcript_payload, events_payload)
            transcript_text = _transcript_text(call_events)
            answers = [payload.get('answer', '') for payload in ask_payloads]
            last_answer = answers[-1] if answers else ''
            lower_answer = last_answer.lower()
            pass_terms = all(term in lower_answer for term in expected_terms)
            citations_present = any(payload.get('citations') for payload in ask_payloads)
            status = 'pass' if pass_terms and citations_present else 'fail'

            return _with_report_fields({
                'scenario_id': scenario.scenario_id,
                'title': scenario.title,
                'adapter': self.name,
                'status': status,
                'started_at': created_at,
                'completed_at': max((event['timestamp'] for event in call_events), default=_utc_now()),
                'summary': last_answer,
                'call_id': session_id,
                'call_events': call_events,
                'transcript': transcript_text,
                'final_state': {
                    'session': session_payload,
                    'last_answer': last_answer,
                    'citations': ask_payloads[-1].get('citations', []) if ask_payloads else [],
                },
                'metrics': {
                    'turn_count': sum(1 for event in call_events if event['event_type'] == 'transcript_turn'),
                    'event_count': len(call_events),
                    'question_count': len(questions),
                },
                'artifacts': [
                    {
                        'type': 'session_ask_harness',
                        'path': f'/api/sessions/{session_id}/ask' if session_id else None,
                        'source': self.name,
                    },
                    {
                        'type': 'session_transcript',
                        'path': f'/api/sessions/{session_id}/transcript' if session_id else None,
                        'source': self.name,
                    },
                    {
                        'type': 'session_events',
                        'path': f'/api/sessions/{session_id}/events' if session_id else None,
                        'source': self.name,
                    }
                ],
                'evidence': {
                    'answers': answers,
                    'questions': questions,
                },
            })
        finally:
            db.close()


def seeded_voice_lab_scenarios() -> list[VoiceLabScenario]:
    return [
        VoiceLabScenario(
            scenario_id='acc-scripted-wrap',
            adapter='agentic-contact-center',
            title='Deterministic Cancellation Rescue Script',
            prompt='Drive the seeded cancellation rescue flow to a wrapped call without promising unsupported credit.',
            metadata={'expected_outcome': 'scripted_wrap_complete'},
        ),
        VoiceLabScenario(
            scenario_id='acc-fail-closed-fallback',
            adapter='agentic-contact-center',
            title='Deterministic Fail-Closed Fallback',
            prompt='Trigger the fail-closed fallback when the required tool exceeds its latency budget.',
            metadata={'expected_outcome': 'fail_closed_handoff'},
        ),
        VoiceLabScenario(
            scenario_id='deck-grounded-ask-loop',
            adapter='session-ask',
            title='Transcript Injection Ask Loop',
            prompt='Inject a grounded presentation question through the /ask harness and capture transcript plus citations.',
            metadata={
                'questions': ['What is the main value proposition for this voice lab proof loop?'],
                'expected_terms': ['voice lab proof loop', 'transcript'],
                'slides': [
                    {
                        'title': 'Voice Lab Proof Loop',
                        'summary': 'The voice lab proof loop shows the main value proposition: transcript, action trace, and final-state evidence in one fast local harness.',
                        'raw_text': 'Transcript, action trace, and final state evidence make regressions easy to spot before production.',
                        'talk_track': 'Keep the proof loop small, local, and evidence-rich.',
                    },
                    {
                        'title': 'Deterministic Adapters',
                        'summary': 'Deterministic adapters let the team replay seeded scenarios against local targets before live telephony.',
                        'raw_text': 'Use deterministic scenarios first, then layer in live voice when the contract is stable.',
                        'talk_track': 'Determinism makes reliability work cheaper and faster.',
                    },
                ],
            },
        ),
    ]


def build_default_voice_lab_runner(project_root: Path) -> VoiceLabRunner:
    return VoiceLabRunner(
        adapters=[
            AgenticContactCenterAdapter(repo_root=project_root.parent / 'agentic-contact-center', artifact_dir=project_root / 'artifacts' / 'voice-lab'),
            TranscriptInjectionAdapter(),
        ]
    )


def _enrich_result(result: dict[str, Any], scenario: VoiceLabScenario) -> dict[str, Any]:
    return {
        **result,
        'scenario': {
            'scenario_id': scenario.scenario_id,
            'title': scenario.title,
            'adapter': scenario.adapter,
            'prompt': scenario.prompt,
            'metadata': scenario.metadata,
            'execution_mode': _execution_mode_for_adapter(scenario.adapter),
        },
        'integration_status': _integration_status_for_adapter(scenario.adapter),
    }


def _execution_mode_for_adapter(adapter_name: str) -> str:
    if adapter_name == 'agentic-contact-center':
        return 'deterministic_fixture'
    if adapter_name == 'session-ask':
        return 'transcript_injection'
    return 'unknown'


def _integration_status_for_adapter(adapter_name: str) -> dict[str, Any]:
    if adapter_name == 'agentic-contact-center':
        return {
            'proof_stage': 'progressive_baseline',
            'supported_layers': [
                'deterministic contact-center flow execution',
                'structured transcript capture',
                'platform event trail capture',
                'latency mark capture',
                'artifact log persistence',
            ],
            'unsupported_layers': [
                'live_asr',
                'live_tts',
                'sip_trunk',
                'webrtc_media',
                'recorded_audio_waveforms',
            ],
            'next_integration_step': 'Run the same seeded scenario contract through the Pipecat or WebRTC path and persist audio, ASR turns, TTS turns, and transport logs beside the deterministic proof bundle.',
        }
    if adapter_name == 'session-ask':
        return {
            'proof_stage': 'progressive_fixture',
            'supported_layers': [
                'transcript-injected question loop',
                'citation capture',
                'session transcript export',
                'session event export',
            ],
            'unsupported_layers': [
                'live_asr',
                'live_tts',
                'sip_trunk',
                'webrtc_media',
                'barge_in_interruptions',
            ],
            'next_integration_step': 'Drive this same ask loop through a live Pipecat session so the evidence bundle can add media recordings and transcript-to-audio alignment for the identical scenario contract.',
        }
    return {
        'proof_stage': 'unknown',
        'supported_layers': [],
        'unsupported_layers': ['unknown'],
        'next_integration_step': 'Register a concrete adapter before claiming integrated voice coverage.',
    }


def _create_lab_deck(db, *, title: str, slides: list[dict[str, str]]) -> Deck:
    manifest_slides: list[dict[str, Any]] = []
    deck = Deck(
        title=title,
        pdf_path='',
        status='ready',
        slide_count=len(slides),
        manifest_json='{}',
    )
    db.add(deck)
    db.flush()

    for index, slide in enumerate(slides):
        manifest_slides.append({'index': index, 'title': slide['title']})
        db.add(
            Slide(
                deck_id=deck.id,
                index=index,
                title=slide['title'],
                raw_text=slide.get('raw_text', ''),
                summary=slide.get('summary', ''),
                talk_track=slide.get('talk_track', ''),
                faq_json='[]',
            )
        )

    deck.manifest_json = json.dumps({'slide_count': len(slides), 'slides': manifest_slides})
    db.commit()
    db.refresh(deck)
    return deck


def _normalize_contact_center_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[VoiceLabEvent] = []

    for index, turn in enumerate(snapshot.get('transcript', []), start=1):
        normalized.append(
            VoiceLabEvent(
                event_id=f'transcript-{index}',
                event_type='transcript_turn',
                timestamp=turn.get('timestamp') or _utc_now(),
                source='contact_center_transcript',
                speaker=turn.get('speaker'),
                text=turn.get('text'),
            )
        )

    for index, event in enumerate(snapshot.get('events', []), start=1):
        normalized.append(
            VoiceLabEvent(
                event_id=f'platform-{index}',
                event_type=event.get('type', 'platform_event'),
                timestamp=event.get('at') or _utc_now(),
                source='contact_center_event_trail',
                detail=event.get('detail') or {},
            )
        )

    for index, mark in enumerate(snapshot.get('latencyMarks', []), start=1):
        normalized.append(
            VoiceLabEvent(
                event_id=f'latency-{index}',
                event_type='latency_mark',
                timestamp=mark.get('recordedAt') or _utc_now(),
                source='contact_center_latency',
                detail={
                    'stage': mark.get('stage'),
                    'elapsed_ms': mark.get('elapsedMs'),
                    'budget_ms': mark.get('budgetMs'),
                },
            )
        )

    normalized.sort(key=lambda event: (event.timestamp, event.event_id))
    return [asdict(event) for event in normalized]


def _normalize_session_payload(transcript_payload: list[dict[str, Any]], events_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[VoiceLabEvent] = []

    for index, item in enumerate(transcript_payload, start=1):
        normalized.append(
            VoiceLabEvent(
                event_id=f'transcript-{index}',
                event_type='transcript_turn',
                timestamp=item.get('created_at') or _utc_now(),
                source='session_transcript',
                speaker=item.get('role'),
                text=item.get('text'),
            )
        )

    for index, item in enumerate(events_payload, start=1):
        detail = item.get('payload_json') or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                detail = {'raw_payload_json': detail}
        normalized.append(
            VoiceLabEvent(
                event_id=f'event-{index}',
                event_type=item.get('type', 'session_event'),
                timestamp=item.get('created_at') or _utc_now(),
                source='session_event',
                detail=detail if isinstance(detail, dict) else {'payload': detail},
            )
        )

    normalized.sort(key=lambda event: (event.timestamp, event.event_id))
    return [asdict(event) for event in normalized]


def _transcript_text(call_events: list[dict[str, Any]]) -> str:
    lines = [
        f"{(event.get('speaker') or event['source']).title()}: {event['text']}"
        for event in call_events
        if event['event_type'] == 'transcript_turn' and event.get('text')
    ]
    return '\n'.join(lines)


def _within_budget_marks(latency_marks: list[dict[str, Any]]) -> dict[str, int]:
    within_budget = 0
    over_budget = 0
    for mark in latency_marks:
        budget_ms = mark.get('budgetMs')
        elapsed_ms = mark.get('elapsedMs')
        if budget_ms is None or elapsed_ms is None:
            continue
        if elapsed_ms <= budget_ms:
            within_budget += 1
        else:
            over_budget += 1
    return {
        'within_budget': within_budget,
        'over_budget': over_budget,
    }


def _blocked_result(*, scenario: VoiceLabScenario, blocker: str, artifacts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    timestamp = _utc_now()
    return _with_report_fields({
        'scenario_id': scenario.scenario_id,
        'title': scenario.title,
        'adapter': scenario.adapter,
        'status': 'blocked',
        'started_at': timestamp,
        'completed_at': timestamp,
        'summary': blocker,
        'call_id': None,
        'call_events': [],
        'transcript': '',
        'final_state': {},
        'metrics': {},
        'artifacts': artifacts or [],
        'evidence': {'blocker': blocker},
    })


def _with_report_fields(result: dict[str, Any]) -> dict[str, Any]:
    status = result['status']
    if status == 'pass':
        scores = {
            'overall': 100,
            'task_completion': 100,
            'evidence_completeness': 100 if result.get('call_events') or result.get('artifacts') else 50,
        }
        verdict = 'pass'
    elif status == 'blocked':
        scores = {
            'overall': 0,
            'task_completion': 0,
            'evidence_completeness': 0,
        }
        verdict = 'blocked'
    else:
        scores = {
            'overall': 0,
            'task_completion': 0,
            'evidence_completeness': 50 if result.get('call_events') or result.get('artifacts') else 0,
        }
        verdict = 'fail'

    return {
        **result,
        'scores': scores,
        'verdict': verdict,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
