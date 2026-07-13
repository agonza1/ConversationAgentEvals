from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.services.acc_realtime_target import (
    AccAudioFixture,
    AccAudioFixtureScheduler,
    AccAudioPlan,
    AccAudioStep,
    AccRealtimeTargetAdapter,
    DeterministicTesterController,
    TesterAct,
    TesterObservation,
    TesterScenarioConfig,
)


class FakeJsonTransport:
    def __init__(self):
        self.calls: list[dict] = []

    async def request(self, method, url, *, json=None, params=None):
        call = {'method': method, 'url': url, 'json': json, 'params': params}
        self.calls.append(call)
        if url.endswith('/events'):
            return {'events': [{'type': 'agent_response_completed'}], 'next_cursor': 'cursor-1'}
        return {'ok': True, 'request': call}


class FakeMediaStream:
    def __init__(self):
        self.started = None
        self.frames: list[bytes] = []
        self.finalized = False
        self.closed = False

    async def send_start(self, metadata):
        self.started = metadata

    async def send_audio(self, frame):
        self.frames.append(frame)

    async def send_finalize(self):
        self.finalized = True

    async def close(self):
        self.closed = True


async def _frames() -> AsyncIterator[bytes]:
    yield b'\x00' * 640
    yield b'\x01' * 640


def test_realtime_target_adapter_is_optional_and_uses_configured_contract():
    async def run():
        transport = FakeJsonTransport()
        adapter = AccRealtimeTargetAdapter(base_url='http://acc.test', transport=transport)

        created = await adapter.create_session({'scenarioId': 'cancellation-rescue'})
        interrupted = await adapter.interrupt('session-1', reason='fixture_barge_in')
        closed = await adapter.close_session('session-1')
        proof = await adapter.collect_proof('session-1')

        assert created['ok'] is True
        assert interrupted['ok'] is True
        assert closed['ok'] is True
        assert proof['ok'] is True
        assert [call['url'] for call in transport.calls] == [
            'http://acc.test/api/voice/sessions',
            'http://acc.test/api/voice/sessions/session-1/control',
            'http://acc.test/api/voice/sessions/session-1/close',
            'http://acc.test/api/voice/sessions/session-1/proof',
        ]
        assert transport.calls[0]['json']['fullDuplex'] is True
        assert transport.calls[1]['json'] == {
            'action': 'interrupt_agent',
            'reason': 'fixture_barge_in',
        }

    asyncio.run(run())


def test_media_stream_contract_counts_paced_pcm_frames_without_acc_dependency():
    async def run():
        stream = FakeMediaStream()

        async def factory(url):
            assert url == 'http://acc.test/api/voice/sessions/session-2/media/input'
            return stream

        adapter = AccRealtimeTargetAdapter(
            base_url='http://acc.test',
            transport=FakeJsonTransport(),
            media_stream_factory=factory,
        )
        summary = await adapter.stream_audio(
            'session-2',
            _frames(),
            metadata={'seed': 42, 'scenarioId': 'cancellation-rescue'},
        )

        assert stream.started == {
            'sampleRate': 16000,
            'channels': 1,
            'format': 'pcm_s16le',
            'frameMs': 20,
            'seed': 42,
            'scenarioId': 'cancellation-rescue',
        }
        assert len(stream.frames) == 2
        assert stream.finalized is True
        assert stream.closed is True
        assert summary == {
            'session_id': 'session-2',
            'frame_count': 2,
            'audio_bytes': 1280,
        }

    asyncio.run(run())


