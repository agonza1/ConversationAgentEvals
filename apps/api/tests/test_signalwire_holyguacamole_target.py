from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import app.services.signalwire_holyguacamole_target as signalwire_target
from app.services.signalwire_holyguacamole_target import run_signalwire_holyguacamole_call


@pytest.fixture(autouse=True)
def _stub_existing_cae_tester_media(monkeypatch):
    monkeypatch.setenv('KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(
        signalwire_target.ReferenceMediaServices,
        'synthesize',
        lambda _self, _text, *, voice=None: b'cae-kokoro-caller-wav',
    )
    monkeypatch.setattr(
        signalwire_target,
        '_open_live_audio_broadcast',
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        signalwire_target,
        '_close_live_audio_broadcast',
        lambda *_args, **_kwargs: None,
    )


def test_signalwire_target_requires_explicit_public_gate(tmp_path, monkeypatch):
    monkeypatch.delenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, raising=False)
    with pytest.raises(RuntimeError, match='CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE'):
        run_signalwire_holyguacamole_call(
            caller_text='I want a taco.',
            artifact_dir=tmp_path,
            conversation_id='conversation-gated',
            timeout_seconds=60,
        )


def test_signalwire_target_retries_transient_caller_synthesis(tmp_path, monkeypatch):
    repo_root = Path(signalwire_target.__file__).resolve().parents[4]
    result_dir = tmp_path / 'webrtc-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    result_path = result_dir / 'result.json'
    result_path.write_text(json.dumps({
        'status': 'pass',
        'connection': {'call_connected': True, 'remote_stream_seen': True},
        'tester': {'transport_runtime': 'direct-webrtc'},
        'media': {'target_audio_duration_ms': 1000},
        'transcript': {'agent_text': '', 'source': 'remote_audio_capture_untranscribed'},
        'artifacts': {
            'target_audio': str(target_audio.relative_to(repo_root))
            if target_audio.is_relative_to(repo_root)
            else str(target_audio),
            'target_audio_mime': 'audio/webm',
            'caller_audio': str(target_audio),
            'result_json': str(result_path),
        },
    }), encoding='utf-8')

    class Completed:
        stdout = json.dumps({'result_path': str(result_path)})
        stderr = ''
        returncode = 0

    attempts = 0

    def flaky_synthesize(_self, _text, *, voice=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout('transient Kokoro timeout')
        return b'RIFF-retried-caller-wav'

    monkeypatch.setenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, '1')
    monkeypatch.setattr(signalwire_target.ReferenceMediaServices, 'synthesize', flaky_synthesize)
    monkeypatch.setattr(signalwire_target.subprocess, 'run', lambda *args, **kwargs: Completed())

    result = run_signalwire_holyguacamole_call(
        caller_text='I want a taco.',
        artifact_dir=tmp_path / 'api-artifacts',
        conversation_id='conversation-retry',
        timeout_seconds=60,
    )

    assert attempts == 2
    assert result['recording_handle'].bytes_captured == len(b'current-run-signalwire-audio')


