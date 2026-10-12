from __future__ import annotations

import base64
import io
import wave
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import execution_run_store
from app.services.execution_vcon import build_ietf_execution_vcon, validate_ietf_vcon
from app.services.vcon_evidence import decode_evidence, intake_vcon
from app.services.vcon_interop import is_vcon_sha512, vcon_content_hash
from app.services.vcon_media import MediaExportError, export_conversation_media


def wav(samples: int = 5000) -> bytes:
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\x01\x00' * samples)
    return data.getvalue()


def document() -> dict:
    return build_ietf_execution_vcon(conversation_id='conv', execution_run_id='exec', suite_id='s', scenario_id='test',
        scenario_title='Test', mode='waylo_livekit', turns=[{'speaker': 'Caller', 'text': 'What phones do you sell?'}],
        recording=None, created_at='2026-10-12T12:00:00Z', updated_at='2026-10-12T12:01:00Z')


def inline(audio: bytes) -> dict:
    return {'type': 'recording', 'parties': [1], 'encoding': 'base64url', 'mediatype': 'audio/wav',
            'body': base64.urlsafe_b64encode(audio).decode().rstrip('='), 'content_hash': vcon_content_hash(audio)}


def test_new_hash_is_standard_legacy_intake_is_deliberate_and_reexport_is_strict():
    exported = document()
    audio = wav()
    media = inline(audio)
    exported['dialog'].append(media)
    assert is_vcon_sha512(media['content_hash'], allow_legacy=False)
    media['content_hash'] = media['content_hash'][7:]
    assert intake_vcon({'vcon': exported})[1]['profile']
    assert not validate_ietf_vcon(exported)['valid']
    normalized = export_conversation_media(exported, {}, Path('/tmp'), include_audio=True)
    assert normalized['dialog'][-1]['content_hash'] == vcon_content_hash(audio)
    assert validate_ietf_vcon(normalized)['valid']
    assert exported['dialog'][-1]['content_hash'] == media['content_hash']
    normalized['dialog'][-1]['body'] = base64.urlsafe_b64encode(b'tampered').decode().rstrip('=')
    with pytest.raises(ValueError, match='does not match'):
        intake_vcon({'vcon': normalized})


def test_attachment_dialog_is_required_for_export_but_old_cae_intake_remains_readable():
    exported = document()
    del exported['attachments'][0]['dialog']
    assert decode_evidence(exported)
    assert not validate_ietf_vcon(exported)['valid']
    result = export_conversation_media(exported, {}, Path('/tmp'), include_audio=False)
    assert result['attachments'][0]['dialog'] == 0
    assert validate_ietf_vcon(result)['valid']


def test_old_waylo_export_includes_all_five_retained_turns_above_old_600kb_budget(monkeypatch, tmp_path):
    root = tmp_path / 'exec'
    directory = root / 'audio' / 'conv'
    directory.mkdir(parents=True)
    files = ['agent-greeting-received.wav', 'caller-1-sent.wav', 'agent-1-received.wav',
             'caller-2-sent.wav', 'agent-2-received.wav']
    audio = wav(80000)
    for filename in files:
        (directory / filename).write_bytes(audio)
    # Other private/aggregate files are never swept into portable evidence.
    (directory / 'private.wav').write_bytes(b'private')
    (directory / 'agent-received.wav').write_bytes(audio)
    exported = document()
    greeting = inline(audio)
    greeting['content_hash'] = greeting['content_hash'][7:]
    exported['dialog'].append(greeting)
    conversation = {'conversation_id': 'conv', 'audio_session': {'transport': 'waylo_livekit'},
                    'ietf_vcon_export': exported}
    original = deepcopy(conversation)
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(execution_run_store, 'get_execution_run', lambda _: {'user_id': 'owner'})
    monkeypatch.setattr(execution_run_store, 'get_conversation', lambda *_: conversation)
    client = TestClient(app)
    url = '/api/execution/runs/exec/conversations/conv/vcon'
    assert client.get(url, params={'user_id': 'other', 'include_audio': True}).status_code == 404
    default = client.get(url, params={'user_id': 'owner'})
    assert default.status_code == 200, default.text
    assert not any(d.get('body') for d in default.json()['dialog'] if d['type'] == 'recording')
    report = default.json()['analysis'][1]['body']['recording']
    assert report['captured_tracks'] == report['omitted_tracks'] == 5
    assert report['local_audio_bytes'] > 600_000
    assert {t['reason'] for t in report['omissions']} == {'audio_opt_in_required'}
    response = client.get(url, params={'user_id': 'owner', 'include_audio': True})
    assert response.status_code == 200, response.text[:200]
    media = [d for d in response.json()['dialog'] if d['type'] == 'recording' and d.get('body')]
    assert len(media) == 5
    assert [d['parties'] for d in media] == [[1], [0], [1], [0], [1]]
    assert all(d['duration'] == pytest.approx(80000 / 24000) for d in media)
    assert all(is_vcon_sha512(d['content_hash'], allow_legacy=False) for d in media)
    assert all('start' not in d for d in media)  # legacy clock anchor was not recorded
    report = response.json()['analysis'][1]['body']['recording']
    assert report['included_tracks'] == 5 and report['omitted_tracks'] == 0
    assert report['full_media_coverage'] == 'all_retained_local_tracks_included'
    assert report['media_scope'] == 'captured_tracks_only'
    assert str(tmp_path) not in response.text and 'private.wav' not in response.text
    assert response.headers['cache-control'] == 'no-store, private'
    assert conversation == original
    assert intake_vcon({'vcon': response.json()})[1]['profile']
    assert validate_ietf_vcon(response.json())['valid']


