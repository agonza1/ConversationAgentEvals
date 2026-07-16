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
from app.services.agent_store import get_agent
from app.services.execution_metrics import build_metrics_and_timeline
from app.services.acc_realtime_target import (
    AccAudioFixture,
    AccAudioFixtureScheduler,
    AccAudioPlan,
    AccAudioStep,
    TesterAct,
    TesterScenarioConfig,
)
from app.services.agentic_contact_center_example import build_benchmark_run_request, normalize_acc_run
from app.services.benchmark_catalog_extensions import register_builtin_benchmark_extensions
from app.services.benchmark_service import get_suite, run_scenario, simulate_scenario
from app.services.execution_audio import (
    DeterministicExecutionTtsRenderer,
    ExecutionAudioTargetAdapter,
    LocalPipecatSmallWebRtcTransport,
)
from app.services.execution_vcon import build_execution_vcon, vcon_summary
from app.services.pipecat_tester_agent import PipecatTesterAgentRunner


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
    resolved = _resolve_agent_payload(payload)
    suite = get_suite(resolved.suite_id)
    if suite is None:
        raise ValueError(f'Unknown suite: {resolved.suite_id}')

    scenario_ids = list(resolved.scenario_ids)
    if not scenario_ids:
        scenario_ids = [item['id'] for item in suite.get('scenarios') or []]
        optional = suite.get('optional_scenarios') or []
        if resolved.mode in {'voice_fixture', 'pipecat_webrtc'} and optional:
            scenario_ids = [optional[0]['id']]
        elif not scenario_ids and optional:
            scenario_ids = [optional[0]['id']]
    if not scenario_ids:
        raise ValueError('No scenarios selected for execution.')
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError('Duplicate scenario ids are not allowed.')

    _validate_scenarios(suite, scenario_ids)
    _validate_fixture_mode_scenarios(resolved, scenario_ids)
    if resolved.voice_fixture_path:
        _repo_path(resolved.voice_fixture_path)
    if resolved.audio_plan_path:
        _repo_path(resolved.audio_plan_path)
    total = len(scenario_ids) * resolved.iterations
    now = datetime.now(UTC).isoformat()
    execution_run_id = f'exec-{uuid.uuid4().hex[:12]}'
    agent = get_agent(resolved.agent_id) if resolved.agent_id else None
    record = ExecutionRunRecord(
        execution_run_id=execution_run_id,
        status='queued',
        mode=resolved.mode,
        suite_id=resolved.suite_id,
        scenario_ids=scenario_ids,
        user_id=resolved.user_id,
        project_id=resolved.project_id,
        agent_id=resolved.agent_id,
        agent_name=(agent or {}).get('name'),
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
    resolved = _resolve_agent_payload(payload)
    run = execution_run_store.mark_execution_run_running(execution_run_id)
    if run is None:
        raise ValueError(f'Unknown execution run: {execution_run_id}')

    output_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    inference_path = output_dir / 'inference_set.jsonl'

    try:
        jobs = [
            (scenario_id, iteration)
            for iteration in range(1, resolved.iterations + 1)
            for scenario_id in run['scenario_ids']
        ]
        for scenario_id, iteration in jobs:
            conversation_id = f'{execution_run_id}-{scenario_id}-{iteration}'
            suite = get_suite(resolved.suite_id) or {}
            execution_run_store.upsert_conversation(
                execution_run_id,
                ConversationRecord(
                    conversation_id=conversation_id,
                    execution_run_id=execution_run_id,
                    suite_id=resolved.suite_id,
                    scenario_id=scenario_id,
                    scenario_title=_scenario_title(suite, scenario_id),
                    mode=resolved.mode,
                    status='running',
                    iteration=iteration,
                    started_at=datetime.now(UTC).isoformat(),
                ),
            )
            conversation = _run_one_conversation(
                execution_run_id=execution_run_id,
                suite_id=resolved.suite_id,
                scenario_id=scenario_id,
                iteration=iteration,
                payload=resolved,
            )
            execution_run_store.upsert_conversation(execution_run_id, conversation)
            if conversation.status != 'failed':
                with inference_path.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(conversation.model_dump(mode='json'), ensure_ascii=True) + '\n')

        latest = execution_run_store.get_execution_run(execution_run_id) or {}
        failed = any(item.get('status') == 'failed' for item in latest.get('conversations') or [])
        reviewed = any(item.get('verdict') == 'needs_review' for item in latest.get('conversations') or [])
        status = 'failed' if failed else 'needs_review' if reviewed else 'completed'
        snapshot_rel = str((output_dir / 'run.json').relative_to(REPO_ROOT))
        return execution_run_store.complete_execution_run(
            execution_run_id,
            status=status,
            inference_set_path=str(inference_path.relative_to(REPO_ROOT)),
            run_snapshot_path=snapshot_rel,
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
        elif payload.mode == 'pipecat_webrtc':
            result = asyncio.run(
                _execute_pipecat_webrtc(
                    execution_run_id=execution_run_id,
                    conversation_id=conversation_id,
                    suite_id=suite_id,
                    scenario_id=scenario_id,
                    payload=payload,
                )
            )
        else:
            result = asyncio.run(_execute_voice_fixture(suite_id, scenario_id, payload))
        metrics_summary, timeline = build_metrics_and_timeline(
            turns=result['turns'],
            latency_marks=result.get('latency_marks') or [],
            verdict=result.get('verdict'),
            score=result.get('score'),
        )
        return ConversationRecord(
            conversation_id=conversation_id,
            execution_run_id=execution_run_id,
            suite_id=suite_id,
            scenario_id=scenario_id,
            scenario_title=scenario_title,
            mode=payload.mode,
            status='failed' if result.get('verdict') in {'fail', 'failed'} else 'completed',
            iteration=iteration,
            turns=result['turns'],
            transcript=result.get('transcript'),
            action_trace=result.get('action_trace') or [],
            final_state=result.get('final_state') or {},
            latency_marks=result.get('latency_marks') or [],
            metrics_summary=metrics_summary,
            timeline=timeline,
            recording=result.get('recording'),
            vcon_export=result.get('vcon_export'),
            vcon_export_summary=result.get('vcon_export_summary'),
            audio_session=result.get('audio_session'),
            verdict=result.get('verdict'),
            score=result.get('score'),
            started_at=started,
            completed_at=datetime.now(UTC).isoformat(),
            error=(
                str((result.get('final_state') or {}).get('tester_error'))
                if (result.get('final_state') or {}).get('tester_error')
                else None
            ),
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


def _resolve_agent_payload(payload: ExecutionRunCreateRequest) -> ExecutionRunCreateRequest:
    if not payload.agent_id:
        return payload
    agent = get_agent(payload.agent_id)
    if agent is None:
        raise ValueError(f'Unknown agent: {payload.agent_id}')
    target = str(agent.get('target') or 'mock_agent')
    channel = str(agent.get('channel') or 'text')
    if target in {'voice_fixture', 'offline_acc_fixture'} or channel == 'voice':
        mode = 'voice_fixture'
        text_callable = payload.text_callable
    else:
        mode = 'text_callable'
        text_callable = target if target in {'mock_agent', 'offline_acc_fixture'} else 'mock_agent'
    return payload.model_copy(
        update={
            'mode': mode,
            'text_callable': text_callable,
            'agent_id': agent['id'],
        }
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


async def _execute_pipecat_webrtc(
    *,
    execution_run_id: str,
    conversation_id: str,
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
) -> dict[str, Any]:
    """Drive Pipecat tester over local small WebRTC hooks and emit vCon evidence."""
    if payload.audio_transport != 'pipecat_small_webrtc':
        raise ValueError(
            'pipecat_webrtc execution currently supports audio_transport=pipecat_small_webrtc only; '
            'freeswitch_verto_sip is deferred'
        )

    artifact_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id / 'audio'
    transport = LocalPipecatSmallWebRtcTransport(artifact_dir=artifact_dir)
    target = ExecutionAudioTargetAdapter(transport)
    runner = PipecatTesterAgentRunner(
        target=target,
        tts_renderer=DeterministicExecutionTtsRenderer(),
    )
    tester_result = await runner.run(_pipecat_webrtc_tester_config(scenario_id))
    session_id = str(tester_result.get('session_id') or '')
    if not session_id:
        raise RuntimeError('pipecat_webrtc execution did not produce a session id')

    tester_status = str(tester_result.get('status') or '')
    tester_error = tester_result.get('error')
    tester_failed = tester_status in {'failed', 'needs_review'} or bool(tester_error)

    transcription = transport.transcription_turns(session_id)
    recording = transport.recording_handle(session_id)
    if recording is None:
        recording = await transport.stop_recording(session_id)

    vcon_export = build_execution_vcon(
        conversation_id=conversation_id,
        execution_run_id=execution_run_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
        transport=transport.transport_id,
        transcription_turns=transcription,
        recording=recording,
        termination_reason=tester_result.get('termination_reason'),
        tester_provenance=tester_result.get('tester_provenance')
        if isinstance(tester_result.get('tester_provenance'), dict)
        else {},
        extra_analysis_body={
            'tester_status': tester_result.get('status'),
            'tester_error': tester_result.get('error'),
            'turn_count': len(tester_result.get('turns') or []),
        },
    )

    turns = [
        ConversationTurn(
            turn_index=item.turn_index,
            speaker=item.speaker.lower(),
            text=item.text,
            act_id=item.act_id,
            event_types=list(item.event_types),
        )
        for item in transcription
    ]
    transcript = '\n'.join(
        f'{item.speaker}: {item.text}' for item in transcription if item.text.strip()
    )

    # Scoring remains fixture-backed for cancellation-rescue until a live SUT proof
    # path lands; capture (recording + dialog + vCon) comes from the WebRTC session.
    # Do not let offline fixture pass mask a failed/needs_review tester run.
    evidence = _evidence_from_offline_fixture(
        suite_id,
        scenario_id,
        payload,
        evaluate=payload.evaluate and not tester_failed,
    )
    if tester_failed:
        verdict = 'fail' if tester_status == 'failed' else 'needs_review'
        score = None
    else:
        verdict = evidence.get('verdict')
        score = evidence.get('score')
    return {
        'turns': turns or evidence['turns'],
        'transcript': transcript or evidence.get('transcript'),
        'action_trace': evidence.get('action_trace') or [],
        'final_state': {
            **(evidence.get('final_state') or {}),
            'audio_transport': transport.transport_id,
            'tester_termination_reason': tester_result.get('termination_reason'),
            'tester_error': tester_error,
        },
        'latency_marks': evidence.get('latency_marks') or [],
        'recording': recording.as_call_media(),
        'vcon_export': vcon_export,
        'vcon_export_summary': vcon_summary(vcon_export),
        'audio_session': {
            **transport.session_proof(session_id),
            'tester_status': tester_result.get('status'),
            'tester_error': tester_error,
            'proof': tester_result.get('proof'),
            'extension_points': {
                'freeswitch_verto_sip': {
                    'status': 'deferred',
                    'note': (
                        'Attach FreeSWITCH Verto outbound SIP to the same Pipecat small WebRTC '
                        'send/receive + recording/transcription/vCon surface. Not required for CI.'
                    ),
                }
            },
        },
        'verdict': verdict,
        'score': score,
    }


async def _execute_voice_fixture(suite_id: str, scenario_id: str, payload: ExecutionRunCreateRequest) -> dict[str, Any]:
    plan = _load_audio_plan(payload.audio_plan_path or DEFAULT_AUDIO_PLAN, scenario_id=scenario_id)
    target = _VoiceFixtureTarget()
    scheduler = AccAudioFixtureScheduler(target, sleeper=_fast_sleep, event_poll_interval_seconds=0.01)
    inject_results = await scheduler.run('voice-fixture-session', plan)
    turns = [
        ConversationTurn(
            turn_index=index,
            speaker='caller',
            act_id=str(item.get('expected_caller_act') or ''),
            text=str((item.get('response') or {}).get('utterance') or item.get('fixture_id') or ''),
            event_types=['audio_injected'],
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
    return {
        **evidence,
        'turns': turns,
        'latency_marks': evidence.get('latency_marks') or [],
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
        payload.mode in {'voice_fixture', 'pipecat_webrtc'}
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


def _pipecat_webrtc_tester_config(scenario_id: str) -> TesterScenarioConfig:
    return TesterScenarioConfig(
        scenario_id=scenario_id,
        goal='Drive local Pipecat small WebRTC audio in/out and capture vCon evidence.',
        allowed_caller_acts=[
            'request_cancellation',
            'explain_renewal_increase',
            'request_final_disposition',
        ],
        acts=[
            TesterAct(
                act_id='request_cancellation',
                objective='Request cancellation.',
                example_utterance='I want to cancel my policy today.',
            ),
            TesterAct(
                act_id='explain_renewal_increase',
                objective='Explain the renewal concern.',
                example_utterance='The renewal increase is too high.',
                metadata={'barge_in': True},
            ),
            TesterAct(
                act_id='request_final_disposition',
                objective='Request safe closeout.',
                example_utterance='Please record the approved follow-up and close the call.',
                terminal_after=True,
            ),
        ],
        max_turns=3,
        total_timeout_seconds=30,
        terminal_event_types=['call_wrapped', 'human_handoff_started'],
        terminal_final_states=['scripted_wrap_complete', 'human_handoff'],
        observation_mode='semantic',
        seed=20260715,
        model_version='execution-caller-v1',
        prompt_version='execution-caller-prompt-v1',
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