def test_signalwire_target_client_invokes_smoke_and_returns_current_run_evidence(
    tmp_path,
    monkeypatch,
):
    repo_root = Path(signalwire_target.__file__).resolve().parents[4]
    result_dir = tmp_path / 'webrtc-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    result_path = result_dir / 'result.json'
    result_payload = {
        'status': 'pass',
        'connection': {'call_connected': True, 'remote_stream_seen': True},
        'tester': {'media_source': 'macos_say_tts'},
        'latency_metrics': {'call_connected_to_remote_track_ms': 1234.0},
        'media': {'target_audio_duration_ms': 5000},
        'transcript': {
            'caller_text': 'I want a taco.',
            'caller_text_verified': True,
            'caller_text_source': 'macos_say_tts',
            'agent_text': 'status: Connected! Ready to take your order.',
            'source': 'signalwire_call_events',
        },
        'artifacts': {
            'target_audio': str(target_audio),
            'target_audio_mime': 'audio/webm',
            'target_audio_sha256': 'sha-not-a-secret',
            'caller_audio': str(target_audio),
            'result_json': str(result_path),
        },
    }
    result_path.write_text(json.dumps(result_payload), encoding='utf-8')
    observed: dict[str, object] = {}
    closed_publishers: list[tuple[str | None, str | None]] = []
    setup_sequence: list[str] = []

    class Completed:
        stdout = json.dumps({'result_path': str(result_path)})
        stderr = ''
        returncode = 0

    def fake_run(command, *, cwd, env, **kwargs):
        observed['command'] = command
        observed['cwd'] = cwd
        observed['env'] = env
        observed['kwargs'] = kwargs
        return Completed()

    monkeypatch.setenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, '1')
    monkeypatch.setattr(signalwire_target.subprocess, 'run', fake_run)
    monkeypatch.setattr(
        signalwire_target.ReferenceMediaServices,
        'synthesize',
        lambda _self, _text, *, voice=None: (
            setup_sequence.append('synthesize') or b'cae-kokoro-caller-wav'
        ),
    )
    monkeypatch.setattr(
        signalwire_target,
        '_open_live_audio_broadcast',
        lambda _runtime, *, execution_run_id, conversation_id: (
            setup_sequence.append('open_broadcast') or 'preopened-publisher'
        ),
    )
    monkeypatch.setattr(
        signalwire_target,
        '_close_live_audio_broadcast',
        lambda _runtime, *, execution_run_id, publisher_id: closed_publishers.append(
            (execution_run_id, publisher_id)
        ),
    )

    result = run_signalwire_holyguacamole_call(
        caller_text='I want a taco.',
        artifact_dir=tmp_path / 'api-artifacts',
        conversation_id='conversation-1',
        execution_run_id='exec-signalwire',
        timeout_seconds=60,
        scenario={'id': 'demo'},
    )

    command = observed['command']
    assert command[:2] == ['node', str(repo_root / 'scripts' / 'signalwire_holyguacamole_smoke.mjs')]
    assert '--caller-text' in command
    assert 'I want a taco.' in command
    assert '--caller-audio' in command
    assert command[command.index('--max-exchanges') + 1] == '1'
    caller_audio_path = Path(command[command.index('--caller-audio') + 1])
    assert caller_audio_path.read_bytes() == b'cae-kokoro-caller-wav'
    assert observed['cwd'] == repo_root
    assert observed['env']['SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC'] == '1'
    assert observed['env']['SIGNALWIRE_HOLYGUACAMOLE_CONVERSATION_ID'] == 'conversation-1'
    assert observed['env']['SIGNALWIRE_HOLYGUACAMOLE_MAX_EXCHANGES'] == '1'
    assert observed['env']['SIGNALWIRE_HOLYGUACAMOLE_LIVE_PUBLISHER_ID'] == 'preopened-publisher'
    assert closed_publishers == [('exec-signalwire', 'preopened-publisher')]
    assert setup_sequence[:2] == ['open_broadcast', 'synthesize']
    assert [turn.text for turn in result['transcription_turns']] == [
        'I want a taco.',
    ]
    assert result['recording_handle'].transport == 'signalwire_webrtc'
    assert result['recording_handle'].mime_type == 'audio/webm'
    assert result['recording_handle'].bytes_captured == len(b'current-run-signalwire-audio')
    assert result['transcription_turns'][0].frame_metadata['caller_text_source'] == 'cae_kokoro_tts'
    assert 'token' not in str(result).lower()


