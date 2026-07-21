from __future__ import annotations

import pytest

from app.services.two_agent_pipecat_duplex import (
    InMemoryDuplexFrameTransport,
    PipecatAgentGraph,
    PipecatProcessorSpec,
    TwoPipecatDuplexHarness,
    build_builtin_sample_voice_graphs,
)


class FakeLlm:
    def __init__(self, *, provider_id: str, model_name: str, replies: list[str]):
        self.provider_id = provider_id
        self.model_name = model_name
        self.llm_mode = 'mock'
        self.replies = replies
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, history: list[dict]):
        assert history
        self.prompts.append(prompt)
        return self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]


class FakeTts:
    provider_id = 'kokoro'
    model_name = 'kokoro'

    def synthesize(self, text: str) -> bytes:
        return f'wav:{text}'.encode('utf-8')


class FakeAsr:
    provider_id = 'rtc-asr'
    model_name = 'base.en'

    def __init__(self):
        self.receipts: list[str] = []

    def transcribe(self, audio: bytes, *, source_text: str) -> str:
        assert audio.startswith(b'wav:')
        self.receipts.append(source_text)
        return source_text


def test_two_pipecat_duplex_harness_exercises_independent_graphs_and_directions():
    tester_graph, target_graph = build_builtin_sample_voice_graphs(
        tester_llm_provider='openai',
        tester_llm_model='gpt-5.4-mini',
        target_llm_provider='openai',
        target_llm_model='gpt-5.4-mini',
        stt_model='base.en',
        tts_model='kokoro',
        llm_mode='mock',
    )
    tester_asr = FakeAsr()
    target_asr = FakeAsr()
    transport = InMemoryDuplexFrameTransport(run_id='offline-duplex-proof')
    harness = TwoPipecatDuplexHarness(
        tester_graph=tester_graph,
        target_graph=target_graph,
        tester_llm=FakeLlm(
            provider_id='openai',
            model_name='gpt-5.4-mini',
            replies=['I need to cancel after a renewal increase.', 'Thanks, [done]'],
        ),
        target_llm=FakeLlm(
            provider_id='openai',
            model_name='gpt-5.4-mini',
            replies=['I can help and will preserve your cancellation request.'],
        ),
        tester_tts=FakeTts(),
        target_tts=FakeTts(),
        tester_asr=tester_asr,
        target_asr=target_asr,
        transport=transport,
        max_turn_pairs=1,
    )

    result = harness.run(scenario_instruction='Test cancellation rescue.')

    assert result.status == 'completed'
    assert result.termination_reason == 'max_turn_pairs'
    assert result.provenance()['architecture'] == 'two_independent_pipecat_graphs_duplex_frames'
    assert result.graphs['tester']['participant_id'] == 'pipecat_tester'
    assert result.graphs['target']['participant_id'] == 'pipecat_target'
    assert [item['name'] for item in result.graphs['tester']['processors']] == ['rtc-asr', 'llm', 'kokoro']
    assert [item['name'] for item in result.graphs['target']['processors']] == ['rtc-asr', 'llm', 'kokoro']
    assert [frame['direction'] for frame in result.frames] == ['tester_to_target', 'target_to_tester']
    assert transport.frames_for('pipecat_target')[0].source_text == 'I need to cancel after a renewal increase.'
    assert transport.frames_for('pipecat_tester')[0].source_text.startswith('I can help')
    assert target_asr.receipts == ['I need to cancel after a renewal increase.']
    assert tester_asr.receipts == ['I can help and will preserve your cancellation request.']

    transcript = result.transcript()
    assert [item['source'] for item in transcript] == [
        'tester.llm_output',
        'target.asr_receipt',
        'target.llm_output',
        'tester.asr_receipt',
    ]
    assert {item['direction'] for item in transcript} == {'tester_to_target', 'target_to_tester'}


def test_duplex_harness_records_external_live_graph_receipts():
    tester_graph, target_graph = build_builtin_sample_voice_graphs(
        tester_llm_provider='openai',
        tester_llm_model='tester-model',
        target_llm_provider='openai',
        target_llm_model='target-model',
        stt_model='base.en',
        tts_model='kokoro',
        llm_mode='mock',
    )
    dependency = FakeLlm(provider_id='openai', model_name='unused', replies=['unused'])
    harness = TwoPipecatDuplexHarness(
        tester_graph=tester_graph,
        target_graph=target_graph,
        tester_llm=dependency,
        target_llm=dependency,
        tester_tts=FakeTts(),
        target_tts=FakeTts(),
        tester_asr=FakeAsr(),
        target_asr=FakeAsr(),
        transport=InMemoryDuplexFrameTransport(run_id='live-exchange'),
    )

    tester_turn, target_turn = harness.record_exchange(
        tester_text='Please cancel.',
        tester_audio=b'tester-wav',
        target_receipt='Please cancel.',
        target_text='I can help.',
        target_audio=b'target-wav',
        tester_receipt='I can help with cancellation.',
    )

    assert tester_turn.asr_receipt == 'Please cancel.'
    assert target_turn.llm_output == 'I can help.'
    assert target_turn.asr_receipt == 'I can help with cancellation.'
    assert [frame.direction for frame in harness.transport.frames] == [
        'tester_to_target',
        'target_to_tester',
    ]


def test_duplex_transport_rejects_wrong_direction_and_empty_audio():
    transport = InMemoryDuplexFrameTransport(run_id='direction-proof')
    with pytest.raises(ValueError, match='tester_to_target requires pipecat_tester -> pipecat_target'):
        transport.send(
            direction='tester_to_target',
            sender='pipecat_target',
            receiver='pipecat_tester',
            audio_bytes=b'wav',
            source_text='wrong way',
        )
    with pytest.raises(ValueError, match='must carry bytes'):
        transport.send(
            direction='target_to_tester',
            sender='pipecat_target',
            receiver='pipecat_tester',
            audio_bytes=b'',
            source_text='empty',
        )


def test_pipecat_graph_requires_canonical_processor_order():
    with pytest.raises(ValueError, match='rtc-asr -> llm -> kokoro'):
        PipecatAgentGraph(
            participant_id='pipecat_tester',
            label='bad graph',
            processors=(
                PipecatProcessorSpec('llm', 'openai', 'gpt-5.4-mini'),
                PipecatProcessorSpec('rtc-asr', 'rtc-asr', 'base.en'),
                PipecatProcessorSpec('kokoro', 'kokoro', 'kokoro'),
            ),
            llm_mode='mock',
        )
