from __future__ import annotations

import asyncio
import base64
import io
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.services.acc_realtime_target import TesterAct, TesterScenarioConfig
from app.services.execution_audio import ExecutionAudioTargetAdapter
from app.services.pipecat_tester_agent import PipecatTesterAgentRunner
from app.services.reference_generalist_agent import (
    KokoroTesterTtsRenderer,
    ReferenceMediaServices,
    ReferencePipecatAgentTransport,
    ReferencePipecatTesterGraphRenderer,
    ReferenceRuntimeConfig,
    ReferenceRuntimeError,
)


client = TestClient(app)


def _wav(value: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes((value.to_bytes(2, 'little', signed=True)) * 160)
    return output.getvalue()


class FakeCompletion:
    provider_id = 'fake_openai'

    def status(self):
        return {'status': 'connected', 'provider': 'fake_openai'}

    def complete(self, prompt: str, *, model_name: str | None = None) -> str:
        assert 'Caller:' in prompt
        assert model_name == 'fake-model'
        return 'I can help with that request.'


class FakeMedia:
    def __init__(self):
        self.transcriptions = 0
        self.client = self

    def get(self, url: str, **kwargs):
        del kwargs
        assert url.endswith('/reference-agent/readiness')
        return FakeResponse({'ready': True, 'pipeline_runtime': True, 'rtc_asr_configured': True, 'kokoro_configured': True})

    def post(self, url: str, *, json=None, timeout=None, headers=None):
        del timeout, headers
        if url.endswith('/reference-tester/turn'):
            assert json['scenario_instruction']
            return FakeResponse({
                'tester_text': 'Please help me cancel.',
                'tester_audio_wav_base64': base64.b64encode(_wav(3)).decode('ascii'),
                'pipeline': {'provider': 'pipecat', 'processors': ['rtc-asr', 'llm', 'kokoro']},
            })
        assert url.endswith('/reference-agent/turn')
        assert json['audio_wav_base64']
        self.transcriptions += 1
        return FakeResponse({
            'caller_transcript': 'Please help me cancel.',
            'agent_text': 'I can help with that request.',
            'agent_audio_wav_base64': base64.b64encode(_wav(2)).decode('ascii'),
            'pipeline': {'provider': 'pipecat', 'current_run': True},
        })

    def readiness(self):
        return {
            'stt': {'provider': 'rtc-asr', 'backend': 'faster-whisper', 'model': 'base.en', 'status': 'ready'},
            'tts': {'provider': 'kokoro', 'model': 'kokoro', 'voice': 'af_heart', 'status': 'ready'},
        }

    def synthesize(self, text: str) -> bytes:
        assert text
        return _wav(len(text) % 100 + 1)

    def transcribe(self, wav_bytes: bytes) -> str:
        assert wav_bytes
        self.transcriptions += 1
        return 'Tester heard a cancellable request.'


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ''

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._payload


def test_reference_tester_to_agent_contract_uses_only_current_run(tmp_path: Path):
    async def run():
        media = FakeMedia()
        config = ReferenceRuntimeConfig(
            rtc_asr_base_url='http://rtc-asr.test',
            kokoro_base_url='http://kokoro.test',
            llm_model='fake-model',
            internal_token='test-token',
        )
        observed = []
        transport = ReferencePipecatAgentTransport(
            artifact_dir=tmp_path,
            media=media,  # type: ignore[arg-type]
            completion=FakeCompletion(),
            config=config,
            event_observer=observed.append,
        )
        runner = PipecatTesterAgentRunner(
            target=ExecutionAudioTargetAdapter(transport),
            tts_renderer=KokoroTesterTtsRenderer(media),  # type: ignore[arg-type]
        )
        result = await runner.run(TesterScenarioConfig(
            scenario_id='reference-contract',
            goal='prove current-run media',
            allowed_caller_acts=['ask_for_help'],
            acts=[TesterAct(
                act_id='ask_for_help',
                objective='ask',
                example_utterance='Please help me.',
                terminal_after=True,
            )],
            max_turns=1,
            total_timeout_seconds=10,
            observation_mode='semantic',
            seed=1,
        ))
        assert result['status'] == 'completed'
        session_id = result['session_id']
        turns = transport.transcription_turns(session_id)
        assert [turn.speaker for turn in turns] == ['Caller', 'Agent']
        assert turns[0].source == 'rtc-asr.current_run'
        assert turns[1].source == 'rtc-asr.current_run'
        assert [turn.direction for turn in turns] == ['tester_to_target', 'target_to_tester']
        assert [turn.evidence_role for turn in turns] == ['target_asr_receipt', 'tester_asr_receipt']
        assert turns[0].frame_metadata['source'] == 'tester_kokoro_audio'
        assert turns[1].frame_metadata['source'] == 'target_kokoro_audio'
        assert turns[1].text == 'Tester heard a cancellable request.'
        assert turns[1].frame_metadata['source_text'] == 'I can help with that request.'
        assert turns[1].frame_metadata['asr_receipt'] == 'Tester heard a cancellable request.'
        assert result['turns'][0]['observation']['agent_text'] == 'Tester heard a cancellable request.'
        assert media.transcriptions == 2
        proof = transport.session_proof(session_id)
        assert proof['tester_participant'] == 'pipecat_tester'
        assert proof['target_participant'] == 'pipecat_target'
        assert proof['reference_endpoint'] == 'reference_pipecat_agent'
        assert proof['evidence_source'] == 'current_run'
        assert proof['architecture'] == 'two_independent_pipecat_graphs_duplex_frames'
        assert proof['graphs']['tester']['participant_id'] == 'pipecat_tester'
        assert proof['graphs']['target']['participant_id'] == 'pipecat_target'
        assert proof['duplex']['frame_count'] == 2
        assert [frame['direction'] for frame in proof['duplex']['frames']] == [
            'tester_to_target',
            'target_to_tester',
        ]
        assert transport.recording_handle(session_id).uri.endswith('.wav')
        assert len(transport.latency_marks(session_id)) == 1
        assert [event['speaker'] for event in observed] == ['Caller', 'Agent']
        assert all(event['text'] for event in observed)
        assert all(event['audio'] for event in observed)

    asyncio.run(run())


def test_reference_tester_renderer_uses_remote_pipecat_graph(tmp_path: Path):
    async def run():
        media = FakeMedia()
        transport = ReferencePipecatAgentTransport(
            artifact_dir=tmp_path,
            media=media,  # type: ignore[arg-type]
            completion=FakeCompletion(),
            config=ReferenceRuntimeConfig(
                rtc_asr_base_url='http://rtc-asr.test',
                kokoro_base_url='http://kokoro.test',
                llm_model='fake-model',
                tester_llm_model='fake-model',
                internal_token='test-token',
            ),
        )
        renderer = ReferencePipecatTesterGraphRenderer(transport)
        config = TesterScenarioConfig(
            scenario_id='reference-contract',
            goal='prove tester graph execution',
            allowed_caller_acts=['ask_for_help'],
            acts=[],
            seed=1,
        )
        act = TesterAct(
            act_id='ask_for_help',
            objective='ask',
            example_utterance='Please help me.',
        )

        text = await renderer.render(act, None, config)
        fixture = await renderer.synthesize(text, seed=1, metadata={'turn_index': 1, 'act_id': act.act_id})

        assert text == 'Please help me cancel.'
        assert fixture.metadata['source'] == 'pipecat_tester_graph'
        assert fixture.metadata['audio_bytes'].startswith(b'RIFF')

    asyncio.run(run())


def test_reference_media_readiness_fails_closed_without_each_service():
    with pytest.raises(ReferenceRuntimeError, match='RTC_ASR_BASE_URL'):
        ReferenceMediaServices(ReferenceRuntimeConfig(
            rtc_asr_base_url='',
            kokoro_base_url='http://kokoro.test',
        )).readiness()


def test_reference_media_readiness_honors_custom_asr_health_path():
    class ReadinessClient:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            if url.endswith('/readyz'):
                return FakeResponse({'backend': 'faster-whisper', 'model': 'base.en'})
            return FakeResponse({'status': 'healthy'})

    client = ReadinessClient()
    readiness = ReferenceMediaServices(
        ReferenceRuntimeConfig(
            rtc_asr_base_url='http://rtc-asr.test',
            rtc_asr_health_path='/readyz',
            kokoro_base_url='http://kokoro.test',
        ),
        client=client,  # type: ignore[arg-type]
    ).readiness()

    assert client.urls[0] == 'http://rtc-asr.test/readyz'
    assert readiness['stt']['backend'] == 'faster-whisper'


def test_reference_completion_callback_requires_internal_token(monkeypatch):
    import app.routes.execution as execution_routes

    monkeypatch.setenv('REFERENCE_AGENT_INTERNAL_TOKEN', 'shared-test-token')
    denied = client.post('/api/execution/reference/complete', json={'prompt': 'hello', 'model_name': 'fake'})
    assert denied.status_code == 403

    monkeypatch.setattr(execution_routes, 'resolve_reference_completion_provider', lambda: FakeCompletion())
    accepted = client.post(
        '/api/execution/reference/complete',
        headers={'x-cae-reference-token': 'shared-test-token'},
        json={'prompt': 'Caller: hello', 'model_name': 'fake-model'},
    )
    assert accepted.status_code == 200
    assert accepted.json()['text'] == 'I can help with that request.'
    with pytest.raises(ReferenceRuntimeError, match='KOKORO_BASE_URL'):
        ReferenceMediaServices(ReferenceRuntimeConfig(
            rtc_asr_base_url='http://rtc-asr.test',
            kokoro_base_url='',
        )).readiness()
