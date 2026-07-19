"""Pure Target → Tester → Executor → Evidence provenance rules.

Targets identify the system under test and its destination. Executors identify
the runtime/transport that conducts the test. This module intentionally has no
network or mutable readiness state.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


TargetKind = Literal[
    'builtin_sample_text',
    'openai_text',
    'http_text_endpoint',
    'saved_text_replay',
    'builtin_sample_voice',
    'saved_voice_replay',
    'sip_agent',
    'phone_agent',
    'browser_webrtc_agent',
    'unknown',
]
TesterId = Literal['scenario_simulator', 'fixture_replay', 'pipecat_tester']
ExecutorId = Literal[
    'local_async_runner',
    'evidence_replay',
    'cae_local_audio_loop',
    'acc_browser_webrtc',
    'acc_sip',
    'acc_phone',
]
EvidenceSource = Literal['generated_text', 'provider_response', 'saved_replay', 'local_audio_loop', 'acc_live']

BUILTIN_SAMPLE_VOICE_HONESTY = (
    'Built-in generalist agent · current-run local audio and scoring · no browser, phone, or SIP call'
)

_COMPATIBLE_EXECUTORS: dict[str, frozenset[ExecutorId]] = {
    'mock_agent': frozenset({'local_async_runner'}),
    'openai_codex': frozenset({'local_async_runner'}),
    'http_endpoint': frozenset({'local_async_runner'}),
    'offline_acc_fixture': frozenset({'evidence_replay'}),
    'builtin_sample_voice': frozenset({'cae_local_audio_loop'}),
    'voice_fixture': frozenset({'evidence_replay'}),
    'sip_agent': frozenset({'acc_sip'}),
    'phone_agent': frozenset({'acc_phone'}),
    'browser_webrtc_agent': frozenset({'acc_browser_webrtc'}),
}
_UNAVAILABLE_EXECUTORS = frozenset({'acc_browser_webrtc', 'acc_sip', 'acc_phone'})


class ExecutionRunProvenance(BaseModel):
    model_config = ConfigDict(extra='forbid')

    target_id: str | None = None
    target_kind: TargetKind
    target_channel: Literal['text', 'voice']
    tester_id: TesterId
    executor_id: ExecutorId
    evidence_source: EvidenceSource
    live_external_connection: bool = False
    saved_evidence: bool = False
    synthetic_media: bool = False
    honesty_label: str | None = None


class ExecutionDefaults(BaseModel):
    model_config = ConfigDict(extra='forbid')

    mode: Literal['text_callable', 'voice_fixture', 'pipecat_webrtc']
    tester_id: TesterId
    executor_id: ExecutorId
    audio_transport: Literal['none', 'pipecat_small_webrtc', 'freeswitch_verto_sip'] = 'none'


def normalize_agent_target(target: str | None) -> str:
    return str(target or '').strip() or 'mock_agent'


def execution_defaults_for_target(target: str | None) -> ExecutionDefaults:
    normalized = normalize_agent_target(target)
    if normalized == 'builtin_sample_voice':
        return ExecutionDefaults(
            mode='pipecat_webrtc',
            tester_id='pipecat_tester',
            executor_id='cae_local_audio_loop',
            audio_transport='pipecat_small_webrtc',
        )
    if normalized == 'voice_fixture':
        return ExecutionDefaults(
            mode='voice_fixture',
            tester_id='fixture_replay',
            executor_id='evidence_replay',
        )
    if normalized == 'offline_acc_fixture':
        return ExecutionDefaults(
            mode='text_callable',
            tester_id='fixture_replay',
            executor_id='evidence_replay',
        )
    if normalized == 'sip_agent':
        return ExecutionDefaults(mode='text_callable', tester_id='scenario_simulator', executor_id='acc_sip')
    if normalized == 'phone_agent':
        return ExecutionDefaults(mode='text_callable', tester_id='scenario_simulator', executor_id='acc_phone')
    if normalized == 'browser_webrtc_agent':
        return ExecutionDefaults(
            mode='text_callable',
            tester_id='scenario_simulator',
            executor_id='acc_browser_webrtc',
        )
    return ExecutionDefaults(
        mode='text_callable',
        tester_id='scenario_simulator',
        executor_id='local_async_runner',
    )


def assert_execution_compatible(
    *,
    agent_target: str | None,
    mode: str,
    tester_id: str,
    executor_id: str,
) -> None:
    normalized = normalize_agent_target(agent_target)
    defaults = execution_defaults_for_target(normalized)
    allowed = _COMPATIBLE_EXECUTORS.get(normalized, frozenset({'local_async_runner'}))
    if executor_id not in allowed:
        raise ValueError(
            f'Executor "{executor_id}" is not compatible with target "{normalized}". '
            f'Allowed: {", ".join(sorted(allowed))}.'
        )
    if mode != defaults.mode or tester_id != defaults.tester_id:
        raise ValueError(
            f'Target "{normalized}" requires mode={defaults.mode}, '
            f'tester_id={defaults.tester_id}, and executor_id={defaults.executor_id}.'
        )
    if executor_id in _UNAVAILABLE_EXECUTORS:
        raise ValueError(
            f'Executor "{executor_id}" is not implemented in ConversationAgentEvals yet. '
            'ACC remains the live media owner; connection readiness does not make the CAE adapter executable.'
        )


def target_kind_for_agent_target(target: str | None) -> TargetKind:
    mapping: dict[str, TargetKind] = {
        'mock_agent': 'builtin_sample_text',
        'openai_codex': 'openai_text',
        'http_endpoint': 'http_text_endpoint',
        'offline_acc_fixture': 'saved_text_replay',
        'builtin_sample_voice': 'builtin_sample_voice',
        'voice_fixture': 'saved_voice_replay',
        'sip_agent': 'sip_agent',
        'phone_agent': 'phone_agent',
        'browser_webrtc_agent': 'browser_webrtc_agent',
    }
    return mapping.get(normalize_agent_target(target), 'unknown')


def build_run_provenance(
    *,
    agent: dict[str, Any] | None,
    agent_target: str | None,
    tester_id: TesterId,
    executor_id: ExecutorId,
    mode: str,
    text_callable: str | None = None,
) -> ExecutionRunProvenance:
    target = normalize_agent_target(agent_target or (agent or {}).get('target') or text_callable)
    channel = str((agent or {}).get('channel') or ('voice' if target in {
        'builtin_sample_voice', 'voice_fixture', 'sip_agent', 'phone_agent', 'browser_webrtc_agent'
    } else 'text'))

    if executor_id == 'cae_local_audio_loop':
        evidence_source: EvidenceSource = 'local_audio_loop'
        honesty_label = BUILTIN_SAMPLE_VOICE_HONESTY
        saved_evidence = False
        synthetic_media = True
    elif executor_id == 'evidence_replay' or mode == 'voice_fixture' or target == 'offline_acc_fixture':
        evidence_source = 'saved_replay'
        honesty_label = 'Saved conversation replay · evidence evaluation · no live call'
        saved_evidence = True
        synthetic_media = target == 'voice_fixture' or mode == 'voice_fixture'
    elif executor_id in _UNAVAILABLE_EXECUTORS:
        evidence_source = 'acc_live'
        honesty_label = None
        saved_evidence = False
        synthetic_media = False
    elif target in {'openai_codex', 'http_endpoint'}:
        evidence_source = 'provider_response'
        honesty_label = None
        saved_evidence = False
        synthetic_media = False
    else:
        evidence_source = 'generated_text'
        honesty_label = None
        saved_evidence = False
        synthetic_media = False

    return ExecutionRunProvenance(
        target_id=str((agent or {}).get('id') or '') or None,
        target_kind=target_kind_for_agent_target(target),
        target_channel='voice' if channel == 'voice' else 'text',
        tester_id=tester_id,
        executor_id=executor_id,
        evidence_source=evidence_source,
        live_external_connection=executor_id in _UNAVAILABLE_EXECUTORS,
        saved_evidence=saved_evidence,
        synthetic_media=synthetic_media,
        honesty_label=honesty_label,
    )