def test_signalwire_target_accepts_only_remote_speech_transcript_sources(tmp_path, monkeypatch):
    result_dir = tmp_path / 'webrtc-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    result_path = result_dir / 'result.json'
    result_path.write_text(json.dumps({
        'status': 'pass',
        'connection': {'call_connected': True, 'remote_stream_seen': True},
        'tester': {'media_source': 'kokoro_tts'},
        'media': {'target_audio_duration_ms': 5000},
        'transcript': {
            'caller_text': 'I want a taco.',
            'caller_text_verified': True,
            'caller_text_source': 'kokoro_tts',
            'agent_text': 'Welcome to Holy Guacamole.',
            'source': 'signalwire_remote_audio_asr',
        },
        'artifacts': {
            'target_audio': str(target_audio),
            'target_audio_mime': 'audio/webm',
            'target_audio_sha256': 'sha-not-a-secret',
            'caller_audio': str(target_audio),
            'result_json': str(result_path),
        },
    }), encoding='utf-8')

    class Completed:
        stdout = json.dumps({'result_path': str(result_path)})
        stderr = ''
        returncode = 0

    monkeypatch.setenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, '1')
    monkeypatch.setattr(signalwire_target.subprocess, 'run', lambda *args, **kwargs: Completed())

    result = run_signalwire_holyguacamole_call(
        caller_text='I want a taco.',
        artifact_dir=tmp_path / 'api-artifacts',
        conversation_id='conversation-1',
        timeout_seconds=60,
    )

    assert [turn.text for turn in result['transcription_turns']] == [
        'I want a taco.',
        'Welcome to Holy Guacamole.',
    ]
    assert result['transcription_turns'][0].frame_metadata['caller_text_source'] == 'cae_kokoro_tts'


def test_signalwire_target_transcribes_captured_response_with_existing_rtc_asr(
    tmp_path,
    monkeypatch,
):
    result_dir = tmp_path / 'webrtc-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    response_audio = result_dir / 'target-response.wav'
    response_audio.write_bytes(b'current-run-response-wav')
    result_path = result_dir / 'result.json'
    result_path.write_text(json.dumps({
        'status': 'pass',
        'connection': {'call_connected': True, 'remote_stream_seen': True},
        'tester': {'media_source': 'kokoro_tts'},
        'media': {'target_audio_duration_ms': 5000},
        'transcript': {
            'caller_text': 'I want a taco.',
            'caller_text_verified': True,
            'caller_text_source': 'kokoro_tts',
            'agent_text': '',
            'source': 'remote_audio_capture_untranscribed',
        },
        'artifacts': {
            'target_audio': str(target_audio),
            'target_audio_mime': 'audio/webm',
            'target_audio_sha256': 'sha-not-a-secret',
            'target_response_audio': str(response_audio),
            'caller_audio': str(target_audio),
            'result_json': str(result_path),
        },
    }), encoding='utf-8')

    class Completed:
        stdout = json.dumps({'result_path': str(result_path)})
        stderr = ''
        returncode = 0

    captured_audio: list[bytes] = []

    def fake_transcribe(_self, wav_bytes):
        captured_audio.append(wav_bytes)
        return 'Welcome to Holy Guacamole. What can I get started for you?'

    monkeypatch.setenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, '1')
    monkeypatch.setenv('RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setattr(signalwire_target.subprocess, 'run', lambda *args, **kwargs: Completed())
    monkeypatch.setattr(signalwire_target.ReferenceMediaServices, 'transcribe', fake_transcribe)

    result = run_signalwire_holyguacamole_call(
        caller_text='I want a taco.',
        artifact_dir=tmp_path / 'api-artifacts',
        conversation_id='conversation-1',
        timeout_seconds=60,
    )

    assert captured_audio == [b'current-run-response-wav']
    assert [(turn.speaker, turn.text) for turn in result['transcription_turns']] == [
        ('Caller', 'I want a taco.'),
        ('Agent', 'Welcome to Holy Guacamole. What can I get started for you?'),
    ]
    assert result['transcript']['source'] == 'rtc-asr.current_run'
    assert json.loads(result_path.read_text(encoding='utf-8'))['transcript']['agent_text_available'] is True


def test_signalwire_target_grounds_caller_text_in_cae_synthesized_audio(
    tmp_path,
    monkeypatch,
):
    result_dir = tmp_path / 'webrtc-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    result_path = result_dir / 'result.json'
    result_path.write_text(json.dumps({
        'status': 'pass',
        'connection': {'call_connected': True, 'remote_stream_seen': True},
        'tester': {'media_source': 'supplied_audio_file'},
        'media': {'target_audio_duration_ms': 5000},
        'transcript': {
            'caller_text': '',
            'caller_text_verified': False,
            'caller_text_source': 'unverified_supplied_audio',
            'agent_text': 'Welcome to Holy Guacamole.',
            'source': 'signalwire_remote_audio_asr',
        },
        'artifacts': {
            'target_audio': str(target_audio),
            'target_audio_mime': 'audio/webm',
            'target_audio_sha256': 'sha-not-a-secret',
            'caller_audio': str(target_audio),
            'result_json': str(result_path),
        },
    }), encoding='utf-8')

    class Completed:
        stdout = json.dumps({'result_path': str(result_path)})
        stderr = ''
        returncode = 0

    monkeypatch.setenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, '1')
    monkeypatch.setattr(signalwire_target.subprocess, 'run', lambda *args, **kwargs: Completed())

    result = run_signalwire_holyguacamole_call(
        caller_text='I want a taco.',
        artifact_dir=tmp_path / 'api-artifacts',
        conversation_id='conversation-1',
        timeout_seconds=60,
    )

    assert [(turn.speaker, turn.text) for turn in result['transcription_turns']] == [
        ('Caller', 'I want a taco.'),
        ('Agent', 'Welcome to Holy Guacamole.'),
    ]


