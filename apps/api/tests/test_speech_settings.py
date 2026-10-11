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
