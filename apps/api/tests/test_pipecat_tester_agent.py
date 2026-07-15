from __future__ import annotations

import asyncio

from app.services.acc_realtime_target import (
    AccAudioFixture,
    TesterAct,
    TesterObservation,
    TesterScenarioConfig,
)
from app.services.pipecat_tester_agent import (
    PipecatTesterAgentRunner,
    TargetEventObservationProvider,
)


class FakeTarget:
    def __init__(self):
        self.created = []
        self.injections = []
        self.closed = []
        self.proof_calls = []

    async def create_session(self, metadata=None):
        self.created.append(metadata)
        return {'sessionId': 'target-session-1', 'ready': True}

    async def inject_audio(self, session_id, *, fixture, step, scenario_id, seed, provenance):
        payload = {
            'session_id': session_id,
            'fixture_id': fixture.fixture_id,
            'expected_caller_act': fixture.expected_caller_act,
            'step_id': step.step_id,
            'barge_in': step.barge_in,
            'scenario_id': scenario_id,
            'seed': seed,
            'provenance': provenance,
        }
        self.injections.append(payload)
        return {'accepted': True, **payload}

    async def observe_events(self, session_id, *, cursor=None):
        return {
            'events': [
                {
                    'type': 'agent_response_completed',
                    'detail': {'speaker': 'agent', 'text': 'I preserved the cancellation request.'},
                }
            ],
            'next_cursor': 'cursor-1',
            'final_state': {'status': 'active'},
            'terminal': False,
        }

    async def close_session(self, session_id, *, reason='tester_complete'):
        self.closed.append({'session_id': session_id, 'reason': reason})
        return {'closed': True, 'reason': reason}

    async def collect_proof(self, session_id):
        self.proof_calls.append(session_id)
        return {'callId': 'call-1', 'sessionId': session_id, 'proof': True}


class FakeTts:
    def __init__(self, mismatch=False):
        self.calls = []
        self.mismatch = mismatch

    async def synthesize(self, text, *, seed, metadata):
        self.calls.append({'text': text, 'seed': seed, 'metadata': metadata})
        act_id = 'wrong_act' if self.mismatch else metadata['act_id']
        return AccAudioFixture(
            fixture_id=f"tts-{metadata['turn_index']}",
            uri=f"fixture://tts/{metadata['turn_index']}.wav",
            expected_caller_act=act_id,
            metadata={'rendered_text': text},
        )


class FakeWording:
    async def render(self, act, observation, config):
        suffix = f" after {observation.agent_text}" if observation and observation.agent_text else ''
        return f"{act.example_utterance}{suffix}"


class SequencedObservationProvider:
    def __init__(self):
        self.calls = []

    async def observe(self, *, target, session_id, config, turn_index, previous_cursor):
        self.calls.append(
            {
                'session_id': session_id,
                'turn_index': turn_index,
                'previous_cursor': previous_cursor,
                'mode': config.observation_mode,
            }
        )
        terminal = turn_index == 2
        return (
            TesterObservation(
                turn_index=turn_index,
                agent_text=f'agent response {turn_index}',
                event_types=['call_wrapped'] if terminal else ['agent_response_completed'],
                final_state={'outcome': 'scripted_wrap_complete' if terminal else 'active'},
                terminal=terminal,
            ),
            f'cursor-{turn_index}',
        )


def _config(*, observation_mode='semantic'):
    return TesterScenarioConfig(
        scenario_id='cancellation-rescue',
        goal='Preserve cancellation intent and test policy boundaries.',
        allowed_caller_acts=[
            'request_cancellation',
            'explain_renewal_increase',
            'request_final_disposition',
        ],
        acts=[
            TesterAct(
                act_id='request_cancellation',
                objective='Request cancellation.',
                example_utterance='I want to cancel my policy.',
            ),
            TesterAct(
                act_id='explain_renewal_increase',
                objective='Explain the reason.',
                example_utterance='The renewal increase is too high.',
                metadata={'barge_in': True},
            ),
            TesterAct(
                act_id='request_final_disposition',
                objective='Request safe closeout.',
                example_utterance='Please record the outcome and close the call.',
            ),
        ],
        max_turns=3,
        total_timeout_seconds=30,
        terminal_event_types=['call_wrapped', 'human_handoff_started'],
        terminal_final_states=['scripted_wrap_complete', 'human_handoff'],
        observation_mode=observation_mode,
        seed=700,
        model_version='caller-model-v1',
        prompt_version='caller-prompt-v2',
    )