def test_continuous_duplex_is_exported_once_with_explicit_known_clock(tmp_path):
    root = tmp_path / 'exec'
    root.mkdir()
    audio = root / 'mix.wav'
    audio.write_bytes(wav())
    conversation = {'recording': {'uri': str(audio), 'duration_ms': 5000 / 24,
        'metadata': {'scope': 'continuous_duplex', 'start_ms': 20,
                     'clock_origin': 'cae_monotonic_since_transport_creation',
                     'clock_origin_utc': '2026-10-12T12:00:00Z',
                     'audio_tracks': [{'uri': str(root / 'should-not-be-read.wav')}]} }}
    result = export_conversation_media(document(), conversation, root, include_audio=True)
    media = [d for d in result['dialog'] if d['type'] == 'recording']
    assert len(media) == 1 and media[0]['parties'] == [0, 1]
    assert media[0]['start'] == '2026-10-12T12:00:00.020000+00:00'
    assert media[0]['duration'] == pytest.approx(5000 / 24000)
    assert result['analysis'][1]['body']['recording']['media_scope'] == 'continuous_duplex'


def test_unknown_clock_is_not_converted_and_media_paths_are_owner_scoped(tmp_path):
    root = tmp_path / 'exec'
    root.mkdir()
    audio = root / 'turn.wav'
    audio.write_bytes(wav())
    track = {'uri': str(audio), 'parties': [0], 'start_ms': 1000,
             'clock_origin_utc': '2026-10-12T12:00:00Z', 'clock_origin': 'provider_worker'}
    result = export_conversation_media(document(), {'local_audio_tracks': [track]}, root, include_audio=True)
    assert 'start' not in result['dialog'][-1]
    track['uri'] = str(tmp_path / 'secret.wav')
    (tmp_path / 'secret.wav').write_bytes(b'secret')
    with pytest.raises(MediaExportError, match='run-local'):
        export_conversation_media(document(), {'local_audio_tracks': [track]}, root, include_audio=True)


def test_total_media_budget_is_atomic_and_metadata_export_reports_every_omission(monkeypatch, tmp_path):
    from app.services import vcon_media
    root = tmp_path / 'exec'
    root.mkdir()
    tracks = []
    for index in range(3):
        audio = root / f'turn-{index}.wav'
        audio.write_bytes(wav())
        tracks.append({'uri': str(audio), 'parties': [0], 'track_id': f'turn-{index}'})
    monkeypatch.setattr(vcon_media, 'MAX_INLINE_AUDIO_BYTES', 25_000)
    original = document()
    with pytest.raises(MediaExportError, match='No tracks were truncated') as failure:
        export_conversation_media(original, {'local_audio_tracks': tracks}, root, include_audio=True)
    assert failure.value.status_code == 413
    assert len(original['dialog']) == 1
    default = export_conversation_media(original, {'local_audio_tracks': tracks}, root, include_audio=False)
    manifest = default['analysis'][1]['body']['recording']
    assert manifest['omitted_tracks'] == 3 and manifest['included_tracks'] == 0


def test_metadata_export_survives_missing_or_empty_audio_and_reports_unknown(tmp_path):
    root = tmp_path / 'exec'
    root.mkdir()
    empty = root / 'empty.wav'
    empty.write_bytes(b'')
    tracks = [{'uri': str(root / 'missing.wav'), 'track_id': 'missing'},
              {'uri': str(empty), 'track_id': 'empty'}]
    result = export_conversation_media(document(), {'local_audio_tracks': tracks}, root, include_audio=False)
    manifest = result['analysis'][1]['body']['recording']
    assert manifest['status'] == 'media_unavailable' and manifest['capture_completeness'] == 'unknown'
    assert manifest['omitted_tracks'] == 2 and manifest['included_tracks'] == 0
    assert {i['reason'] for i in manifest['omissions']} == {'local_media_unavailable', 'local_media_empty'}
    with pytest.raises(MediaExportError):
        export_conversation_media(document(), {'local_audio_tracks': tracks}, root, include_audio=True)


