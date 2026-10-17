import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import speech_settings as settings
from app.services.reference_generalist_agent import ReferenceRuntimeConfig, ReferenceMediaServices


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'settings_path', lambda: tmp_path / 'speech.json')
    monkeypatch.delenv('CAE_STT_PROVIDER', raising=False)
    monkeypatch.delenv('CAE_TTS_PROVIDER', raising=False)


def cloud_selection():
    return settings.SpeechSelection(stt_provider='deepgram', tts_provider='cartesia',
        stt_model='nova-3', tts_model='sonic-3.6', tester_voice='caller-id', target_voice='agent-id')


def test_console_persists_selection_without_credentials(monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'not-in-browser-or-file')
    client = TestClient(app)
    assert client.get('/api/execution/speech-settings').json()['selection']['stt_provider'] == 'rtc-asr'
    config = cloud_selection().model_dump()
    response = client.put('/api/execution/speech-settings', json=config)
    assert response.status_code == 200
    assert response.json()['selection'] == config
    assert json.loads(settings.settings_path().read_text()) == config
    assert 'not-in-browser-or-file' not in response.text
    assert 'not-in-browser-or-file' not in settings.settings_path().read_text()
    assert ReferenceRuntimeConfig().speech == cloud_selection()


def test_external_tester_cloud_tts_runs_in_worker_not_api():
    import base64
    import io
    import wave
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b'\x01\x00' * 2400)
    audio = output.getvalue()
    config = settings.SpeechSelection(tts_provider='deepgram', tts_model='aura-2',
        tester_voice='aura-2-asteria-en', target_voice='aura-2-orion-en')
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'audio_wav_base64': base64.b64encode(audio).decode()})
    media = ReferenceMediaServices(ReferenceRuntimeConfig(speech=config, pipecat_service_url='http://worker',
        internal_token='worker-test-token', kokoro_base_url=''), client=httpx.Client(transport=httpx.MockTransport(respond)))
    assert media.synthesize('Hello') == audio
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'http://worker/reference-tester/synthesize'
    payload = json.loads(request.content)
    assert payload['speech'] == config.model_dump()
    assert payload['voice'] == config.tester_voice
    assert request.headers['x-cae-reference-token'] == 'worker-test-token'
    assert 'DEEPGRAM_API_KEY' not in request.content.decode()


@pytest.mark.parametrize('update', [
    {'stt_provider': 'unknown'}, {'tts_provider': 'unknown'}, {'target_voice': 'caller-id'},
    {'stt_model': 'https://untrusted'}, {'api_key': 'untrusted'}, {'cartesia_version': 'latest'},
])
def test_invalid_settings_do_not_overwrite_saved_selection(update):
    settings.save_speech_selection(cloud_selection())
    invalid = {**cloud_selection().model_dump(), **update}
    assert TestClient(app).put('/api/execution/speech-settings', json=invalid).status_code == 422
    assert settings.speech_selection() == cloud_selection()


def test_env_provider_defaults_are_independent(monkeypatch):
    monkeypatch.setenv('CAE_STT_PROVIDER', 'cartesia')
    monkeypatch.setenv('CAE_TTS_PROVIDER', 'deepgram')
    config = settings.speech_selection()
    assert config.stt_model == 'ink-2'
    assert config.tts_provider == 'deepgram'
    assert config.tester_voice != config.target_voice


def test_cloud_readiness_uses_worker_selection_without_local_models():
    observed = []
    def respond(request):
        observed.append(request)
        assert request.url.path == '/reference-agent/speech-readiness'
        assert json.loads(request.content) == cloud_selection().model_dump()
        return httpx.Response(200, json={'ready': True, 'issues': []})
    config = ReferenceRuntimeConfig(speech=cloud_selection(), internal_token='worker-token')
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        runtime = ReferenceMediaServices(config, client=client).readiness()
    assert runtime['stt']['provider'] == 'deepgram'
    assert runtime['tts']['provider'] == 'cartesia'
    assert len(observed) == 1


def test_missing_worker_key_fails_closed():
    def respond(request):
        return httpx.Response(200, json={'ready': False, 'issues': ['Set DEEPGRAM_API_KEY.']})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(RuntimeError, match='DEEPGRAM_API_KEY'):
            ReferenceMediaServices(ReferenceRuntimeConfig(speech=cloud_selection()), client=client).readiness()


def test_mixed_provider_requirements_identify_the_missing_stage(monkeypatch):
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'test-key')
    monkeypatch.delenv('CARTESIA_API_KEY', raising=False)
    stages = settings.selection_requirements(cloud_selection(), rtc_asr_url='', kokoro_url='')
    assert stages['stt']['ready'] is True
    assert stages['tts']['ready'] is False
    assert stages['tts']['issues'] == ['Set CARTESIA_API_KEY in the Pipecat server environment.']


def test_saved_local_voice_ids_are_not_replaced_by_environment_defaults():
    selected = settings.SpeechSelection(tester_voice='am_michael', target_voice='af_sky')
    settings.save_speech_selection(selected)
    assert ReferenceRuntimeConfig().speech == selected