def test_tester_agent_connects_controller_wording_tts_target_observation_and_proof():
    async def run():
        target = FakeTarget()
        tts = FakeTts()
        observer = SequencedObservationProvider()
        runner = PipecatTesterAgentRunner(
            target=target,
            tts_renderer=tts,
            observation_provider=observer,
            wording_renderer=FakeWording(),
        )

        result = await runner.run(_config())

        assert result['status'] == 'completed'
        assert result['termination_reason'] == 'target_terminal_state'
        assert result['error'] is None
        assert result['session_id'] == 'target-session-1'
        assert len(result['turns']) == 2
        assert [turn['act_id'] for turn in result['turns']] == [
            'request_cancellation',
            'explain_renewal_increase',
        ]
        assert result['turns'][1]['utterance'].endswith('after agent response 1')
        assert target.injections[1]['barge_in'] is True
        assert [call['seed'] for call in tts.calls] == [700, 701]
        assert tts.calls[0]['metadata']['model_version'] == 'caller-model-v1'
        assert tts.calls[0]['metadata']['prompt_version'] == 'caller-prompt-v2'
        assert target.closed == [
            {'session_id': 'target-session-1', 'reason': 'target_terminal_state'}
        ]
        assert result['proof']['proof'] is True
        assert result['tester_provenance']['seed'] == 700
        assert result['tester_provenance']['observation_mode'] == 'semantic'

    asyncio.run(run())


def test_default_observer_rejects_fake_acoustic_claims():
    async def run():
        provider = TargetEventObservationProvider()
        target = FakeTarget()
        try:
            await provider.observe(
                target=target,
                session_id='target-session-1',
                config=_config(observation_mode='acoustic'),
                turn_index=1,
                previous_cursor=None,
            )
        except RuntimeError as exc:
            assert 'acoustic observation requires' in str(exc)
        else:
            raise AssertionError('default semantic provider must reject acoustic mode')

    asyncio.run(run())


def test_tts_act_mismatch_fails_run_but_still_closes_and_collects_proof():
    async def run():
        target = FakeTarget()
        runner = PipecatTesterAgentRunner(
            target=target,
            tts_renderer=FakeTts(mismatch=True),
            observation_provider=SequencedObservationProvider(),
        )

        result = await runner.run(_config())

        assert result['status'] == 'needs_review'
        assert result['termination_reason'] == 'runner_error'
        assert 'caller act mismatch' in result['error']
        assert result['turns'] == []
        assert target.closed == [
            {'session_id': 'target-session-1', 'reason': 'runner_error'}
        ]
        assert result['proof']['proof'] is True

    asyncio.run(run())


def test_session_setup_and_cleanup_are_bounded_by_timeouts():
    class HangingCreateTarget(FakeTarget):
        async def create_session(self, metadata=None):
            await asyncio.sleep(5)
            return await super().create_session(metadata)

    class HangingCleanupTarget(FakeTarget):
        async def close_session(self, session_id, *, reason='tester_complete'):
            await asyncio.sleep(5)
            return await super().close_session(session_id, reason=reason)

    async def run():
        create_runner = PipecatTesterAgentRunner(
            target=HangingCreateTarget(),
            tts_renderer=FakeTts(),
            observation_provider=SequencedObservationProvider(),
        )
        create_config = _config()
        create_config.total_timeout_seconds = 0.05
        create_result = await create_runner.run(create_config)
        assert create_result['status'] == 'needs_review'
        assert create_result['error'] == 'tester_total_timeout'
        assert create_result['session_id'] is None
        assert create_result['proof'] is None

        cleanup_runner = PipecatTesterAgentRunner(
            target=HangingCleanupTarget(),
            tts_renderer=FakeTts(),
            observation_provider=SequencedObservationProvider(),
        )
        cleanup_config = _config()
        cleanup_config.total_timeout_seconds = 0.05
        cleanup_result = await cleanup_runner.run(cleanup_config)
        assert cleanup_result['status'] == 'needs_review'
        assert cleanup_result['error'] == 'tester_cleanup_timeout'
        assert cleanup_result['session_id'] == 'target-session-1'

    asyncio.run(run())


def test_terminal_act_failure_uses_runner_error_reason():
    class ExplodingInjectTarget(FakeTarget):
        async def inject_audio(self, session_id, *, fixture, step, scenario_id, seed, provenance):
            raise RuntimeError('injection blew up')

    async def run():
        config = _config()
        config.acts = [
            TesterAct(
                act_id='request_cancellation',
                objective='Request cancellation.',
                example_utterance='I want to cancel my policy.',
                terminal_after=True,
            )
        ]
        config.max_turns = 1
        target = ExplodingInjectTarget()
        runner = PipecatTesterAgentRunner(
            target=target,
            tts_renderer=FakeTts(),
            observation_provider=SequencedObservationProvider(),
        )

        result = await runner.run(config)

        assert result['status'] == 'needs_review'
        assert result['termination_reason'] == 'runner_error'
        assert 'injection blew up' in result['error']
        assert target.closed == [
            {'session_id': 'target-session-1', 'reason': 'runner_error'}
        ]

    asyncio.run(run())