def test_inline_only_metadata_omission_reports_exact_decoded_size(tmp_path):
    source = document()
    audio = wav()
    source['dialog'].append(inline(audio))
    result = export_conversation_media(source, {}, tmp_path, include_audio=False)
    manifest = result['analysis'][1]['body']['recording']
    assert manifest['omissions'] == [{'track_id': 'dialog-1', 'bytes': len(audio), 'parties': [1], 'reason': 'audio_opt_in_required'}]


def test_legacy_track_symlink_does_not_read_private_wav_header(monkeypatch, tmp_path):
    root = tmp_path / 'exec'
    directory = root / 'audio' / 'conv'
    directory.mkdir(parents=True)
    private = tmp_path / 'private.wav'
    private.write_bytes(wav())
    (directory / 'caller-1-sent.wav').symlink_to(private)
    def forbidden_header_read(*args, **kwargs):
        pytest.fail('Private WAV metadata must not be dereferenced')
    monkeypatch.setattr(wave, 'open', forbidden_header_read)
    result = export_conversation_media(document(), {'conversation_id': 'conv', 'audio_session': {'transport': 'waylo_livekit'}},
                                       root, include_audio=False)
    assert result['analysis'][1]['body']['recording']['status'] == 'media_unavailable'


def test_empty_capture_placeholder_does_not_claim_failure_or_spoken_text():
    exported = build_ietf_execution_vcon(conversation_id='empty', execution_run_id='exec', suite_id='s', scenario_id='s',
        scenario_title=None, mode='waylo_livekit', turns=[], recording=None,
        created_at='2026-10-12T12:00:00Z', updated_at='2026-10-12T12:00:00Z')
    assert exported['dialog'] == [{'type': 'recording', 'parties': [0, 1]}]
    assert exported['analysis'][1]['body']['recording']['status'] == 'not_captured'
    assert validate_ietf_vcon(exported)['valid']
    assert intake_vcon({'vcon': exported})[0].get('transcript', '') == ''


def test_native_profile_limits_are_independent_of_optional_audio_size():
    from app.services.waylo_target import project_capture, WayloError
    capture = {'target_id': 'steven', 'origin': 'cae_ai_tester',
        'session': {'id': 'session', 'createdAt': '2026-10-12T12:00:00Z', 'endedAt': '2026-10-12T12:01:00Z'},
        'transcripts': [{'seq': 1, 'role': 'user', 'text': 'What phones do you sell?'}],
        'events': [], 'items': [], 'unavailable': {}}
    # >2 MB inline media is allowed at explicit export, independent of profiles.
    media = inline(wav(1_100_000))
    result = project_capture(capture, media_dialogs=[media])
    assert result['vcon']['analysis'][1]['body']['recording']['included_tracks'] == 1
    native = result['vcon']['attachments'][-1]
    assert native['dialog'] == 0 and validate_ietf_vcon(result['vcon'])['valid']
    capture['padding'] = 'x' * 2_000_001
    with pytest.raises(WayloError, match='export limit'):
        project_capture(capture)


def test_saved_media_capture_status_survives_without_inline_audio():
    from app.services.waylo_target import project_capture
    capture = {'target_id': 'steven', 'origin': 'cae_ai_tester',
        'session': {'id': 'session', 'createdAt': '2026-10-12T12:00:00Z'},
        'transcripts': [], 'events': [], 'items': [], 'unavailable': {}}
    media_capture = {'status': 'retained_locally', 'captured_tracks': 1, 'included_tracks': 0,
                     'omitted_tracks': 1, 'media_scope': 'continuous_duplex', 'reason': 'audio_opt_in_required'}
    result = project_capture(capture, media_capture=media_capture)
    assert result['vcon']['analysis'][1]['body']['recording'] == media_capture
    assert validate_ietf_vcon(result['vcon'])['valid']


def test_native_transcript_timing_uses_provider_timestamps_and_seconds_only():
    from app.services.waylo_target import project_capture
    capture = {'target_id': 'steven', 'origin': 'cae_ai_tester',
        'session': {'id': 'session', 'createdAt': '2026-10-12T12:00:00Z'},
        'transcripts': [{'seq': 1, 'role': 'user', 'text': 'What phones?',
                         'startedAt': '2026-10-12T12:00:01Z', 'endedAt': '2026-10-12T12:00:02.25Z'},
                        {'seq': 2, 'role': 'agent', 'text': 'Several options.', 'startedAt': 'unknown'}],
        'events': [], 'items': [], 'unavailable': {}}
    vcon = project_capture(capture)['vcon']
    assert vcon['dialog'][0]['start'] == '2026-10-12T12:00:01Z'
    assert vcon['dialog'][0]['duration'] == 1.25
    assert 'start' not in vcon['dialog'][1] and 'duration' not in vcon['dialog'][1]
    assert vcon['analysis'][0]['body']['turns'][0]['timing']['clock_origin'] == 'waylo_provider_utc'
