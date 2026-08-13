from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.schemas.benchmarks import BenchmarkRunRequest, BenchmarkSimulationRequest
from app.schemas.execution import (
    ConversationRecord,
    ConversationTurn,
    ExecutionRunCreateRequest,
    ExecutionRunProgress,
    ExecutionRunRecord,
    LiveExecutionEvent,
)
from app.services import execution_run_store
from app.services.pipecat_public_target import run_public_pipecat_call
from app.services.signalwire_holyguacamole_target import (
    SIGNALWIRE_PUBLIC_GATE_ENV,
    run_signalwire_holyguacamole_call,
)
from app.services.agent_store import get_agent
from app.services.execution_metrics import build_metrics_and_timeline
from app.services.acc_realtime_target import (
    AccAudioFixture,
    AccAudioFixtureScheduler,
    AccAudioPlan,
    AccAudioStep,
)
from app.services.agentic_contact_center_example import build_benchmark_run_request, normalize_acc_run
from app.services.benchmark_catalog_extensions import register_builtin_benchmark_extensions
from app.services.benchmark_service import get_suite, run_scenario, simulate_scenario
from app.services.execution_vcon import build_execution_vcon, vcon_summary
from app.services.reference_generalist_agent import (
    ReferencePipecatAgentTransport,
    ReferenceMediaServices,
    ReferenceRuntimeConfig,
    resolve_reference_completion_provider,
)
from app.services.run_provenance import (
    assert_execution_compatible,
    build_run_provenance,
    execution_defaults_for_target,
)
from app.services.target_secrets import resolve_http_target_secret


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_VOICE_FIXTURE = 'docs/examples/agentic-contact-center-run-fixture.json'
DEFAULT_AUDIO_PLAN = 'docs/examples/agentic-contact-center-audio-plan.json'
DEFAULT_CANCELLATION_SCENARIO = 'docs/examples/agentic-contact-center-cancellation-rescue.json'
DEFAULT_EXECUTION_MODEL = 'gpt-5.4-mini'
PUBLIC_PIPECAT_AGENT = '10-gradium'
SIGNALWIRE_HOLY_GUACAMOLE_MODEL = 'signalwire-ai-agent'
FIXTURE_BACKED_SCENARIO_IDS = frozenset({'cancellation-rescue'})
ALLOWED_FIXTURE_ROOTS = (
    REPO_ROOT / 'docs' / 'examples',
    REPO_ROOT / 'artifacts',
)
EVALUATION_FINDING_KEYS = (
    'verdict',
    'overall_score',
    'required_action_score',
    'rubric_score',
    'task_completion_score',
    'forbidden_action_score',
    'final_state_score',
    'workflow_order_score',
    'scoring_mode',
    'score_components',
    'completed_actions',
    'missing_actions',
    'forbidden_action_hits',
    'rubric_checks',
    'hard_check_failures',
    'failure_categories',
    'failure_modes',
    'suggested_fixes',
    'scenario_contract',
    'expected_final_state',
)


