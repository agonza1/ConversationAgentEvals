from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas.benchmarks import BenchmarkRunRequest, BenchmarkSimulationRequest
from app.schemas.execution import (
    ConversationRecord,
    ConversationTurn,
    ExecutionRunCreateRequest,
    ExecutionRunProgress,
    ExecutionRunRecord,
)
from app.services import execution_run_store
from app.services.acc_realtime_target import (
    AccAudioFixture,
    AccAudioFixtureScheduler,
    AccAudioPlan,
    AccAudioStep,
)
from app.services.agentic_contact_center_example import build_benchmark_run_request, normalize_acc_run
from app.services.benchmark_catalog_extensions import register_builtin_benchmark_extensions
from app.services.benchmark_service import get_suite, run_scenario, simulate_scenario
from app.services.execution_audio import (
    WebRtcBackedVoiceTarget,
    create_execution_audio_session,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_VOICE_FIXTURE = 'docs/examples/agentic-contact-center-run-fixture.json'
DEFAULT_AUDIO_PLAN = 'docs/examples/agentic-contact-center-audio-plan.json'
DEFAULT_CANCELLATION_SCENARIO = 'docs/examples/agentic-contact-center-cancellation-rescue.json'
FIXTURE_BACKED_SCENARIO_IDS = frozenset({'cancellation-rescue'})
ALLOWED_FIXTURE_ROOTS = (
    REPO_ROOT / 'docs' / 'examples',
    REPO_ROOT / 'artifacts',
)


def start_execution_run(payload: ExecutionRunCreateRequest) -> dict[str, Any]:
    register_builtin_benchmark_extensions()
    suite = get_suite(payload.suite_id)
    if suite is None:
        raise ValueError(f'Unknown suite: {payload.suite_id}')

    scenario_ids = list(payload.scenario_ids)
    if not scenario_ids:
        scenario_ids = [item['id'] for item in suite.get('scenarios') or []]
        optional = suite.get('optional_scenarios') or []
        if payload.mode in {'voice_fixture', 'voice_webrtc'} and optional:
            scenario_ids = [optional[0]['id']]
        elif not scenario_ids and optional:
            scenario_ids = [optional[0]['id']]
    if not scenario_ids:
        raise ValueError('No scenarios selected for execution.')
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError('Duplicate scenario ids are not allowed.')

    _validate_scenarios(suite, scenario_ids)
    _validate_fixture_mode_scenarios(payload, scenario_ids)
    if payload.voice_fixture_path:
        _repo_path(payload.voice_fixture_path)
    if payload.audio_plan_path:
        _repo_path(payload.audio_plan_path)
    total = len(scenario_ids) * payload.iterations
    now = datetime.now(UTC).isoformat()
    execution_run_id = f'exec-{uuid.uuid4().hex[:12]}'
    record = ExecutionRunRecord(
        execution_run_id=execution_run_id,
        status='queued',
        mode=payload.mode,
        suite_id=payload.suite_id,
        scenario_ids=scenario_ids,
        user_id=payload.user_id,
        project_id=payload.project_id,
        progress=ExecutionRunProgress(
            phase='queued',
            completed_conversations=0,
            total_conversations=total,
            percent=0.0,
        ),
        created_at=now,
        updated_at=now,
    )
    return execution_run_store.create_execution_run(record)


def execute_execution_run(execution_run_id: str, payload: ExecutionRunCreateRequest) -> dict[str, Any]:
    register_builtin_benchmark_extensions()
    run = execution_run_store.mark_execution_run_running(execution_run_id)
    if run is None:
        raise ValueError(f'Unknown execution run: {execution_run_id}')

    output_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    inference_path = output_dir / 'inference_set.jsonl'

    try:
        jobs = [
            (scenario_id, iteration)
            for iteration in range(1, payload.iterations + 1)
            for scenario_id in run['scenario_ids']
        ]
        for scenario_id, iteration in jobs:
            conversation_id = f'{execution_run_id}-{scenario_id}-{iteration}'
            suite = get_suite(payload.suite_id) or {}
            execution_run_store.upsert_conversation(
                execution_run_id,
                ConversationRecord(
                    conversation_id=conversation_id,
                    execution_run_id=execution_run_id,
                    suite_id=payload.suite_id,
                    scenario_id=scenario_id,
                    scenario_title=_scenario_title(suite, scenario_id),
                    mode=payload.mode,
                    status='running',
                    iteration=iteration,
                    started_at=datetime.now(UTC).isoformat(),
                ),
            )
            conversation = _run_one_conversation(
                execution_run_id=execution_run_id,
                suite_id=payload.suite_id,
                scenario_id=scenario_id,
                iteration=iteration,
                payload=payload,
            )
            execution_run_store.upsert_conversation(execution_run_id, conversation)
            if conversation.status != 'failed':
                with inference_path.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(conversation.model_dump(mode='json'), ensure_ascii=True) + '\n')

        latest = execution_run_store.get_execution_run(execution_run_id) or {}
        failed = any(item.get('status') == 'failed' for item in latest.get('conversations') or [])
        reviewed = any(item.get('verdict') == 'needs_review' for item in latest.get('conversations') or [])
        status = 'failed' if failed else 'needs_review' if reviewed else 'completed'
        return execution_run_store.complete_execution_run(
            execution_run_id,
            status=status,
            inference_set_path=str(inference_path.relative_to(REPO_ROOT)),
        ) or latest
    except Exception as exc:
        return execution_run_store.mark_execution_run_failed(execution_run_id, str(exc)) or {
            'execution_run_id': execution_run_id,
            'status': 'failed',
            'error': str(exc),
        }