@pytest.mark.parametrize('stt_provider', ['deepgram', 'cartesia'])
@pytest.mark.parametrize('tts_provider', ['deepgram', 'cartesia'])
def test_model_aware_voice_launch_preserves_cloud_graphs_and_voices(tmp_path, stt_provider, tts_provider):
    from app.services.reference_model_preflight import _ModelAwareReferencePipecatAgentTransport

    class ConnectedCompletion:
        provider_id = 'openai'

        def status(self):
            return {'status': 'connected', 'provider': self.provider_id}

    selected = settings.SpeechSelection(
        stt_provider=stt_provider, tts_provider=tts_provider,
        stt_model='nova-3' if stt_provider == 'deepgram' else 'ink-2',
        tts_model='aura-2' if tts_provider == 'deepgram' else 'sonic-3.6',
        tester_voice='aura-2-asteria-en' if tts_provider == 'deepgram' else 'caller-id',
        target_voice='aura-2-orion-en' if tts_provider == 'deepgram' else 'agent-id',
    )
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == '/reference-agent/speech-readiness':
            assert json.loads(request.content) == selected.model_dump()
            return httpx.Response(200, json={'ready': True, 'issues': []})
        assert request.url.path == '/reference-agent/readiness'
        return httpx.Response(200, json={
            'ready': False, 'pipeline_runtime': True, 'duplex_route_ready': True,
            'route': '/reference-duplex/run',
        })

    config = ReferenceRuntimeConfig(speech=selected, internal_token='worker-token',
        llm_model='test-model', tester_llm_model='test-model')
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        transport = _ModelAwareReferencePipecatAgentTransport(
            artifact_dir=tmp_path, media=ReferenceMediaServices(config, client=client),
            completion=ConnectedCompletion(), config=config,
        )
    assert requests == ['/reference-agent/speech-readiness', '/reference-agent/readiness']
    for participant, voice in (('tester', selected.tester_voice), ('target', selected.target_voice)):
        processors = transport.graphs[participant]['processors']
        assert [item['name'] for item in processors] == [stt_provider, 'llm', tts_provider]
        assert processors[0]['model'] == selected.stt_model
        assert processors[2]['model'] == selected.tts_model
        assert processors[2]['voice'] == voice


@pytest.mark.parametrize('failure', ['connect', 'timeout', 'non-json', 'list', 'null', 'http', 'not-ready'])
def test_cloud_caller_readiness_failures_are_validation_errors(monkeypatch, failure):
    from app.services import execution_runner
    from app.schemas.execution import ExecutionRunCreateRequest
    settings.save_speech_selection(cloud_selection())
    original_client = httpx.Client

    def respond(request):
        if failure == 'connect':
            raise httpx.ConnectError('worker unreachable', request=request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('worker timed out', request=request)
        if failure == 'non-json':
            return httpx.Response(200, text='<html>proxy error</html>')
        if failure == 'http':
            return httpx.Response(503, text='unavailable')
        return httpx.Response(200, json={'list': [], 'null': None, 'not-ready': {'ready': False}}[failure])

    monkeypatch.setattr(execution_runner.httpx, 'Client',
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(ValueError, match='Selected caller TTS is unavailable; check speech provider readiness'):
        execution_runner._preflight_signalwire_caller_tts_runtime(ExecutionRunCreateRequest())


@pytest.mark.parametrize('executor', ['pipecat_public_daily', 'signalwire_public_webrtc', 'waylo_livekit'])
def test_external_adapters_receive_queued_speech(monkeypatch, tmp_path, executor):
    import asyncio
    from app.services import execution_runner
    from app.schemas.execution import ExecutionRunCreateRequest
    selected = cloud_selection()
    payload = ExecutionRunCreateRequest(mode='pipecat_webrtc', executor_id=executor,
        scenario_ids=['case'], max_exchanges=1)
    payload._speech_selection = selected
    settings.save_speech_selection(settings.SpeechSelection())
    monkeypatch.setattr(execution_runner, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(execution_runner, '_scenario_definition', lambda *_: {'id': 'case', 'user_opener': 'Hello'})
    monkeypatch.setattr(execution_runner, '_selected_waylo_scenario', lambda *_: {'id': 'case', 'caller_steps': ['Hello']})

    class CapturedConfig(Exception):
        pass

    def capture(**kwargs):
        assert kwargs['config'].speech == selected
        raise CapturedConfig

    monkeypatch.setattr(execution_runner, 'run_public_pipecat_call', capture)
    monkeypatch.setattr(execution_runner, 'run_signalwire_holyguacamole_call', capture)

    def capture_media(config):
        assert config.speech == selected
        raise CapturedConfig

    monkeypatch.setattr(execution_runner, 'ReferenceMediaServices', capture_media)
    kwargs = dict(execution_run_id='test', conversation_id='conversation', suite_id='suite',
        scenario_id='case', payload=payload)
    with pytest.raises(CapturedConfig):
        if executor == 'waylo_livekit':
            asyncio.run(execution_runner._execute_waylo(**kwargs, event_observer=None,
                agent_snapshot={'connection': {'auth_type': 'secret_ref'}}))
        elif executor == 'pipecat_public_daily':
            execution_runner._execute_public_pipecat_daily(**kwargs)
        else:
            execution_runner._execute_signalwire_holyguacamole_webrtc(**kwargs)