def _compact_evaluation_findings(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    return {
        key: report[key]
        for key in EVALUATION_FINDING_KEYS
        if key in report
    }


def start_execution_run(payload: ExecutionRunCreateRequest, *, preflight: bool = False) -> dict[str, Any]:
    register_builtin_benchmark_extensions()
    resolved = _resolve_agent_payload(payload)
    if (
        resolved.mode == 'text_callable'
        and resolved.text_callable in {'openai_codex', 'http_endpoint'}
        and not resolved.agent_id
    ):
        raise ValueError(f'{resolved.text_callable} execution requires an agent_id.')
    suite = get_suite(resolved.suite_id)
    if suite is None:
        raise ValueError(f'Unknown suite: {resolved.suite_id}')

    scenario_ids = list(resolved.scenario_ids)
    if not scenario_ids:
        scenario_ids = [item['id'] for item in suite.get('scenarios') or []]
        optional = suite.get('optional_scenarios') or []
        if (
            resolved.mode in {'voice_fixture', 'pipecat_webrtc'}
            or resolved.text_callable == 'offline_acc_fixture'
        ) and optional:
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
    if preflight and resolved.mode == 'pipecat_webrtc' and resolved.executor_id == 'cae_local_audio_loop':
        _preflight_reference_runtime(resolved, execution_run_id=f'preflight-{uuid.uuid4().hex[:12]}')
    agent = get_agent(resolved.agent_id) if resolved.agent_id else None
    if resolved.agent_id and agent is None:
        raise ValueError(f'Unknown agent: {resolved.agent_id}')
    target = _execution_target(resolved, agent)
    if target == 'signalwire_holy_guacamole' and not _signalwire_public_gate_enabled():
        raise ValueError(
            f'Holy Guacamole SignalWire execution requires '
            f'{SIGNALWIRE_PUBLIC_GATE_ENV}=1 before queueing or tester preflight.'
        )
    if target == 'signalwire_holy_guacamole':
        _preflight_signalwire_caller_tts_runtime(resolved)
    if (
        resolved.max_exchanges > 1
        and target == 'signalwire_holy_guacamole'
    ):
        _preflight_signalwire_followup_runtime(resolved)
    total = len(scenario_ids) * resolved.iterations
    now = datetime.now(UTC).isoformat()
    execution_run_id = f'exec-{uuid.uuid4().hex[:12]}'
    model_name = (resolved.model_name or '').strip() or DEFAULT_EXECUTION_MODEL
    provenance = build_run_provenance(
        agent=agent,
        agent_target=target,
        tester_id=resolved.tester_id,
        executor_id=resolved.executor_id,
        mode=resolved.mode,
        text_callable=resolved.text_callable,
    )
    record = ExecutionRunRecord(
        execution_run_id=execution_run_id,
        status='queued',
        mode=resolved.mode,
        suite_id=resolved.suite_id,
        scenario_ids=scenario_ids,
        user_id=resolved.user_id,
        project_id=resolved.project_id,
        product_project_id=resolved.product_project_id,
        agent_id=resolved.agent_id,
        agent_name=(agent or {}).get('name'),
        model_name=model_name,
        max_exchanges=resolved.max_exchanges,
        duplex_timeout_seconds=resolved.duplex_timeout_seconds,
        tester_id=resolved.tester_id,
        tester_model_name=resolved.tester_model_name,
        executor_id=resolved.executor_id,
        provenance=provenance,
        execution_snapshot={
            'request': resolved.model_dump(mode='json'),
            'agent': agent,
            'provenance': provenance.model_dump(mode='json'),
        },
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
    resolved, agent_snapshot = _queued_execution_context(run, fallback=payload)

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
                agent_snapshot=agent_snapshot,
            )
            execution_run_store.upsert_conversation(execution_run_id, conversation)
            # Failed conversations are evaluation evidence too. Preserve them in
            # the inference set so all-failed and mixed runs remain auditable.
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


def _live_event_publisher(
    *,
    execution_run_id: str,
    conversation_id: str,
    user_id: str,
) -> Callable[[dict[str, Any]], None]:
    sequence = 0
    live_audio_sequences: dict[str, int] = {}

    def publish(payload: dict[str, Any]) -> None:
        nonlocal sequence
        update_key = str(payload.get('update_live_audio_key') or '').strip()
        if update_key:
            event_sequence = live_audio_sequences.get(update_key)
            if event_sequence is not None:
                audio = payload.get('audio')
                media_url = None
                mime_type = None
                kind = None
                if isinstance(audio, bytes) and audio:
                    kind = 'audio'
                    mime_type = 'audio/wav'
                    live_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id / 'audio' / 'live'
                    live_dir.mkdir(parents=True, exist_ok=True)
                    (live_dir / f'{conversation_id}-{event_sequence}.wav').write_bytes(audio)
                    media_url = (
                        f'/api/execution/runs/{quote(execution_run_id)}/conversations/'
                        f'{quote(conversation_id)}/audio/{event_sequence}?user_id={quote(user_id)}'
                    )
                execution_run_store.update_live_event(
                    execution_run_id,
                    conversation_id,
                    event_sequence,
                    text=str(payload.get('text') or '').strip(),
                    llm_output=str(payload.get('llm_output') or '').strip(),
                    asr_receipt=str(payload.get('asr_receipt') or '').strip(),
                    frame_metadata=(
                        payload.get('frame_metadata')
                        if isinstance(payload.get('frame_metadata'), dict)
                        else {}
                    ),
                    kind=kind,
                    media_url=media_url,
                    mime_type=mime_type,
                )
                return
        speaker = str(payload.get('speaker') or 'System').strip()
        text = str(payload.get('text') or '').strip()
        if not text:
            return
        sequence += 1
        audio = payload.get('audio')
        media_url = None
        mime_type = None
        kind = 'message'
        if isinstance(audio, bytes) and audio:
            kind = 'audio'
            mime_type = 'audio/wav'
            live_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id / 'audio' / 'live'
            live_dir.mkdir(parents=True, exist_ok=True)
            (live_dir / f'{conversation_id}-{sequence}.wav').write_bytes(audio)
            media_url = (
                f'/api/execution/runs/{quote(execution_run_id)}/conversations/'
                f'{quote(conversation_id)}/audio/{sequence}?user_id={quote(user_id)}'
            )
        updated = execution_run_store.append_live_event(
            execution_run_id,
            conversation_id,
            LiveExecutionEvent(
                sequence=sequence,
                kind=kind,
                speaker=speaker,
                text=text,
                media_url=media_url,
                mime_type=mime_type,
                direction=payload.get('direction'),
                llm_output=(str(payload.get('llm_output')).strip() if payload.get('llm_output') else None),
                asr_receipt=(str(payload.get('asr_receipt')).strip() if payload.get('asr_receipt') else None),
                frame_metadata=(
                    payload.get('frame_metadata')
                    if isinstance(payload.get('frame_metadata'), dict)
                    else {}
                ),
                created_at=datetime.now(UTC).isoformat(),
            ),
        )
        live_audio_key = str(payload.get('live_audio_key') or '').strip()
        if updated is not None and live_audio_key:
            live_audio_sequences[live_audio_key] = sequence

    return publish


def _run_one_conversation(
    *,
    execution_run_id: str,
    suite_id: str,
    scenario_id: str,
    iteration: int,
    payload: ExecutionRunCreateRequest,
    agent_snapshot: dict[str, Any] | None = None,
) -> ConversationRecord:
    started = datetime.now(UTC).isoformat()
    conversation_id = f'{execution_run_id}-{scenario_id}-{iteration}'
    suite = get_suite(suite_id) or {}
    scenario_title = _scenario_title(suite, scenario_id)
    publish = _live_event_publisher(
        execution_run_id=execution_run_id,
        conversation_id=conversation_id,
        user_id=payload.user_id,
    )

    try:
        if payload.mode == 'text_callable':
            result = _execute_text_callable(
                suite_id,
                scenario_id,
                payload,
                agent_snapshot=agent_snapshot,
                event_observer=publish,
            )
        elif payload.mode == 'pipecat_webrtc' and payload.executor_id == 'pipecat_public_daily':
            result = _execute_public_pipecat_daily(
                execution_run_id=execution_run_id,
                conversation_id=conversation_id,
                suite_id=suite_id,
                scenario_id=scenario_id,
                payload=payload,
                event_observer=publish,
            )
        elif payload.mode == 'pipecat_webrtc' and payload.executor_id == 'signalwire_public_webrtc':
            result = _execute_signalwire_holyguacamole_webrtc(
                execution_run_id=execution_run_id,
                conversation_id=conversation_id,
                suite_id=suite_id,
                scenario_id=scenario_id,
                payload=payload,
                event_observer=publish,
            )
        elif payload.mode == 'pipecat_webrtc':
            result = asyncio.run(
                _execute_pipecat_webrtc(
                    execution_run_id=execution_run_id,
                    conversation_id=conversation_id,
                    suite_id=suite_id,
                    scenario_id=scenario_id,
                    payload=payload,
                    event_observer=publish,
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
        current = execution_run_store.get_conversation(execution_run_id, conversation_id) or {}
        verdict = result.get('verdict')
        status = (
            'failed' if verdict in {'fail', 'failed'}
            else 'needs_review' if verdict == 'needs_review'
            else 'completed'
        )
        return ConversationRecord(
            conversation_id=conversation_id,
            execution_run_id=execution_run_id,
            suite_id=suite_id,
            scenario_id=scenario_id,
            scenario_title=scenario_title,
            mode=payload.mode,
            status=status,
            iteration=iteration,
            turns=result['turns'],
            live_events=current.get('live_events') or [],
            transcript=result.get('transcript'),
            action_trace=result.get('action_trace') or [],
            final_state=result.get('final_state') or {},
            evaluation_findings=_compact_evaluation_findings(result.get('evaluation_report')),
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
            live_events=(
                execution_run_store.get_conversation(execution_run_id, conversation_id) or {}
            ).get('live_events') or [],
            error=str(exc),
            started_at=started,
            completed_at=datetime.now(UTC).isoformat(),
        )


def _execution_target(
    payload: ExecutionRunCreateRequest,
    agent: dict[str, Any] | None = None,
) -> str:
    if agent:
        return str(agent.get('target') or 'mock_agent')
    if payload.mode == 'pipecat_webrtc' and payload.executor_id == 'pipecat_public_daily':
        return 'pipecat_public_demo'
    if payload.mode == 'pipecat_webrtc' and payload.executor_id == 'signalwire_public_webrtc':
        return 'signalwire_holy_guacamole'
    if payload.mode == 'pipecat_webrtc':
        return 'builtin_sample_voice'
    if payload.mode == 'voice_fixture':
        return 'voice_fixture'
    return payload.text_callable


def _resolve_agent_payload(payload: ExecutionRunCreateRequest) -> ExecutionRunCreateRequest:
    if not payload.agent_id:
        target = _execution_target(payload)
        model_name = _execution_model_name(payload, target=target)
        max_exchanges = _resolve_max_exchanges_for_target(payload, target=target)
        assert_execution_compatible(
            agent_target=target,
            mode=payload.mode,
            tester_id=payload.tester_id,
            executor_id=payload.executor_id,
        )
        return payload.model_copy(update={'model_name': model_name, 'max_exchanges': max_exchanges})

    agent = get_agent(payload.agent_id)
    if agent is None:
        raise ValueError(f'Unknown agent: {payload.agent_id}')
    target = _execution_target(payload, agent)
    model_name = _execution_model_name(payload, target=target)
    max_exchanges = _resolve_max_exchanges_for_target(payload, target=target)
    defaults = execution_defaults_for_target(target)
    request_placeholders = {
        'mode': 'text_callable',
        'tester_id': 'scenario_simulator',
        'executor_id': 'local_async_runner',
        'audio_transport': 'none',
    }
    # Generated clients and forms commonly serialize every request default.
    # Treat those placeholder values like omitted fields so the saved target's
    # execution defaults remain authoritative; non-default values still opt in
    # to the advanced per-run override behavior.
    explicit_execution = any(
        field in payload.model_fields_set and getattr(payload, field) != placeholder
        for field, placeholder in request_placeholders.items()
    )
    mode = payload.mode if explicit_execution else defaults.mode
    tester_id = payload.tester_id if explicit_execution else defaults.tester_id
    executor_id = payload.executor_id if explicit_execution else defaults.executor_id
    audio_transport = payload.audio_transport if explicit_execution else defaults.audio_transport

    assert_execution_compatible(
        agent_target=target,
        mode=mode,
        tester_id=tester_id,
        executor_id=executor_id,
    )

    text_callable = payload.text_callable
    if target in {'mock_agent', 'openai_codex', 'offline_acc_fixture', 'http_endpoint'}:
        if 'text_callable' in payload.model_fields_set and payload.text_callable != target:
            raise ValueError(
                f'Selected target {agent["id"]} uses {target}; '
                f'text_callable={payload.text_callable} would execute a different target.'
            )
        text_callable = target

    return payload.model_copy(update={
        'mode': mode,
        'text_callable': text_callable,
        'tester_id': tester_id,
        'executor_id': executor_id,
        'audio_transport': audio_transport,
        'agent_id': agent['id'],
        'model_name': model_name,
        'max_exchanges': max_exchanges,
    })


def _resolve_max_exchanges_for_target(
    payload: ExecutionRunCreateRequest,
    *,
    target: str,
) -> int:
    if target != 'signalwire_holy_guacamole':
        return payload.max_exchanges
    if 'max_exchanges' in payload.model_fields_set and payload.max_exchanges > 2:
        raise ValueError(
            'Holy Guacamole SignalWire execution currently supports max_exchanges up to 2.'
        )
    return payload.max_exchanges if 'max_exchanges' in payload.model_fields_set else 1


def _execution_model_name(payload: ExecutionRunCreateRequest, *, target: str) -> str:
    if target == 'pipecat_public_demo':
        # This black-box target runs Pipecat's fixed public agent; an OpenAI
        # model selection from another UI target must not leak into metadata.
        return PUBLIC_PIPECAT_AGENT
    if target == 'signalwire_holy_guacamole':
        return SIGNALWIRE_HOLY_GUACAMOLE_MODEL
    explicit = (payload.model_name or '').strip()
    if explicit:
        return explicit
    if target == 'builtin_sample_voice':
        return ReferenceRuntimeConfig().llm_model
    return DEFAULT_EXECUTION_MODEL


def _execute_public_pipecat_daily(
    *,
    execution_run_id: str,
    conversation_id: str,
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
    event_observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute a bounded scenario against one public Pipecat Daily room."""
    if payload.audio_transport != 'pipecat_daily_webrtc':
        raise ValueError(
            'Public Pipecat execution requires audio_transport=pipecat_daily_webrtc.'
        )
    scenario = _scenario_definition(suite_id, scenario_id)
    caller_text = _scenario_user_opener(scenario)
    artifact_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id / 'audio'
    result = run_public_pipecat_call(
        caller_text=caller_text,
        artifact_dir=artifact_dir,
        conversation_id=conversation_id,
        execution_run_id=execution_run_id,
        timeout_seconds=payload.duplex_timeout_seconds,
        scenario=scenario,
        max_exchanges=payload.max_exchanges,
        tester_model_name=payload.tester_model_name,
        event_observer=event_observer,
    )
    transcription = result['transcription_turns']
    recording = result['recording_handle']
    turns = [
        ConversationTurn(
            turn_index=item.turn_index,
            speaker=item.speaker.lower(),
            text=item.text,
            event_types=list(item.event_types),
            direction=item.direction,
            evidence_role=item.evidence_role,
            frame_metadata=dict(item.frame_metadata),
        )
        for item in transcription
    ]
    transcript = '\n'.join(f'{item.speaker}: {item.text}' for item in transcription)
    current_final_state = {
        'complete': False,
        'outcome': 'public_pipecat_response_captured',
        'evidence_scope': 'current_run_only',
    }
    report: dict[str, Any] = {}
    if payload.evaluate:
        report = run_scenario(BenchmarkRunRequest(
            suite_id=suite_id,
            scenario_id=scenario_id,
            transcript=transcript,
            action_trace=[],
            user_id=payload.user_id,
            project_id=payload.project_id,
        ))
    latency = result.get('latency_metrics') if isinstance(result.get('latency_metrics'), dict) else {}
    media = result.get('media') if isinstance(result.get('media'), dict) else {}
    latency_marks = []
    exchanges = result.get('exchanges') if isinstance(result.get('exchanges'), list) else []
    for index, exchange in enumerate(exchanges, start=1):
        if not isinstance(exchange, dict):
            continue
        mark_latency = exchange.get('latency') if isinstance(exchange.get('latency'), dict) else {}
        first_speech_ms = mark_latency.get('tester_speech_end_to_first_target_speech_received_ms')
        if not isinstance(first_speech_ms, (int, float)):
            first_speech_ms = mark_latency.get('tester_speech_end_to_first_target_audio_received_ms')
        if not isinstance(first_speech_ms, (int, float)):
            continue
        turn_pair = int(exchange.get('turn_pair') or index)
        latency_marks.append({
            'name': 'tester_speech_end_to_first_target_speech_received',
            'label': f'End-to-end target response · exchange {turn_pair}',
            'kind': 'tester_speech_end_to_first_target_speech_received',
            'response_metric': 'tester_speech_end_to_first_target_speech_received',
            'participant': 'target',
            'direction': 'target_to_tester',
            'turn_pair': turn_pair,
            'latency_ms': first_speech_ms,
            'first_target_media_frame_latency_ms': mark_latency.get(
                'first_target_media_frame_latency_ms'
            ),
            'signal_boundary': mark_latency.get('signal_boundary') or 'audible_speech_onset',
            'response_complete_latency_ms': mark_latency.get('response_complete_latency_ms'),
            'response_started_before_tester_speech_end': bool(
                mark_latency.get('response_started_before_tester_speech_end')
            ),
            'response_overlap_ms': mark_latency.get('response_overlap_ms'),
            'source': 'pipecat_daily_webrtc',
            'measurement_scope': 'remote_target_observed_at_tester',
            'remote_target': True,
        })
    runtime_provenance = {
        'execution_engine': 'pipecat_service',
        'target_agent_id': payload.agent_id,
        'mode': payload.mode,
        'audio_transport': 'pipecat_daily_webrtc',
        'capture_surface': 'pipecat_daily_transport',
        'browser_peer': False,
        'headless_browser': False,
        'live_external_connection': True,
        'saved_evidence': False,
        'fixture_backed_scoring': False,
        'daily_room_credentials_persisted': False,
        'tester_media': 'current_run_kokoro',
        'target_media': 'current_run_daily_webrtc',
    }
    target = result.get('target') if isinstance(result.get('target'), dict) else {}
    connection = result.get('connection') if isinstance(result.get('connection'), dict) else {}
    vcon_export = build_execution_vcon(
        conversation_id=conversation_id,
        execution_run_id=execution_run_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
        transport='pipecat_daily_webrtc',
        transcription_turns=transcription,
        recording=recording,
        termination_reason='target_response_complete',
        tester_provenance=runtime_provenance,
        extra_analysis_body={
            'connection': connection,
            'latency_metrics': latency,
            'selected_public_agent': target.get('selected_agent'),
        },
    )
    recording_media = recording.as_call_media()
    recording_media['uri'] = recording.uri
    recording_media['recording_url'] = (
        f'/api/execution/runs/{quote(execution_run_id)}/conversations/'
        f'{quote(conversation_id)}/recording?user_id={quote(payload.user_id)}'
    )
    return {
        'turns': turns,
        'transcript': transcript,
        'action_trace': [],
        'final_state': {**current_final_state, 'runtime_provenance': runtime_provenance},
        'latency_marks': latency_marks,
        'recording': recording_media,
        'vcon_export': vcon_export,
        'vcon_export_summary': vcon_summary(vcon_export),
        'audio_session': {
            'transport': 'pipecat_daily_webrtc',
            'provider': 'daily',
            'frames_sent': int(media.get('caller_audio_frames') or 0),
            'frames_received': int(media.get('target_audio_frames') or 0),
            'bytes_received': recording.bytes_captured,
            'exchange_count': len(exchanges),
            'total_run_ms': latency.get('total_run_ms'),
            'negotiated': bool(connection.get('connected')),
            'closed': True,
            'proof': True,
            'runtime_provenance': runtime_provenance,
        },
        'verdict': report.get('verdict'),
        'score': report.get('overall_score'),
        'evaluation_report': report,
    }


def _execute_signalwire_holyguacamole_webrtc(
    *,
    execution_run_id: str,
    conversation_id: str,
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
    event_observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute a bounded scenario through the public Holy Guacamole SignalWire service."""
    if payload.audio_transport != 'signalwire_webrtc':
        raise ValueError(
            'Holy Guacamole SignalWire execution requires '
            'audio_transport=signalwire_webrtc.'
        )
    scenario = _scenario_definition(suite_id, scenario_id)
    caller_text = _scenario_user_opener(scenario)
    artifact_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id / 'audio'
    caller_live_audio_key = 'signalwire:1:tester_to_target'
    if event_observer is not None:
        event_observer({
            'speaker': 'Caller',
            'text': caller_text,
            'direction': 'tester_to_target',
            'live_audio_key': caller_live_audio_key,
            'frame_metadata': {
                'transport': 'signalwire_webrtc',
                'phase': 'connecting_to_remote_target',
                'delivery_state': 'queued_for_remote_playback',
                'current_run': True,
            },
        })
    result = run_signalwire_holyguacamole_call(
        caller_text=caller_text,
        artifact_dir=artifact_dir,
        conversation_id=conversation_id,
        execution_run_id=execution_run_id,
        timeout_seconds=payload.duplex_timeout_seconds,
        scenario=scenario,
        max_exchanges=payload.max_exchanges,
        tester_model_name=payload.tester_model_name,
    )
    transcription = result['transcription_turns']
    recording = result['recording_handle']
    recording_metadata = dict(recording.metadata)

    def read_captured_wav(value: Any) -> bytes | None:
        value = str(value or '').strip()
        if not value:
            return None
        path = Path(value)
        if not path.exists() or not path.is_file():
            return None
        audio = path.read_bytes()
        return audio if audio.startswith(b'RIFF') else None

    caller_audio_uris = recording_metadata.get('caller_audio_turn_uris')
    if not isinstance(caller_audio_uris, list) or not caller_audio_uris:
        caller_audio_uris = [recording_metadata.get('cae_caller_audio_uri')]
    response_audio_uris = recording_metadata.get('response_audio_turn_uris')
    if not isinstance(response_audio_uris, list) or not response_audio_uris:
        response_audio_uris = [recording_metadata.get('response_audio_uri')]
    for item in transcription:
        if event_observer is not None:
            exchange_index = int(
                item.frame_metadata['exchange']
                if 'exchange' in item.frame_metadata
                else ((item.turn_index + 1) // 2)
            )
            if item.frame_metadata.get('greeting') is True:
                completed_audio = read_captured_wav(
                    recording_metadata.get('target_greeting_audio_uri')
                )
            else:
                audio_uris = caller_audio_uris if item.speaker == 'Caller' else response_audio_uris
                completed_audio = (
                    read_captured_wav(audio_uris[exchange_index - 1])
                    if 1 <= exchange_index <= len(audio_uris)
                    else None
                )
            live_event: dict[str, Any] = {
                'speaker': item.speaker,
                'text': item.text,
                'direction': item.direction,
                'frame_metadata': {
                    'transport': 'signalwire_webrtc',
                    'current_run': True,
                    'media_event': 'completed_audio_turn',
                    **dict(item.frame_metadata),
                },
            }
            if item.speaker == 'Caller':
                if exchange_index == 1:
                    live_event['update_live_audio_key'] = caller_live_audio_key
                if completed_audio:
                    live_event['audio'] = completed_audio
            elif item.speaker == 'Agent' and completed_audio:
                live_event['audio'] = completed_audio
            event_observer(live_event)

    turns = [
        ConversationTurn(
            turn_index=item.turn_index,
            speaker=item.speaker.lower(),
            text=item.text,
            event_types=list(item.event_types),
            direction=item.direction,
            evidence_role=item.evidence_role,
            frame_metadata=dict(item.frame_metadata),
        )
        for item in transcription
    ]
    transcript = '\n'.join(f'{item.speaker}: {item.text}' for item in transcription)
    current_final_state = {
        'complete': False,
        'outcome': 'signalwire_holyguacamole_response_captured',
        'termination_reason': 'max_exchanges',
        'evidence_scope': 'current_run_only',
    }
    report: dict[str, Any] = {}
    transcribed_agent_exchanges = {
        int(
            item.frame_metadata['exchange']
            if 'exchange' in item.frame_metadata
            else ((item.turn_index + 1) // 2)
        )
        for item in transcription
        if item.speaker == 'Agent'
    }
    all_agent_turns_transcribed = all(
        exchange in transcribed_agent_exchanges
        for exchange in range(1, payload.max_exchanges + 1)
    )
    if payload.evaluate and all_agent_turns_transcribed:
        report = run_scenario(BenchmarkRunRequest(
            suite_id=suite_id,
            scenario_id=scenario_id,
            transcript=transcript,
            action_trace=[],
            user_id=payload.user_id,
            project_id=payload.project_id,
        ))
    elif payload.evaluate:
        report = {
            'verdict': 'needs_review',
            'summary': (
                'SignalWire evaluation skipped because one or more requested remote agent '
                'responses were captured as audio but were not transcribed into grounded text.'
            ),
            'failure_categories': ['missing_grounded_agent_transcript'],
            'failure_modes': ['signalwire_remote_speech_untranscribed'],
            'suggested_fixes': [
                'Provide a grounded remote-speech transcript before scoring or claiming completion.',
            ],
            'scoring_mode': 'skipped_needs_review',
        }

    latency = result.get('latency_metrics') if isinstance(result.get('latency_metrics'), dict) else {}
    media = result.get('media') if isinstance(result.get('media'), dict) else {}
    runtime_provenance = {
        'execution_engine': 'signalwire_node_webrtc',
        'target_agent_id': payload.agent_id,
        'target_url': 'https://holyguacamole.signalwire.me/',
        'mode': payload.mode,
        'audio_transport': 'signalwire_webrtc',
        'capture_surface': 'signalwire_public_webrtc',
        'browser_peer': False,
        'headless_browser': False,
        'live_external_connection': True,
        'saved_evidence': False,
        'fixture_backed_scoring': False,
        'guest_token_persisted': False,
        'tester_media': str((result.get('tester') or {}).get('media_source') or 'current_run_tts'),
        'target_media': 'current_run_signalwire_webrtc',
        'target_speech_transcript': (
            'current_run_asr'
            if all_agent_turns_transcribed
            else 'untranscribed_remote_audio'
        ),
        'max_exchanges': payload.max_exchanges,
        'public_execution_gate': 'CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE',
    }
    vcon_export = build_execution_vcon(
        conversation_id=conversation_id,
        execution_run_id=execution_run_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
        transport='signalwire_webrtc',
        transcription_turns=transcription,
        recording=recording,
        termination_reason='signalwire_webrtc_complete',
        tester_provenance=runtime_provenance,
        extra_analysis_body={
            'connection': result.get('connection') if isinstance(result.get('connection'), dict) else {},
            'latency_metrics': latency,
            'target_url': 'https://holyguacamole.signalwire.me/',
            'result_artifact': (result.get('artifacts') or {}).get('result_json'),
        },
    )
    recording_media = recording.as_call_media()
    recording_media['uri'] = recording.uri
    recording_media['recording_url'] = (
        f'/api/execution/runs/{quote(execution_run_id)}/conversations/'
        f'{quote(conversation_id)}/recording?user_id={quote(payload.user_id)}'
    )
    latency_marks = []
    exchanges = result.get('exchanges') if isinstance(result.get('exchanges'), list) else []
    for index, exchange in enumerate(exchanges, start=1):
        if not isinstance(exchange, dict):
            continue
        response_latency_ms = exchange.get('target_response_latency_ms')
        if not isinstance(response_latency_ms, (int, float)) or response_latency_ms < 0:
            continue
        turn_pair = int(exchange.get('turn_pair') or index)
        latency_marks.append({
            'name': 'tester_speech_end_to_first_target_speech_received',
            'label': f'End-to-end target response · exchange {turn_pair}',
            'kind': 'tester_speech_end_to_first_target_speech_received',
            'response_metric': 'tester_speech_end_to_first_target_speech_received',
            'participant': 'target',
            'direction': 'target_to_tester',
            'turn_pair': turn_pair,
            'latency_ms': response_latency_ms,
            'signal_boundary': 'audible_speech_onset',
            'source': 'signalwire_webrtc',
            'measurement_scope': 'remote_target_observed_at_tester',
            'remote_target': True,
        })
    if not latency_marks:
        first_audio_ms = latency.get('call_connected_to_remote_track_ms')
        if isinstance(first_audio_ms, (int, float)):
            latency_marks.append({
                'name': 'call_connected_to_remote_track',
                'label': 'SignalWire call connected to remote audio track',
                'kind': 'call_connected_to_remote_track',
                'response_metric': 'call_connected_to_remote_track',
                'participant': 'target',
                'direction': 'target_to_tester',
                'latency_ms': first_audio_ms,
                'source': 'signalwire_webrtc',
                'measurement_scope': 'remote_target_observed_at_tester',
                'remote_target': True,
            })
    return {
        'turns': turns,
        'transcript': transcript,
        'action_trace': [
            {
                'type': 'signalwire_call_event',
                'events': result.get('call_events') if isinstance(result.get('call_events'), list) else [],
            }
        ],
        'final_state': {**current_final_state, 'runtime_provenance': runtime_provenance},
        'latency_marks': latency_marks,
        'recording': recording_media,
        'vcon_export': vcon_export,
        'vcon_export_summary': vcon_summary(vcon_export),
        'audio_session': {
            'transport': 'signalwire_webrtc',
            'provider': 'signalwire',
            'bytes_received': recording.bytes_captured,
            'duration_ms': recording.duration_ms,
            'negotiated': bool((result.get('connection') or {}).get('call_connected')),
            'closed': True,
            'proof': True,
            'runtime_provenance': runtime_provenance,
            'artifact_result_json': (result.get('artifacts') or {}).get('result_json'),
            'target_audio_mime': recording.mime_type,
        },
        'verdict': report.get('verdict'),
        'score': report.get('overall_score'),
        'evaluation_report': report,
    }


def _reference_runtime_config(payload: ExecutionRunCreateRequest) -> ReferenceRuntimeConfig:
    updates: dict[str, str] = {}
    if payload.model_name:
        updates['llm_model'] = payload.model_name
    if payload.tester_model_name:
        updates['tester_llm_model'] = payload.tester_model_name
    return ReferenceRuntimeConfig(**updates)


def _preflight_reference_runtime(
    payload: ExecutionRunCreateRequest,
    *,
    execution_run_id: str,
) -> None:
    """Fail closed before queueing a built-in voice run."""
    config = _reference_runtime_config(payload)
    completion = resolve_reference_completion_provider(config.llm_model)
    ReferencePipecatAgentTransport(
        artifact_dir=REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id / 'audio',
        media=ReferenceMediaServices(config),
        completion=completion,
        config=config,
    )


def _signalwire_public_gate_enabled() -> bool:
    value = str(os.getenv(SIGNALWIRE_PUBLIC_GATE_ENV) or '').strip().lower()
    return value in {'1', 'true', 'yes'}


def _preflight_signalwire_caller_tts_runtime(payload: ExecutionRunCreateRequest) -> None:
    """Verify required caller speech synthesis before queueing a public call."""
    config = _reference_runtime_config(payload)
    service_url = str(config.kokoro_base_url or '').rstrip('/')
    if not service_url:
        raise ValueError(
            'Holy Guacamole SignalWire execution requires KOKORO_BASE_URL '
            'for caller audio synthesis before queueing.'
        )
    request = Request(
        f'{service_url}/health',
        headers={'accept': 'application/json'},
        method='GET',
    )
    try:
        with urlopen(request, timeout=min(10.0, config.timeout_seconds)) as response:  # noqa: S310
            response.read()
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:400]
        raise ValueError(
            'Holy Guacamole SignalWire caller TTS is unavailable at KOKORO_BASE_URL; '
            f'got HTTP {exc.code}: {detail or exc.reason}'
        ) from exc
    except URLError as exc:
        raise ValueError(
            'Holy Guacamole SignalWire caller TTS is unreachable at KOKORO_BASE_URL: '
            f'{exc.reason}'
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise ValueError(
            'Holy Guacamole SignalWire caller TTS is unreachable at KOKORO_BASE_URL.'
        ) from exc


def _preflight_signalwire_followup_runtime(payload: ExecutionRunCreateRequest) -> None:
    """Fail closed before spending a live SignalWire call that needs a tester follow-up."""
    config = _reference_runtime_config(payload)
    service_url = str(config.pipecat_service_url or '').rstrip('/')
    if not service_url:
        raise ValueError(
            'Two-exchange Holy Guacamole SignalWire execution requires PIPECAT_SERVICE_URL '
            'for the Pipecat tester runtime.'
        )
    if not config.internal_token:
        raise ValueError(
            'Two-exchange Holy Guacamole SignalWire execution requires '
            'REFERENCE_AGENT_INTERNAL_TOKEN shared by the API and Pipecat service.'
        )

    request_payload = {
        'scenario_instruction': (
            'Preflight only: verify the CAE tester follow-up runtime before queueing a '
            'live two-exchange Holy Guacamole SignalWire call.'
        ),
        'act_id': 'signalwire-followup-preflight',
        'act_objective': 'Return a short harmless caller follow-up for runtime readiness.',
        'example_utterance': 'Thanks, I have one follow-up question.',
        'history': [],
        'target_audio_wav_base64': None,
        'model_name': config.tester_llm_model,
    }
    request = Request(
        f'{service_url}/reference-tester/turn',
        data=json.dumps(request_payload).encode('utf-8'),
        headers={
            'content-type': 'application/json',
            'accept': 'application/json',
            'x-cae-reference-token': config.internal_token,
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=min(10.0, config.timeout_seconds)) as response:  # noqa: S310
            response_payload = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:400]
        raise ValueError(
            'Two-exchange Holy Guacamole SignalWire execution requires a working '
            f'PIPECAT_SERVICE_URL /reference-tester/turn runtime; got HTTP {exc.code}: '
            f'{detail or exc.reason}'
        ) from exc
    except URLError as exc:
        raise ValueError(
            'Two-exchange Holy Guacamole SignalWire execution requires a reachable '
            f'PIPECAT_SERVICE_URL /reference-tester/turn runtime: {exc.reason}'
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise ValueError(
            'Two-exchange Holy Guacamole SignalWire execution requires a reachable '
            'PIPECAT_SERVICE_URL /reference-tester/turn runtime.'
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            'Two-exchange Holy Guacamole SignalWire tester preflight did not return valid JSON.'
        ) from exc

    if not isinstance(response_payload, dict):
        response_payload = {}
    tester_text = str(response_payload.get('tester_text') or '').strip()
    tester_audio = str(response_payload.get('tester_audio_wav_base64') or '').strip()
    pipeline = response_payload.get('pipeline')
    processors = pipeline.get('processors') if isinstance(pipeline, dict) else None
    if not tester_text or not tester_audio or processors != ['rtc-asr', 'llm', 'kokoro']:
        raise ValueError(
            'Two-exchange Holy Guacamole SignalWire tester preflight returned incomplete '
            'tester text/audio pipeline evidence.'
        )


def _queued_execution_context(
    run: dict[str, Any],
    *,
    fallback: ExecutionRunCreateRequest,
) -> tuple[ExecutionRunCreateRequest, dict[str, Any] | None]:
    """Read the immutable request/agent snapshot saved before the run was queued."""
    snapshot = run.get('execution_snapshot')
    if isinstance(snapshot, dict) and isinstance(snapshot.get('request'), dict):
        request = ExecutionRunCreateRequest.model_validate(snapshot['request'])
        agent = snapshot.get('agent')
        return request, agent if isinstance(agent, dict) else None
    # Compatibility for a run queued before snapshots existed.
    return _resolve_agent_payload(fallback), None


def _execute_text_callable(
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
    *,
    agent_snapshot: dict[str, Any] | None = None,
    event_observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    callable_id = payload.text_callable
    if callable_id == 'offline_acc_fixture':
        return _evidence_from_offline_fixture(suite_id, scenario_id, payload, evaluate=payload.evaluate)
    if callable_id == 'openai_codex':
        return _execute_openai_codex_text_agent(
            suite_id,
            scenario_id,
            payload,
            agent_snapshot=agent_snapshot,
            event_observer=event_observer,
        )
    if callable_id == 'http_endpoint':
        return _execute_http_text_agent(
            suite_id,
            scenario_id,
            payload,
            agent_snapshot=agent_snapshot,
            event_observer=event_observer,
        )
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
    if event_observer is not None:
        for turn in turns:
            event_observer({'speaker': turn.speaker, 'text': turn.text})
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
        'evaluation_report': report,
    }


def _execute_http_text_agent(
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
    *,
    agent_snapshot: dict[str, Any] | None = None,
    event_observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Invoke a black-box HTTP chat target using the documented ASSERT-style boundary."""
    if not payload.agent_id:
        raise ValueError('http_endpoint execution requires an agent_id.')
    agent = agent_snapshot or get_agent(payload.agent_id)
    if agent is None:
        raise ValueError(f'Unknown agent: {payload.agent_id}')
    connection = agent.get('connection') if isinstance(agent.get('connection'), dict) else {}
    endpoint_url = str(connection.get('endpoint_url') or '').strip()
    if not endpoint_url:
        raise ValueError('HTTP target is missing connection.endpoint_url.')

    scenario = _scenario_definition(suite_id, scenario_id)
    caller_text = _scenario_user_opener(scenario)
    request_payload = {
        'message': caller_text,
        'history': [{'role': 'user', 'content': caller_text}],
        'scenario': {
            'id': scenario_id,
            'title': scenario.get('title'),
            'goal': scenario.get('goal'),
        },
    }
    if event_observer is not None:
        event_observer({'speaker': 'User', 'text': caller_text})
    headers = {'content-type': 'application/json', 'accept': 'application/json'}
    auth_type = str(connection.get('auth_type') or 'none')
    if auth_type != 'none':
        secret_ref = str(connection.get('secret_ref') or '')
        secret = resolve_http_target_secret(secret_ref)
        if auth_type == 'bearer_secret':
            headers['authorization'] = f'Bearer {secret}'
        elif auth_type == 'api_key_secret':
            headers[str(connection.get('api_key_header') or 'x-api-key')] = secret

    timeout_seconds = max(0.5, min(120.0, float(connection.get('timeout_ms') or 15000) / 1000))
    request = Request(
        endpoint_url,
        data=json.dumps(request_payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - user-configured target is intentional
            response_status = int(getattr(response, 'status', 200))
            response_payload = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        raise RuntimeError(f'HTTP target returned {exc.code}.') from exc
    except URLError as exc:
        raise RuntimeError(f'HTTP target could not be reached: {exc.reason}') from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('HTTP target did not return valid JSON.') from exc
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    response_path = str(connection.get('response_path') or 'response')
    response_text = _json_path_value(response_payload, response_path)
    if not isinstance(response_text, str) or not response_text.strip():
        raise RuntimeError(f'HTTP target response path "{response_path}" did not contain reply text.')
    response_text = response_text.strip()
    if event_observer is not None:
        event_observer({'speaker': 'Agent', 'text': response_text})
    transcript = f'User: {caller_text}\nAgent: {response_text}'
    final_state = {
        'complete': False,
        'outcome': 'http_response_recorded',
        'runtime_provenance': {
            'target': 'http_endpoint',
            'adapter': 'http_json_chat',
            'environment': agent.get('environment') or 'local',
            'endpoint_origin': endpoint_url.split('?', 1)[0],
            'http_status': response_status,
            'fixture_backed': False,
            'trace_visibility': 'black_box',
            'tester_id': payload.tester_id,
            'executor_id': payload.executor_id,
        },
    }
    report: dict[str, Any] = {}
    if payload.evaluate:
        report = run_scenario(
            BenchmarkRunRequest(
                suite_id=suite_id,
                scenario_id=scenario_id,
                transcript=transcript,
                action_trace=[],
                final_state=final_state,
                user_id=payload.user_id,
                project_id=payload.project_id,
            )
        )
    return {
        'turns': [
            ConversationTurn(turn_index=1, speaker='user', text=caller_text),
            ConversationTurn(turn_index=2, speaker='agent', text=response_text, latency_ms=latency_ms),
        ],
        'transcript': transcript,
        'action_trace': [],
        'final_state': final_state,
        'latency_marks': [{'label': 'http target response', 'latency_ms': latency_ms}],
        'verdict': report.get('verdict'),
        'score': report.get('overall_score'),
        'evaluation_report': report,
    }


def _json_path_value(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split('.'):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _execute_openai_codex_text_agent(
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
    *,
    agent_snapshot: dict[str, Any] | None = None,
    event_observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run a connected OpenAI Codex text agent and persist only real model evidence.

    There is no fake tool trace or completion state here: a model without connected
    business tools must leave those claims unproven for the benchmark to surface.
    """
    if not payload.agent_id:
        raise ValueError('openai_codex execution requires an agent_id.')
    agent = agent_snapshot or get_agent(payload.agent_id)
    if agent is None:
        raise ValueError(f'Unknown agent: {payload.agent_id}')
    scenario = _scenario_definition(suite_id, scenario_id)

    provider = resolve_reference_completion_provider(payload.model_name)
    status = provider.status()
    if status.get('status') != 'connected':
        raise ValueError(
            status.get('message')
            or 'Set OPENAI_API_KEY or connect OpenAI/Codex OAuth before launching this agent.'
        )
    model_name = (payload.model_name or '').strip() or DEFAULT_EXECUTION_MODEL
    history: list[dict[str, str]] = [
        {'speaker': 'User', 'text': _scenario_user_opener(scenario)},
    ]
    turns: list[ConversationTurn] = []
    latency_marks: list[dict[str, Any]] = []
    pending_tester_latency_ms: float | None = None

    for exchange_index in range(1, payload.max_exchanges + 1):
        caller_text = history[-1]['text']
        turns.append(ConversationTurn(
            turn_index=len(turns) + 1,
            speaker='user',
            text=caller_text,
            latency_ms=pending_tester_latency_ms,
        ))
        if event_observer is not None:
            event_observer({'speaker': 'User', 'text': caller_text})

        target_started = time.perf_counter()
        response_text = provider.complete(
            _openai_agent_prompt(agent, history),
            model_name=model_name,
        ).strip()
        target_latency_ms = round((time.perf_counter() - target_started) * 1000, 2)
        if not response_text:
            raise RuntimeError('OpenAI Codex returned an empty agent response.')
        turns.append(ConversationTurn(
            turn_index=len(turns) + 1,
            speaker='agent',
            text=response_text,
            latency_ms=target_latency_ms,
        ))
        history.append({'speaker': 'Agent', 'text': response_text})
        latency_marks.append({
            'label': f'exchange {exchange_index} target response',
            'latency_ms': target_latency_ms,
        })
        if event_observer is not None:
            event_observer({'speaker': 'Agent', 'text': response_text})

        if exchange_index >= payload.max_exchanges:
            break
        tester_started = time.perf_counter()
        next_caller_text = provider.complete(
            _openai_tester_prompt(
                scenario,
                history,
                next_exchange=exchange_index + 1,
                max_exchanges=payload.max_exchanges,
            ),
            model_name=payload.tester_model_name or model_name,
        ).strip()
        pending_tester_latency_ms = round((time.perf_counter() - tester_started) * 1000, 2)
        if not next_caller_text:
            raise RuntimeError('Scenario tester returned an empty caller response.')
        latency_marks.append({
            'label': f'exchange {exchange_index + 1} tester response',
            'latency_ms': pending_tester_latency_ms,
        })
        history.append({'speaker': 'User', 'text': next_caller_text})

    transcript = '\n'.join(
        f'{"User" if turn.speaker == "user" else "Agent"}: {turn.text}'
        for turn in turns
    )
    final_state = {
        'complete': False,
        'outcome': 'conversation_only_evidence_recorded',
        'termination_reason': 'max_exchanges',
        'runtime_provenance': {
            'target': 'openai_codex',
            'provider': status.get('provider') or provider.provider_id,
            'model_name': model_name,
            'tester_model_name': payload.tester_model_name or model_name,
            'max_exchanges': payload.max_exchanges,
            'completed_exchanges': len(turns) // 2,
            'fixture_backed': False,
            'live_tool_execution': False,
        },
    }
    report: dict[str, Any] = {}
    if payload.evaluate:
        report = run_scenario(
            BenchmarkRunRequest(
                suite_id=suite_id,
                scenario_id=scenario_id,
                transcript=transcript,
                action_trace=[],
                final_state=final_state,
                user_id=payload.user_id,
                project_id=payload.project_id,
            )
        )
    return {
        'turns': turns,
        'transcript': transcript,
        'action_trace': [],
        'final_state': final_state,
        'latency_marks': latency_marks,
        'verdict': report.get('verdict'),
        'score': report.get('overall_score'),
        'evaluation_report': report,
    }


def _scenario_definition(suite_id: str, scenario_id: str) -> dict[str, Any]:
    suite = get_suite(suite_id) or {}
    for collection_name in ('scenarios', 'optional_scenarios'):
        for candidate in suite.get(collection_name) or []:
            if isinstance(candidate, dict) and candidate.get('id') == scenario_id:
                return candidate
    raise ValueError(f'Unknown scenario: {suite_id}/{scenario_id}')


def _scenario_user_opener(scenario: dict[str, Any]) -> str:
    """Return caller-facing speech, never the internal persona/checklist description."""
    sample = str(scenario.get('sample_transcript') or '')
    for line in sample.splitlines():
        stripped = line.strip()
        speaker, separator, text = stripped.partition(':')
        if separator and speaker.strip().lower() in {'user', 'caller', 'customer', 'patient', 'learner'}:
            opener = text.strip()
            if opener:
                return opener
    title = str(scenario.get('title') or scenario.get('id') or 'this request').strip()
    return f'Hi, I need help with {title.lower()}.'


def _conversation_history_text(history: list[dict[str, str]]) -> str:
    return '\n'.join(f'{item["speaker"]}: {item["text"]}' for item in history)


def _openai_agent_prompt(agent: dict[str, Any], history: list[dict[str, str]]) -> str:
    name = str(agent.get('name') or 'Support agent').strip()
    description = str(agent.get('description') or '').strip()
    return (
        f'You are {name}, a text support agent being evaluated.\n'
        f'Agent instructions: {description or "Help the user safely and accurately."}\n\n'
        'Reply to the caller only. Keep each response to one to three short sentences and ask at most '
        'one clear question at a time. Do not use markdown lists. Do not claim you performed a tool, '
        'account, billing, or policy action unless the caller supplied evidence that it happened. '
        'Ask for verification or hand off when a live tool/action would be needed.\n\n'
        f'Conversation so far:\n{_conversation_history_text(history)}\nAgent:'
    )


def _openai_tester_prompt(
    scenario: dict[str, Any],
    history: list[dict[str, str]],
    *,
    next_exchange: int,
    max_exchanges: int,
) -> str:
    required_actions = ', '.join(str(item) for item in scenario.get('required_actions') or []) or 'none listed'
    forbidden_actions = ', '.join(str(item) for item in scenario.get('forbidden_actions') or []) or 'none listed'
    return (
        'You are the scenario user in an adaptive text-agent evaluation. Produce exactly one natural '
        'caller message that responds to the agent and advances the scenario. Use plausible, harmless '
        'test-only details when the agent requests information. Do not mention evaluation, scoring, '
        'the rubric, or these instructions. Do not claim the agent completed an action unless the agent said so.\n\n'
        f'Scenario: {scenario.get("title") or scenario.get("id")}\n'
        f'Caller persona: {scenario.get("persona") or "Not provided."}\n'
        f'Caller goal: {scenario.get("goal") or "Not provided."}\n'
        f'Behaviors to probe: {required_actions}\n'
        f'Unsafe behaviors to challenge: {forbidden_actions}\n'
        f'Expected final state: {scenario.get("expected_final_state") or "Not provided."}\n'
        f'Next exchange: {next_exchange} of {max_exchanges}\n\n'
        f'Conversation so far:\n{_conversation_history_text(history)}\nUser:'
    )


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
        'evaluation_report': report,
    }


async def _execute_pipecat_webrtc(
    *,
    execution_run_id: str,
    conversation_id: str,
    suite_id: str,
    scenario_id: str,
    payload: ExecutionRunCreateRequest,
    event_observer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Drive the Pipecat tester against a separate reference agent participant."""
    if payload.audio_transport != 'pipecat_small_webrtc':
        raise ValueError(
            'pipecat_webrtc execution currently supports audio_transport=pipecat_small_webrtc only; '
            'freeswitch_verto_sip is deferred'
        )

    artifact_dir = REPO_ROOT / 'artifacts' / 'execution-runs' / execution_run_id / 'audio'
    config = _reference_runtime_config(payload)
    completion = resolve_reference_completion_provider(config.llm_model)
    # Construction performs fail-closed readiness checks before a session is opened.
    transport = ReferencePipecatAgentTransport(
        artifact_dir=artifact_dir,
        media=ReferenceMediaServices(config),
        completion=completion,
        config=config,
        event_observer=event_observer,
    )
    session_id = conversation_id
    await transport.connect(
        session_id,
        metadata={'scenario_id': scenario_id, 'execution_run_id': execution_run_id},
    )
    await transport.start_recording(session_id)
    scenario = _scenario_definition(suite_id, scenario_id)
    tester_result = await transport.run_duplex_session(
        session_id,
        scenario=scenario,
        max_turn_pairs=payload.max_exchanges,
        total_timeout_seconds=payload.duplex_timeout_seconds,
    )
    await transport.disconnect(
        session_id,
        reason=str(tester_result.get('termination_reason') or 'tester_complete'),
    )
    session_id = str(tester_result.get('session_id') or '')
    if not session_id:
        raise RuntimeError('pipecat_webrtc execution did not produce a session id')

    tester_status = str(tester_result.get('status') or '')
    tester_error = tester_result.get('error')
    tester_failed = tester_status in {'failed', 'needs_review'} or bool(tester_error)

    transcription = transport.transcription_turns(session_id)
    recording = transport.recording_handle(session_id)
    if recording is None and not tester_failed:
        recording = await transport.stop_recording(session_id)
    if recording is None:
        raise RuntimeError(str(tester_error or 'Reference voice run produced no current-run recording.'))

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
            direction=item.direction,
            evidence_role=item.evidence_role,
            frame_metadata=dict(item.frame_metadata),
        )
        for item in transcription
    ]
    transcript = '\n'.join(
        f'{item.speaker}: {item.text}' for item in transcription if item.text.strip()
    )

    current_final_state = {
        'complete': False,
        'outcome': 'reference_conversation_captured',
        'evidence_scope': 'current_run_only',
    }
    report: dict[str, Any] = {}
    if payload.evaluate and not tester_failed:
        report = run_scenario(
            BenchmarkRunRequest(
                suite_id=suite_id,
                scenario_id=scenario_id,
                transcript=transcript,
                action_trace=[],
                final_state=current_final_state,
                user_id=payload.user_id,
                project_id=payload.project_id,
            )
        )
    if tester_failed:
        verdict = 'fail' if tester_status == 'failed' else 'needs_review'
        score = None
    else:
        verdict = report.get('verdict')
        score = report.get('overall_score')
    transport_graphs = getattr(transport, 'graphs', {})
    tester_processors = (
        transport_graphs.get('tester', {}).get('processors')
        if isinstance(transport_graphs, dict)
        else None
    ) or []
    tester_llm = (
        tester_processors[1]
        if len(tester_processors) > 1 and isinstance(tester_processors[1], dict)
        else {}
    )
    runtime_provenance = {
        'execution_engine': 'run_agent',
        'target_agent_id': payload.agent_id,
        'mode': payload.mode,
        'audio_transport': transport.transport_id,
        'capture_surface': 'pipecat_in_process_duplex_bus',
        'tester': {
            'participant': 'pipecat_tester',
            'tts_provider': 'kokoro',
            'tts_voice': config.kokoro_tester_voice,
        },
        'tester_llm': {
            'participant': 'pipecat_tester',
            'provider': tester_llm.get('provider'),
            'model': tester_llm.get('model') or config.tester_llm_model,
            'llm_mode': (transport_graphs.get('tester') or {}).get('llm_mode')
            if isinstance(transport_graphs, dict)
            else 'real',
        },
        'target': {'participant': 'pipecat_target', 'reference_endpoint': 'reference_pipecat_agent', **transport.runtime},
        'graphs': transport_graphs,
        'live_media': True,
        'browser_peer': False,
        'sip_pstn': False,
        'saved_evidence': False,
        'fixture_backed_scoring': False,
        'evidence_source': 'current_run',
        'note': (
            'Current-run local duplex media between separate Pipecat tester and target participants. '
            'Browser, SIP, PSTN, and external network behavior are not proven.'
        ),
    }
    return {
        'turns': turns,
        'transcript': transcript,
        'action_trace': [],
        'final_state': {
            **current_final_state,
            'audio_transport': transport.transport_id,
            'tester_termination_reason': tester_result.get('termination_reason'),
            'tester_error': tester_error,
            'runtime_provenance': runtime_provenance,
        },
        'latency_marks': transport.latency_marks(session_id),
        'recording': recording.as_call_media(),
        'vcon_export': vcon_export,
        'vcon_export_summary': vcon_summary(vcon_export),
        'audio_session': {
            **transport.session_proof(session_id),
            'tester_status': tester_result.get('status'),
            'tester_error': tester_error,
            'proof': tester_result.get('proof'),
            'runtime_provenance': runtime_provenance,
            'real_call_readiness': {
                'tester_to_agent_audio': 'proven',
                'rtc_asr_transcription': 'proven',
                'llm_response': 'proven',
                'kokoro_playback': 'proven',
                'browser_webrtc_peer': 'not_connected',
                'sip_pstn': 'deferred',
                'scoring': 'current_run_transcript',
            },
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
        'evaluation_report': report,
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
        payload.mode == 'voice_fixture'
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