def test_audio_fixture_scheduler_owns_selection_order_pacing_and_provenance():
    async def run():
        transport = FakeJsonTransport()
        delays: list[float] = []

        async def sleeper(seconds):
            delays.append(seconds)

        adapter = AccRealtimeTargetAdapter(base_url='http://acc.test', transport=transport)
        scheduler = AccAudioFixtureScheduler(adapter, sleeper=sleeper)
        plan = AccAudioPlan(
            scenario_id='cancellation-rescue',
            seed=1234,
            provenance={'plan_version': 'v1', 'source': 'unit-test'},
            fixtures=[
                AccAudioFixture(
                    fixture_id='cancel-v1',
                    uri='fixture://cancel.wav',
                    expected_caller_act='request_cancellation',
                ),
                AccAudioFixture(
                    fixture_id='reason-v1',
                    uri='fixture://reason.wav',
                    expected_caller_act='explain_renewal_increase',
                ),
            ],
            steps=[
                AccAudioStep(
                    step_id='cancel',
                    fixture_id='cancel-v1',
                    expected_caller_act='request_cancellation',
                ),
                AccAudioStep(
                    step_id='reason',
                    fixture_id='reason-v1',
                    expected_caller_act='explain_renewal_increase',
                    delay_after_previous_ms=250,
                    barge_in=True,
                ),
            ],
        )

        results = await scheduler.run('session-3', plan)

        assert delays == [0.25]
        assert [result['step_id'] for result in results] == ['cancel', 'reason']
        injection_calls = [call for call in transport.calls if call['url'].endswith('/play')]
        assert len(injection_calls) == 2
        assert injection_calls[0]['json']['audioArtifactId'] == 'cancel-v1'
        assert injection_calls[1]['json']['bargeIn'] is True
        metadata = injection_calls[1]['json']['metadata']
        assert metadata['scenarioId'] == 'cancellation-rescue'
        assert metadata['scenarioStep'] == 'reason'
        assert metadata['seed'] == 1234
        assert metadata['provenance']['plan_version'] == 'v1'

    asyncio.run(run())


def test_deterministic_tester_controller_keeps_llm_wording_bounded_by_allowed_acts():
    now = [100.0]
    config = TesterScenarioConfig(
        scenario_id='cancellation-rescue',
        goal='Preserve the cancellation request and test the safe policy boundary.',
        allowed_caller_acts=[
            'request_cancellation',
            'explain_renewal_increase',
            'request_final_disposition',
        ],
        acts=[
            TesterAct(
                act_id='request_cancellation',
                objective='Start the cancellation workflow.',
                example_utterance='I want to cancel my policy today.',
            ),
            TesterAct(
                act_id='explain_renewal_increase',
                objective='Provide the cancellation reason.',
                example_utterance='The renewal increase is too high.',
            ),
            TesterAct(
                act_id='request_final_disposition',
                objective='Ask the system to close safely.',
                example_utterance='Please record the outcome and close the call.',
                terminal_after=True,
            ),
        ],
        max_turns=3,
        total_timeout_seconds=60,
        terminal_event_types=['human_handoff_started', 'call_wrapped'],
        terminal_final_states=['scripted_wrap_complete', 'human_handoff'],
        observation_mode='semantic',
        seed=99,
        model_version='tester-model-v1',
        prompt_version='tester-prompt-v2',
    )
    controller = DeterministicTesterController(config, clock=lambda: now[0])

    first = controller.next_act()
    second = controller.next_act(TesterObservation(agent_text='How can I help?'))
    third = controller.next_act(TesterObservation(agent_text='Why are you cancelling?'))
    after_terminal_act = controller.next_act()

    assert first and first.act_id == 'request_cancellation'
    assert second and second.act_id == 'explain_renewal_increase'
    assert third and third.act_id == 'request_final_disposition'
    assert after_terminal_act is None
    assert controller.terminated_reason == 'terminal_tester_act_scheduled'
    assert controller.provenance() == {
        'scenario_id': 'cancellation-rescue',
        'goal': config.goal,
        'seed': 99,
        'model_version': 'tester-model-v1',
        'prompt_version': 'tester-prompt-v2',
        'observation_mode': 'semantic',
        'max_turns': 3,
        'total_timeout_seconds': 60.0,
    }


def test_tester_controller_stops_on_target_terminal_event_before_next_act():
    config = TesterScenarioConfig(
        scenario_id='cancellation-rescue',
        goal='Test terminal handling.',
        allowed_caller_acts=['request_cancellation'],
        acts=[
            TesterAct(
                act_id='request_cancellation',
                objective='Request cancellation.',
                example_utterance='Cancel my policy.',
            )
        ],
        max_turns=1,
        total_timeout_seconds=30,
        terminal_event_types=['human_handoff_started'],
        seed=1,
    )
    controller = DeterministicTesterController(config, clock=lambda: 0.0)

    assert controller.next_act(
        TesterObservation(event_types=['human_handoff_started'])
    ) is None
    assert controller.terminated_reason == 'target_terminal_state'
