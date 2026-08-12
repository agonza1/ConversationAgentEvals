from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

import app.services.signalwire_holyguacamole_target as signalwire_target
from app.services.reference_generalist_agent import ReferenceRuntimeConfig
from app.services.signalwire_holyguacamole_target import run_signalwire_holyguacamole_call


def _wav(value: bytes) -> str:
    return base64.b64encode(value).decode('ascii')


class FakeStream:
    def __init__(self, events: list[dict[str, object]], status_code: int = 200):
        self.events = events
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                'error',
                request=httpx.Request('POST', 'http://pipecat.test/signalwire-holyguacamole/duplex'),
                response=httpx.Response(self.status_code, json={'detail': 'Holy Guacamole blocked'}),
            )

    def iter_lines(self):
        for event in self.events:
            yield json.dumps(event)

    def read(self):
        return json.dumps({'detail': 'Holy Guacamole blocked'}).encode('utf-8')


class FakeClient:
    def __init__(self, events: list[dict[str, object]]):
        self.events = events
        self.observed: dict[str, object] = {}
        self.closed = False

    def stream(self, method, url, *, headers, json):
        self.observed = {
            'method': method,
            'url': url,
            'headers': headers,
            'json': json,
        }
        return FakeStream(self.events)

    def close(self):
        self.closed = True


def _complete_event(*, source: str = 'signalwire_direct_remote_audio_untranscribed'):
    return {
        'type': 'complete',
        'result': {
            'status': 'pass',
            'connection': {'call_connected': True, 'remote_audio_track_seen': True},
            'latency_metrics': {
                'tester_speech_end_to_first_target_audio_received_ms': 640.0,
                'total_run_ms': 6000.0,
            },
            'media': {
                'caller_audio_wav_base64': _wav(b'current-run-caller-wav'),
                'target_audio_wav_base64': _wav(b'current-run-signalwire-wav'),
                'target_audio_frames': 20,
            },
            'transcript': {
                'caller_text': 'I want a taco.',
                'caller_text_verified': True,
                'caller_text_source': 'current_run_kokoro',
                'agent_text': 'Welcome to Holy Guacamole.',
                'source': source,
            },
            'provenance': {
                'browser_peer': False,
                'headless_browser': False,
                'guest_token_persisted': False,
            },
        },
    }


def test_signalwire_target_requires_explicit_public_gate(tmp_path, monkeypatch):
    monkeypatch.delenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, raising=False)
    with pytest.raises(RuntimeError, match='CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE'):
        run_signalwire_holyguacamole_call(
            caller_text='I want a taco.',
            artifact_dir=tmp_path,
            conversation_id='conversation-gated',
            timeout_seconds=60,
        )


def test_signalwire_target_client_streams_pipecat_direct_and_persists_current_run_evidence(
    tmp_path,
    monkeypatch,
):
    client = FakeClient([_complete_event()])
    monkeypatch.setenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, '1')

    result = run_signalwire_holyguacamole_call(
        caller_text='I want a taco.',
        artifact_dir=tmp_path / 'api-artifacts',
        conversation_id='conversation-1',
        execution_run_id='exec-signalwire',
        timeout_seconds=60,
        scenario={'id': 'demo'},
        config=ReferenceRuntimeConfig(
            pipecat_service_url='http://pipecat.test',
            internal_token='shared-token',
        ),
        client=client,
    )

    assert client.observed['method'] == 'POST'
    assert client.observed['url'] == 'http://pipecat.test/signalwire-holyguacamole/duplex'
    assert client.observed['headers']['x-cae-reference-token'] == 'shared-token'
    assert client.observed['json']['caller_text'] == 'I want a taco.'
    assert client.observed['json']['session_id'] == 'conversation-1'
    assert [turn.text for turn in result['transcription_turns']] == ['I want a taco.']
    assert result['recording_handle'].transport == 'signalwire_direct_webrtc'
    assert result['recording_handle'].mime_type == 'audio/wav'
    assert result['recording_handle'].bytes_captured == len(b'current-run-signalwire-wav')
    assert Path(result['caller_audio_path']).read_bytes() == b'current-run-caller-wav'
    assert Path(result['target_audio_path']).read_bytes() == b'current-run-signalwire-wav'
    assert 'eyJ' not in str(result)


def test_signalwire_target_accepts_only_remote_speech_transcript_sources(tmp_path, monkeypatch):
    client = FakeClient([_complete_event(source='signalwire_remote_audio_asr')])
    monkeypatch.setenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, '1')

    result = run_signalwire_holyguacamole_call(
        caller_text='I want a taco.',
        artifact_dir=tmp_path / 'api-artifacts',
        conversation_id='conversation-1',
        timeout_seconds=60,
        config=ReferenceRuntimeConfig(
            pipecat_service_url='http://pipecat.test',
            internal_token='shared-token',
        ),
        client=client,
    )

    assert [turn.text for turn in result['transcription_turns']] == [
        'I want a taco.',
        'Welcome to Holy Guacamole.',
    ]
    assert result['transcription_turns'][0].frame_metadata['caller_text_source'] == 'current_run_kokoro'


def test_signalwire_direct_runner_documents_no_browser_token_persistence():
    repo_root = Path(signalwire_target.__file__).resolve().parents[4]
    script = (repo_root / 'apps' / 'pipecat' / 'signalwire_holyguacamole_direct.mjs').read_text(
        encoding='utf-8'
    )

    assert "from '@signalwire/js'" in script
    assert "from '@roamhq/wrtc'" in script
    assert 'RTCAudioSource' in script
    assert 'RTCAudioSink' in script
    assert 'remote_audio_after_caller_seen' in script
    assert 'captured no audible post-caller remote response' in script
    assert 'chromium' not in script.lower()
    assert 'playwright' not in script.lower()
    assert 'guest_token_persisted: false' in script
    assert 'token_bootstrap_endpoint' in script


def test_local_setup_installs_pipecat_node_dependencies():
    repo_root = Path(signalwire_target.__file__).resolve().parents[4]
    root_package_path = repo_root / 'package.json'
    if not root_package_path.exists():
        pytest.skip('Root setup manifest is not copied into the API Docker test image.')
    root_package = json.loads(root_package_path.read_text(encoding='utf-8'))

    assert root_package['scripts']['setup:pipecat-node'] == 'npm install --prefix apps/pipecat'
    assert 'npm run setup:pipecat-node' in root_package['scripts']['setup']
    assert 'npm run setup:pipecat-node' in root_package['scripts']['dev:pipecat']
