import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import server
from speech_configuration import SpeechSelection
from tester_speech import synthesize_tester, tester_tts_issues as tts_issues, tester_tts_request as tts_request
from test_streaming_media import _wav_header


def selected(provider='deepgram'):
    return SpeechSelection(tts_provider=provider, tts_model='aura-2' if provider == 'deepgram' else 'kokoro',
        tester_voice='aura-2-asteria-en' if provider == 'deepgram' else 'af_heart',
        target_voice='aura-2-orion-en' if provider == 'deepgram' else 'af_bella')


@pytest.mark.parametrize('provider', ['deepgram', 'kokoro'])
def test_caller_uses_selected_provider_without_fallback(provider, monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'test-secret')
    observed = []
    wav = _wav_header(data_size=1920) + b'\x01\x00' * 960
    def respond(request):
        observed.append(request)
        return httpx.Response(200, content=wav)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await synthesize_tester('Hello', selected(provider), kokoro_url='http://kokoro', client=client)
    assert asyncio.run(run()) == wav
    assert len(observed) == 1
    request = observed[0]
    if provider == 'deepgram':
        assert request.url.host == 'api.deepgram.com'
        assert request.url.params['model'] == 'aura-2-asteria-en'
        assert request.headers['authorization'] == 'Token test-secret'
        assert json.loads(request.content) == {'text': 'Hello'}
    else:
        assert request.url.host == 'kokoro'
        assert json.loads(request.content)['voice'] == 'af_heart'
        assert 'authorization' not in request.headers


def test_tester_readiness_only_checks_tts_not_builtin_stt(monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'test-secret')
    assert tts_issues(selected(), '') == []
    monkeypatch.delenv('DEEPGRAM_API_KEY')
    assert 'DEEPGRAM_API_KEY' in ' '.join(tts_issues(selected(), 'http://kokoro'))


def test_cloud_error_does_not_expose_secret_or_fall_back(monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'test-secret')
    observed = []
    def respond(request):
        observed.append(request)
        return httpx.Response(401, text='secret remote account details')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await synthesize_tester('Hello', selected(), kokoro_url='http://kokoro', client=client)
    with pytest.raises(RuntimeError, match='deepgram tester speech synthesis failed') as error:
        asyncio.run(run())
    assert 'secret' not in str(error.value)
    assert len(observed) == 1


def test_tester_speech_routes_require_token_and_cloud_key(monkeypatch):
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'internal-test-token')
    client = TestClient(server.app)
    assert client.post('/reference-tester/synthesize', json={'text': 'Hello'}).status_code in {401, 403}
    monkeypatch.delenv('DEEPGRAM_API_KEY', raising=False)
    response = client.post('/reference-tester/speech-readiness', json=selected().model_dump(),
        headers={'x-cae-reference-token': 'internal-test-token'})
    assert response.status_code == 503
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'test-secret')
    response = client.post('/reference-tester/speech-readiness', json=selected().model_dump(),
        headers={'x-cae-reference-token': 'internal-test-token'})
    assert response.json() == {'ready': True, 'provider': 'deepgram'}


def test_kokoro_switch_retains_local_model_and_custom_voice():
    request = tts_request(selected('kokoro'), 'Hello', kokoro_url='http://local/', voice='bf_emma')
    assert request['url'] == 'http://local/v1/audio/speech'
    assert request['json']['voice'] == 'bf_emma'


def test_buffered_deepgram_streaming_wav_is_finalized_before_api_handoff(monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'test-secret')
    wav = _wav_header(data_size=1920) + b'\x01\x00' * 960
    streamed = wav[:4] + (0x7fff0024).to_bytes(4, 'little') + wav[8:40] + (0x7fff0000).to_bytes(4, 'little') + wav[44:]

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=streamed))) as client:
            return await synthesize_tester('Hello', selected(), kokoro_url='http://kokoro', client=client)

    assert asyncio.run(run()) == wav