def test_signalwire_smoke_uses_direct_webrtc_and_audible_latency():
    repo_root = Path(signalwire_target.__file__).resolve().parents[4]
    script = (repo_root / 'scripts' / 'signalwire_holyguacamole_smoke.mjs').read_text(
        encoding='utf-8'
    )

    assert "require('@signalwire/js')" in script
    assert "require('@roamhq/wrtc')" in script
    assert 'new wrtc.nonstandard.RTCAudioSource()' in script
    assert 'new wrtc.nonstandard.RTCAudioSink(track)' in script
    assert 'new SignalWire(credentialProvider' in script
    assert 'client.dial(firstCredential.address' in script
    assert "import { chromium } from 'playwright'" not in script
    assert 'chromium.launch' not in script
    assert 'headless: true' not in script
    assert 'normalizeAllowlistedTargetUrl(args.targetUrl)' in script
    assert 'url.href !== expected.href' in script
    assert 'first_outbound_sample_epoch_ms' in script
    assert 'caller_audio_played' in script
    assert 'caller_audio_completed' in script
    assert 'caller_text_verified = false' not in script
    assert 'caller_text_verified' in script
    assert 'unverified_supplied_audio' in script
    assert 'supplied audio file (speech text unverified)' in script
    assert 'remote_audio_after_caller_seen' in script
    assert '/reference-tester/turn' in script
    assert '--max-exchanges must be 1 or 2.' in script
    assert 'POST_CALLER_GRACE_MS = 300' in script
    assert 'PRE_RESPONSE_SILENCE_MS = 700' in script
    assert 'RESPONSE_END_SILENCE_MS = 1800' in script
    assert 'RESPONSE_MIN_CAPTURE_MS = 3500' in script
    assert 'RESPONSE_MAX_CAPTURE_MS = 9000' in script
    assert 'caller_audio_completed_to_remote_audio_ms' in script
    assert 'target_response_audio' in script
    assert 'createPcmWav(capture.chunks' in script
    assert "extension: 'target-audio.wav'" in script
    assert 'const deadline = startedMs + args.timeoutMs' in script
    assert 'CaeLiveAudioPublisher' in script
    assert '/outbound-voice/broadcast/open' in script
    assert '/outbound-voice/broadcast/audio' in script
    assert '/outbound-voice/broadcast/close' in script
    assert "direction: 'tester_to_target'" in script
    assert "direction: 'target_to_tester'" in script
