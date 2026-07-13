from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from app.services.acc_realtime_target import (
    AccAudioFixture,
    AccAudioStep,
    AccRealtimeTargetAdapter,
    DeterministicTesterController,
    TesterAct,
    TesterObservation,
    TesterScenarioConfig,
    TesterTtsRenderer,
    TesterWordingRenderer,
)


class TesterObservationProvider(Protocol):
    async def observe(
        self,
        *,
        target: AccRealtimeTargetAdapter,
        session_id: str,
        config: TesterScenarioConfig,
        turn_index: int,
        previous_cursor: str | None,
    ) -> tuple[TesterObservation, str | None]:
        ...


@dataclass(slots=True)
class TesterTurnRecord:
    turn_index: int
    act_id: str
    objective: str
    utterance: str
    fixture_id: str
    expected_caller_act: str
    injection: dict[str, Any]
    observation: dict[str, Any]


class TargetEventObservationProvider:
    """Build semantic observations from a target event/proof endpoint.

    Acoustic observation requires an explicitly injected provider that listens to and
    transcribes the target output audio. This default refuses to pretend event text is
    an acoustic observation.
    """

    async def observe(
        self,
        *,
        target: AccRealtimeTargetAdapter,
        session_id: str,
        config: TesterScenarioConfig,
        turn_index: int,
        previous_cursor: str | None,
    ) -> tuple[TesterObservation, str | None]:
        if config.observation_mode == 'acoustic':
            raise RuntimeError(
                'acoustic observation requires an explicit output-audio/STT observation provider'
            )

        payload = await target.observe_events(session_id, cursor=previous_cursor)
        events = payload.get('events') if isinstance(payload.get('events'), list) else []
        event_types = [
            str(event.get('type'))
            for event in events
            if isinstance(event, dict) and event.get('type')
        ]
        agent_text = _latest_agent_text(events, payload)
        final_state = payload.get('final_state') if isinstance(payload.get('final_state'), dict) else {}
        terminal = bool(payload.get('terminal'))
        next_cursor_value = payload.get('next_cursor') or payload.get('nextCursor')
        next_cursor = str(next_cursor_value) if next_cursor_value is not None else previous_cursor
        return (
            TesterObservation(
                turn_index=turn_index,
                agent_text=agent_text,
                event_types=event_types,
                final_state=final_state,
                terminal=terminal,
            ),
            next_cursor,
        )