def _run_one_conversation(
    *,
    execution_run_id: str,
    suite_id: str,
    scenario_id: str,
    iteration: int,
    payload: ExecutionRunCreateRequest,
) -> ConversationRecord:
    started = datetime.now(UTC).isoformat()
    conversation_id = f'{execution_run_id}-{scenario_id}-{iteration}'
    suite = get_suite(suite_id) or {}
    scenario_title = _scenario_title(suite, scenario_id)

    try:
        if payload.mode == 'text_callable':
            result = _execute_text_callable(suite_id, scenario_id, payload)
        elif payload.mode in {'voice_fixture', 'voice_webrtc'}:
            result = asyncio.run(_execute_voice_fixture(suite_id, scenario_id, payload))
        else:
            raise ValueError(f'Unsupported execution mode: {payload.mode}')
        return ConversationRecord(
            conversation_id=conversation_id,
            execution_run_id=execution_run_id,
            suite_id=suite_id,
            scenario_id=scenario_id,
            scenario_title=scenario_title,
            mode=payload.mode,
            status='completed',
            iteration=iteration,
            turns=result['turns'],
            transcript=result.get('transcript'),
            action_trace=result.get('action_trace') or [],
            final_state=result.get('final_state') or {},
            latency_marks=result.get('latency_marks') or [],
            audio_session=result.get('audio_session'),
            verdict=result.get('verdict'),
            score=result.get('score'),
            started_at=started,
            completed_at=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        return ConversationRecord(
            conversation_id=conversation_id,
            execution_run_id=execution_run_id,
            suite_id=suite_id,
            scenario_id=scenario_id,
            scenario_title=scenario_title,
            mode=payload.mode,
            status='failed',
            iteration=iteration,
            error=str(exc),
            started_at=started,
            completed_at=datetime.now(UTC).isoformat(),
        )


def _execute_text_callable(suite_id: str, scenario_id: str, payload: ExecutionRunCreateRequest) -> dict[str, Any]:
    callable_id = payload.text_callable
    if callable_id == 'offline_acc_fixture':
        return _evidence_from_offline_fixture(suite_id, scenario_id, payload, evaluate=payload.evaluate)
    if callable_id != 'mock_agent':
        raise ValueError(f'Unsupported text callable: {callable_id}')

    simulation = simulate_scenario(
        BenchmarkSimulationRequest(
            suite_id=suite_id,
            scenario_id=scenario_id,
            agent_profile='execution text callable',
            include_failure=False,
            user_id=payload.user_id,
            project_id=payload.project_id,
        )
    )
    transcript = str(simulation.get('transcript') or '')
    action_trace = simulation.get('action_trace') if isinstance(simulation.get('action_trace'), list) else []
    final_state = simulation.get('final_state') if isinstance(simulation.get('final_state'), dict) else {}
    turns = _turns_from_transcript(transcript)
    report: dict[str, Any] = {}
    if payload.evaluate:
        # simulate_scenario always evaluates; only surface the report when evaluate=true.
        candidate = simulation.get('benchmark_report') if isinstance(simulation.get('benchmark_report'), dict) else {}
        report = candidate or run_scenario(
            BenchmarkRunRequest(
                suite_id=suite_id,
                scenario_id=scenario_id,
                transcript=transcript,
                action_trace=action_trace,
                final_state=final_state,
                user_id=payload.user_id,
                project_id=payload.project_id,
            )
        )
    return {
        'turns': turns,
        'transcript': transcript,
        'action_trace': action_trace,
        'final_state': final_state,
        'latency_marks': [],
        'verdict': report.get('verdict'),
        'score': report.get('overall_score'),
    }


def _evidence_from_offline_fixture(
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
    *,
    evaluate: bool,
) -> dict[str, Any]:
    fixture_path = _repo_path(payload.voice_fixture_path or DEFAULT_VOICE_FIXTURE)
    scenario_path = _repo_path(DEFAULT_CANCELLATION_SCENARIO)
    fixture = json.loads(fixture_path.read_text())
    scenario = json.loads(scenario_path.read_text())
    evidence = normalize_acc_run(fixture, scenario=scenario)
    turns = [
        ConversationTurn(
            turn_index=index,
            speaker=str(item.get('speaker') or 'unknown'),
            text=str(item.get('text') or ''),
        )
        for index, item in enumerate((evidence.get('conversation') or {}).get('dialog') or [], start=1)
    ]
    report: dict[str, Any] = {}
    if evaluate:
        request = build_benchmark_run_request(
            evidence,
            scenario=scenario,
            user_id=payload.user_id,
            project_id=payload.project_id,
        )
        # Force suite/scenario into cancellation-rescue when that is the selected catalog id.
        if scenario_id == 'cancellation-rescue':
            request = request.model_copy(update={'suite_id': suite_id, 'scenario_id': scenario_id})
        report = run_scenario(request)
    return {
        'turns': turns,
        'transcript': evidence.get('transcript'),
        'action_trace': evidence.get('action_trace') or [],
        'final_state': evidence.get('final_state') or {},
        'latency_marks': (evidence.get('latency_evidence') or {}).get('marks') or [],
        'verdict': report.get('verdict'),
        'score': report.get('overall_score'),
    }


async def _execute_voice_fixture(suite_id: str, scenario_id: str, payload: ExecutionRunCreateRequest) -> dict[str, Any]:
    plan = _load_audio_plan(payload.audio_plan_path or DEFAULT_AUDIO_PLAN, scenario_id=scenario_id)
    audio_transport = payload.audio_transport
    if payload.mode == 'voice_webrtc' and audio_transport == 'none':
        audio_transport = 'local_pipecat_webrtc'

    audio_session = None
    media_steps: list[dict[str, Any]] = []
    if audio_transport == 'local_pipecat_webrtc':
        audio_session = create_execution_audio_session(
            'local_pipecat_webrtc',
            metadata={
                'execution_mode': payload.mode,
                'suite_id': suite_id,
                'scenario_id': scenario_id,
                'user_id': payload.user_id,
                'project_id': payload.project_id,
            },
        )
        await audio_session.negotiate()
        target = WebRtcBackedVoiceTarget(audio_session)
    else:
        target = _VoiceFixtureTarget()

    scheduler = AccAudioFixtureScheduler(target, sleeper=_fast_sleep, event_poll_interval_seconds=0.01)
    inject_results = await scheduler.run(
        audio_session.session_id if audio_session is not None else 'voice-fixture-session',
        plan,
    )
    if isinstance(target, WebRtcBackedVoiceTarget):
        media_steps = list(target.media_steps)

    turns = [
        ConversationTurn(
            turn_index=index,
            speaker='caller',
            act_id=str(item.get('expected_caller_act') or ''),
            text=str((item.get('response') or {}).get('utterance') or item.get('fixture_id') or ''),
            event_types=['audio_injected']
            + (['webrtc_audio_streamed'] if audio_session is not None else []),
        )
        for index, item in enumerate(inject_results, start=1)
    ]
    evidence = _evidence_from_offline_fixture(suite_id, scenario_id, payload, evaluate=payload.evaluate)
    # Prefer fixture dialog turns when present; keep injection acts as leading provenance.
    if evidence['turns']:
        offset = len(turns)
        for item in evidence['turns']:
            turns.append(
                ConversationTurn(
                    turn_index=offset + item.turn_index,
                    speaker=item.speaker,
                    text=item.text,
                    latency_ms=item.latency_ms,
                )
            )

    audio_proof: dict[str, Any] | None = None
    if audio_session is not None:
        closed = await audio_session.close(reason='execution_complete')
        audio_proof = {
            **closed.model_dump(mode='json'),
            'media_steps': media_steps,
            'extension_points': {
                'sip_verto': {
                    'status': 'deferred',
                    'note': (
                        'Attach FreeSWITCH Verto outbound SIP to the same SmallWebRTC PCM session '
                        'after local in/out hooks are proven. Not required for default CI.'
                    ),
                }
            },
        }

    return {
        **evidence,
        'turns': turns,
        'latency_marks': evidence.get('latency_marks') or [],
        'audio_session': audio_proof,
    }


class _VoiceFixtureTarget:
    async def observe_events(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        # Emit the common event gates from the checked-in audio plan so waits resolve
        # without depending on a live ACC session.
        return {
            'events': [
                {'type': 'agent_response_completed', 'detail': {'text': 'Ready for next act.'}},
                {'type': 'agent_tts_started', 'detail': {}},
                {'type': 'operator_steer_applied', 'detail': {'action': 'approve_offer'}},
            ],
            'next_cursor': 'cursor-1',
        }

    async def inject_audio(
        self,
        session_id: str,
        *,
        fixture: AccAudioFixture,
        step: AccAudioStep,
        scenario_id: str,
        seed: int,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            'accepted': True,
            'session_id': session_id,
            'fixture_id': fixture.fixture_id,
            'utterance': fixture.metadata.get('text_reference')
            or fixture.metadata.get('rendered_text')
            or step.expected_caller_act,
            'scenario_id': scenario_id,
            'seed': seed,
            'provenance': provenance,
        }


def _load_audio_plan(relative_path: str, *, scenario_id: str) -> AccAudioPlan:
    path = _repo_path(relative_path)
    raw = json.loads(path.read_text())
    fixtures = [
        AccAudioFixture(
            fixture_id=str(item['fixture_id']),
            uri=str(item.get('uri') or f'fixture://{item["fixture_id"]}'),
            expected_caller_act=str(item.get('expected_caller_act') or item['fixture_id']),
            duration_ms=item.get('duration_ms'),
            sha256=item.get('sha256'),
            metadata=item.get('metadata') if isinstance(item.get('metadata'), dict) else {},
        )
        for item in raw.get('fixtures') or []
    ]
    steps = [
        AccAudioStep(
            step_id=str(item['step_id']),
            fixture_id=str(item['fixture_id']),
            expected_caller_act=str(item.get('expected_caller_act') or item['fixture_id']),
            delay_after_previous_ms=int(item.get('delay_after_previous_ms') or 0),
            wait_for_event=item.get('wait_for_event'),
            wait_timeout_seconds=float(item.get('wait_timeout_seconds') or (0.05 if item.get('wait_for_event') else 20.0)),
            pacing_mode=item.get('pacing_mode') or 'accelerated',
            acceleration_factor=float(item.get('acceleration_factor') or (4.0 if (item.get('pacing_mode') or 'accelerated') == 'accelerated' else 1.0)),
            barge_in=bool(item.get('barge_in')),
            metadata=item.get('metadata') if isinstance(item.get('metadata'), dict) else {},
        )
        for item in raw.get('steps') or []
    ]
    if not fixtures or not steps:
        raise ValueError(f'Audio plan is missing fixtures/steps: {path}')
    return AccAudioPlan(
        scenario_id=str(raw.get('scenario_id') or scenario_id),
        seed=int(raw.get('seed') or 1),
        provenance=raw.get('provenance') if isinstance(raw.get('provenance'), dict) else {'source': 'execution-runner'},
        fixtures=fixtures,
        steps=steps,
    )


def _turns_from_transcript(transcript: str) -> list[ConversationTurn]:
    turns: list[ConversationTurn] = []
    for index, line in enumerate(transcript.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        speaker = 'agent'
        body = text
        if ':' in text:
            maybe_speaker, maybe_body = text.split(':', 1)
            if maybe_speaker.strip():
                speaker = maybe_speaker.strip().lower()
                body = maybe_body.strip()
        turns.append(ConversationTurn(turn_index=index, speaker=speaker, text=body))
    return turns


def _validate_scenarios(suite: dict[str, Any], scenario_ids: list[str]) -> None:
    known = {item['id'] for item in suite.get('scenarios') or []}
    known.update(item['id'] for item in suite.get('optional_scenarios') or [])
    missing = [scenario_id for scenario_id in scenario_ids if scenario_id not in known]
    if missing:
        raise ValueError(f'Unknown scenario ids for suite: {", ".join(missing)}')


def _validate_fixture_mode_scenarios(payload: ExecutionRunCreateRequest, scenario_ids: list[str]) -> None:
    uses_fixture = (
        payload.mode in {'voice_fixture', 'voice_webrtc'}
        or payload.text_callable == 'offline_acc_fixture'
    )
    if not uses_fixture:
        return
    unsupported = [scenario_id for scenario_id in scenario_ids if scenario_id not in FIXTURE_BACKED_SCENARIO_IDS]
    if unsupported:
        raise ValueError(
            'Fixture-backed execution only supports cancellation-rescue; '
            f'unsupported: {", ".join(unsupported)}'
        )


def _scenario_title(suite: dict[str, Any], scenario_id: str) -> str | None:
    for collection in (suite.get('scenarios') or [], suite.get('optional_scenarios') or []):
        for item in collection:
            if item.get('id') == scenario_id:
                return item.get('title')
    return None


def _repo_path(relative: str) -> Path:
    """Resolve fixture/plan paths and reject anything outside allowlisted repo roots."""
    candidate = Path(relative)
    path = candidate if candidate.is_absolute() else (REPO_ROOT / candidate)
    resolved = path.resolve()
    allowed_roots = [root.resolve() for root in ALLOWED_FIXTURE_ROOTS]
    if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
        raise ValueError(f'Fixture path must stay under docs/examples or artifacts: {relative}')
    if not resolved.is_file():
        raise ValueError(f'Missing file: {relative}')
    return resolved


async def _fast_sleep(_seconds: float) -> None:
    return None
