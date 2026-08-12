from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.services.signalwire_holyguacamole_target as signalwire_target
from app.services.signalwire_holyguacamole_target import run_signalwire_holyguacamole_call


def test_signalwire_target_requires_explicit_public_gate(tmp_path, monkeypatch):
    monkeypatch.delenv(signalwire_target.SIGNALWIRE_PUBLIC_GATE_ENV, raising=False)
    with pytest.raises(RuntimeError, match='CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE'):
        run_signalwire_holyguacamole_call(
            caller_text='I want a taco.',
            artifact_dir=tmp_path,
            conversation_id='conversation-gated',
            timeout_seconds=60,
        )


def test_signalwire_target_client_invokes_smoke_and_returns_current_run_evidence(
    tmp_path,
    monkeypatch,
):
    repo_root = Path(signalwire_target.__file__).resolve().parents[4]
    result_dir = tmp_path / 'browser-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    result_path = result_dir / 'result.json'
    result_payload = {
        'status': 'pass',
        'connection': {'ui_connected': True, 'remote_stream_seen': True},
        'tester': {'headless_browser': True, 'media_source': 'macos_say_tts'},
        'latency_metrics': {'connect_click_to_remote_audio_ms': 1234.0},
        'media': {'target_audio_duration_ms': 5000},
        'transcript': {
            'caller_text': 'I want a taco.',
            'caller_text_verified': True,
            'caller_text_source': 'macos_say_tts',
            'agent_text': 'status: Connected! Ready to take your order.',
            'source': 'holy_guacamole_browser_status_order_events',
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
    assert observed['cwd'] == repo_root
    assert observed['env']['SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC'] == '1'
    assert observed['env']['SIGNALWIRE_HOLYGUACAMOLE_CONVERSATION_ID'] == 'conversation-1'
    assert [turn.text for turn in result['transcription_turns']] == [
        'I want a taco.',
    ]
    assert result['recording_handle'].transport == 'signalwire_browser_webrtc'
    assert result['recording_handle'].mime_type == 'audio/webm'
    assert result['recording_handle'].bytes_captured == len(b'current-run-signalwire-audio')
    assert 'token' not in str(result).lower()


def test_signalwire_target_accepts_only_remote_speech_transcript_sources(tmp_path, monkeypatch):
    result_dir = tmp_path / 'browser-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    result_path = result_dir / 'result.json'
    result_path.write_text(json.dumps({
        'status': 'pass',
        'connection': {'ui_connected': True, 'remote_stream_seen': True},
        'tester': {'headless_browser': True, 'media_source': 'kokoro_tts'},
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
    assert result['transcription_turns'][0].frame_metadata['caller_text_source'] == 'kokoro_tts'


def test_signalwire_target_does_not_assert_unverified_supplied_caller_audio(
    tmp_path,
    monkeypatch,
):
    result_dir = tmp_path / 'browser-result'
    result_dir.mkdir()
    target_audio = result_dir / 'target-audio.webm'
    target_audio.write_bytes(b'current-run-signalwire-audio')
    result_path = result_dir / 'result.json'
    result_path.write_text(json.dumps({
        'status': 'pass',
        'connection': {'ui_connected': True, 'remote_stream_seen': True},
        'tester': {'headless_browser': True, 'media_source': 'supplied_audio_file'},
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
        ('Agent', 'Welcome to Holy Guacamole.'),
    ]


def test_signalwire_browser_smoke_plays_caller_once_and_uses_audible_latency():
    repo_root = Path(signalwire_target.__file__).resolve().parents[4]
    script = (repo_root / 'scripts' / 'signalwire_holyguacamole_smoke.mjs').read_text(
        encoding='utf-8'
    )

    assert 'source.loop = false' in script
    assert 'source.stop(startAt + buffer.duration)' in script
    assert 'normalizeAllowlistedTargetUrl(args.targetUrl)' in script
    assert 'url.href !== expected.href' in script
    assert 'firstAudibleAudioEpochMs' in script
    assert 'first_outbound_sample_epoch_ms' in script
    assert 'caller_audio_played' in script
    assert 'caller_audio_completed' in script
    assert 'caller_text_verified = false' not in script
    assert 'caller_text_verified' in script
    assert 'unverified_supplied_audio' in script
    assert 'supplied audio file (speech text unverified)' in script
    assert 'remote_audio_after_caller_seen' in script
    assert 'firstAudibleAudioAfterCallerEpochMs' in script
    assert 'POST_CALLER_REMOTE_AUDIO_GRACE_MS = 500' in script
    assert 'post_caller_remote_audible_audio_not_captured' in script
    assert 'caller_audio_not_played' in script
    assert 'connect_click_to_first_audible_audio_ms' in script
    assert 'caller_audio_completed_to_remote_audio_ms' in script
    assert 'connect_click_to_remote_audio_ms = clickMs ? remoteAudioMs - clickMs : null' not in script