class PipecatTesterAgentRunner:
    """Drive a bounded caller agent against an optional realtime target.

    The runner owns scenario order, seed, wording, caller TTS, and observation policy.
    The target owns its persistent media session and actual audio decoding/injection.
    ASSERT remains downstream and is not used to choose tester acts.
    """

    def __init__(
        self,
        *,
        target: AccRealtimeTargetAdapter,
        tts_renderer: TesterTtsRenderer,
        observation_provider: TesterObservationProvider | None = None,
        wording_renderer: TesterWordingRenderer | None = None,
    ):
        self.target = target
        self.tts_renderer = tts_renderer
        self.observation_provider = observation_provider or TargetEventObservationProvider()
        self.wording_renderer = wording_renderer

    async def run(self, config: TesterScenarioConfig) -> dict[str, Any]:
        controller = DeterministicTesterController(config)
        session_payload = await self.target.create_session(
            {
                'scenarioId': config.scenario_id,
                'tester': controller.provenance(),
                'observationMode': config.observation_mode,
            }
        )
        session_id = _session_id(session_payload)
        cursor: str | None = None
        observation: TesterObservation | None = None
        turns: list[TesterTurnRecord] = []
        close_payload: dict[str, Any] | None = None
        proof: dict[str, Any] | None = None
        error: str | None = None

        try:
            async with asyncio.timeout(config.total_timeout_seconds):
                while True:
                    act = controller.next_act(observation)
                    if act is None:
                        break
                    turn_index = len(turns) + 1
                    utterance = await controller.render_utterance(
                        act,
                        observation,
                        renderer=self.wording_renderer,
                    )
                    fixture = await self.tts_renderer.synthesize(
                        utterance,
                        seed=config.seed + turn_index - 1,
                        metadata={
                            'scenario_id': config.scenario_id,
                            'turn_index': turn_index,
                            'act_id': act.act_id,
                            'objective': act.objective,
                            'model_version': config.model_version,
                            'prompt_version': config.prompt_version,
                        },
                    )
                    _validate_tts_fixture(fixture, act)
                    step = AccAudioStep(
                        step_id=f'tester-turn-{turn_index}',
                        fixture_id=fixture.fixture_id,
                        expected_caller_act=act.act_id,
                        pacing_mode='realtime',
                        acceleration_factor=1.0,
                        barge_in=bool(act.metadata.get('barge_in')),
                        metadata={
                            'testerAgent': True,
                            'utterance': utterance,
                            'objective': act.objective,
                        },
                    )
                    injection = await self.target.inject_audio(
                        session_id,
                        fixture=fixture,
                        step=step,
                        scenario_id=config.scenario_id,
                        seed=config.seed,
                        provenance=controller.provenance(),
                    )
                    observation, cursor = await self.observation_provider.observe(
                        target=self.target,
                        session_id=session_id,
                        config=config,
                        turn_index=turn_index,
                        previous_cursor=cursor,
                    )
                    turns.append(
                        TesterTurnRecord(
                            turn_index=turn_index,
                            act_id=act.act_id,
                            objective=act.objective,
                            utterance=utterance,
                            fixture_id=fixture.fixture_id,
                            expected_caller_act=fixture.expected_caller_act,
                            injection=injection,
                            observation=observation.model_dump(mode='json'),
                        )
                    )
        except TimeoutError:
            controller.terminated_reason = 'total_timeout'
            error = 'tester_total_timeout'
        except Exception as exc:
            controller.terminated_reason = controller.terminated_reason or 'runner_error'
            error = str(exc)
        finally:
            try:
                close_payload = await self.target.close_session(
                    session_id,
                    reason=controller.terminated_reason or 'tester_complete',
                )
            except Exception as exc:
                error = error or f'target_close_failed: {exc}'
            try:
                proof = await self.target.collect_proof(session_id)
            except Exception as exc:
                error = error or f'target_proof_failed: {exc}'

        return {
            'scenario_id': config.scenario_id,
            'session_id': session_id,
            'status': 'completed' if error is None else 'needs_review',
            'termination_reason': controller.terminated_reason or 'plan_complete',
            'error': error,
            'tester_provenance': controller.provenance(),
            'session': session_payload,
            'turns': [asdict(record) for record in turns],
            'close': close_payload,
            'proof': proof,
        }


def _session_id(payload: dict[str, Any]) -> str:
    candidates = [
        payload.get('sessionId'),
        payload.get('session_id'),
        payload.get('id'),
        (payload.get('session') or {}).get('id') if isinstance(payload.get('session'), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise RuntimeError('target create_session response did not contain a session id')


def _validate_tts_fixture(fixture: AccAudioFixture, act: TesterAct) -> None:
    if fixture.expected_caller_act != act.act_id:
        raise RuntimeError(
            'tester TTS fixture caller act mismatch: '
            f'expected {act.act_id}, got {fixture.expected_caller_act}'
        )


def _latest_agent_text(events: list[Any], payload: dict[str, Any]) -> str | None:
    direct = payload.get('agent_text') or payload.get('agentText')
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get('type') or '')
        detail = event.get('detail') if isinstance(event.get('detail'), dict) else {}
        speaker = event.get('speaker') or detail.get('speaker')
        text = event.get('text') or detail.get('text') or detail.get('transcript')
        if isinstance(text, str) and text.strip() and (
            speaker == 'agent'
            or event_type in {'agent_response', 'agent_transcript', 'agent_response_completed'}
        ):
            return text.strip()
    return None
