from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pipecat.frames.frames import Frame, LLMTextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

import speech_providers as providers
from test_streaming_media import _wav_header


class TurnEnd(Frame):
    pass


def selection(stt='deepgram', tts='cartesia'):
    return providers.SpeechSelection(stt_provider=stt, tts_provider=tts,
        stt_model='nova-3' if stt == 'deepgram' else 'ink-2',
        tts_model='sonic-3.6' if tts == 'cartesia' else 'aura-2',
        tester_voice='caller-uuid' if tts == 'cartesia' else 'aura-2-asteria-en',
        target_voice='agent-uuid' if tts == 'cartesia' else 'aura-2-orion-en')


@pytest.mark.parametrize('provider', ['deepgram', 'cartesia'])
def test_tts_streams_real_pcm_and_measures_first_audio(provider, monkeypatch):
    monkeypatch.setenv(f'{provider.upper()}_API_KEY', 'secret-test-token')
    requests = []
    frames = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=_wav_header(data_size=1920) + b'\x01\x00' * 960)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            config = selection(tts=provider)
            processor = providers.build_tts(config, base_url='', voice=config.tester_voice,
                input_type=LLMTextFrame, end_type=TurnEnd, participant='tester', client=client)
            async def push(frame, direction=FrameDirection.DOWNSTREAM):
                frames.append(frame)
            processor.push_frame = push
            await processor.process_frame(LLMTextFrame('Please update my shipping address today.'), FrameDirection.DOWNSTREAM)
            await processor.process_frame(TurnEnd(), FrameDirection.DOWNSTREAM)
            assert processor.sample_rate == 24000
            assert processor.audio == b'\x01\x00' * 960
            assert processor.ttfb_ms is not None
            assert processor.llm_to_first_audio_ms >= processor.ttfb_ms
            assert len([f for f in frames if hasattr(f, 'audio')]) == 2

    asyncio.run(run())
    assert len(requests) == 1
    request = requests[0]
    assert 'secret-test-token' not in str(request.url)
    body = json.loads(request.content)
    if provider == 'deepgram':
        assert request.headers['authorization'] == 'Token secret-test-token'
        assert request.url.params['model'] == 'aura-2-asteria-en'
        assert body == {'text': 'Please update my shipping address today.'}
    else:
        assert request.headers['cartesia-version'] == '2026-03-01'
        assert body['model_id'] == 'sonic-3.6'
        assert body['voice'] == 'caller-uuid'
        assert body['output_format']['encoding'] == 'pcm_s16le'


@pytest.mark.parametrize('provider', ['deepgram', 'cartesia'])
def test_final_segments_do_not_release_turn_before_flush_ack(provider, monkeypatch):
    monkeypatch.setenv(f'{provider.upper()}_API_KEY', 'secret-test-token')

    async def run():
        queue = asyncio.Queue()
        drained = asyncio.Event()
        connections = []
        pushes = []

        class Socket:
            def __init__(self):
                self.sent = []
                self.closed = False
            async def send(self, message):
                self.sent.append(message)
                if message == ('{"type": "CloseStream"}' if provider == 'deepgram' else 'finalize'):
                    if provider == 'deepgram':
                        await queue.put({'type': 'Results', 'is_final': True, 'start': 0, 'duration': 1,
                            'channel': {'alternatives': [{'transcript': 'Do not'}]}})
                        # Duplicate delivery must not repeat a clause.
                        await queue.put({'type': 'Results', 'is_final': True, 'start': 0, 'duration': 1,
                            'channel': {'alternatives': [{'transcript': 'Do not'}]}})
                        await queue.put({'type': 'Results', 'is_final': True, 'start': 1, 'duration': 1,
                            'channel': {'alternatives': [{'transcript': 'cancel.'}]}})
                    else:
                        await queue.put({'type': 'transcript', 'is_final': True, 'text': 'Do not'})
                        await queue.put({'type': 'transcript', 'is_final': True, 'text': 'cancel.'})
                    drained.set()
            async def close(self):
                self.closed = True
            def __aiter__(self):
                return self
            async def __anext__(self):
                return json.dumps(await queue.get())

        socket = Socket()
        async def connect(url, **kwargs):
            connections.append((url, kwargs))
            return socket
        monkeypatch.setattr(providers.websockets, 'connect', connect)
        processor = providers.build_asr(selection(stt=provider), base_url='', stream_path='',
            participant='target', final_frame_type=TranscriptionFrame, end_type=TurnEnd)
        async def push(frame, direction=FrameDirection.DOWNSTREAM):
            pushes.append(frame)
        processor.push_frame = push
        processor.pre_roll.append(b'\x01\x00' * 320)
        await processor._start_utterance(FrameDirection.DOWNSTREAM)
        finalizing = asyncio.create_task(processor._finalize(FrameDirection.DOWNSTREAM, wait_for_final=True))
        await drained.wait()
        await asyncio.sleep(0.01)
        assert processor.transcript == 'Do not cancel.'
        assert not finalizing.done()
        assert not any(isinstance(f, TranscriptionFrame) for f in pushes)
        await queue.put({'type': 'Metadata' if provider == 'deepgram' else 'flush_done'})
        await finalizing
        await processor._emit_turn_final(FrameDirection.DOWNSTREAM)
        finals = [f for f in pushes if isinstance(f, TranscriptionFrame)]
        assert len(finals) == 1 and finals[0].text == 'Do not cancel.'
        assert processor.final_at >= processor.speech_ended_at
        assert b'\x01\x00' * 320 in socket.sent
        url, kwargs = connections[0]
        assert 'secret-test-token' not in url
        query = parse_qs(urlsplit(url).query)
        assert query['sample_rate'] == ['16000']
        assert query['model'] == ['nova-3' if provider == 'deepgram' else 'ink-2']
        assert any('secret-test-token' in str(value) for value in kwargs.values())
        await processor.cleanup()
        assert socket.closed

    asyncio.run(run())


@pytest.mark.parametrize('provider', ['deepgram', 'cartesia'])
def test_disconnect_is_an_error_and_does_not_release_a_successful_turn(provider):
    async def run():
        class ClosedSocket:
            async def __aiter__(self):
                yield json.dumps({'type': 'error', 'message': 'secret-that-must-not-be-reflected'})
        processor = providers.StreamingCloudAsrProcessor(selection=selection(stt=provider),
            participant='target', final_frame_type=TranscriptionFrame, end_type=TurnEnd)
        processor.websocket = ClosedSocket()
        await processor._receive()
        with pytest.raises(RuntimeError, match='stream failed') as error:
            await processor._wait_for_final()
        assert 'secret-that' not in str(error.value)
        processor.websocket = None
        await processor.cleanup()
    asyncio.run(run())
