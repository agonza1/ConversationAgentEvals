"""Buffered provider WAVs must be finalized without repairing broken fixtures."""
from __future__ import annotations

import asyncio
import io
import json
import struct
import wave

import httpx
import pytest

from app.schemas.execution import ExecutionRunCreateRequest
from app.services import execution_runner, waylo_livekit
from app.services.reference_generalist_agent import (
    ReferenceMediaServices,
    ReferenceRuntimeConfig,
    ReferenceRuntimeError,
)
from app.services.synthesized_audio import finalize_tts_wav
from app.services.waylo_audio_conditions import add_noise
from app.services.waylo_livekit import read_wav


PCM = b'\x01\x00\x02\x00\x03\x00\x04\x00' * 100


def _wav(*, pcm=PCM, rate=24000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return output.getvalue()


def _streaming_wav(*, pcm=PCM, rate=24000, channels=1, bits=16,
                   riff_size=0xFFFFFFFF, data_size=0xFFFFFFFF):
    # Mirrors the observed local Kokoro header: RIFF sentinel, fmt(16),
    # LIST(26), then a terminal data sentinel at byte 74. PCM begins at 78.
    metadata = b'INFOISFT' + struct.pack('<I', 14) + b'Lavf61.7.100\x00\x00'
    assert len(metadata) == 26
    alignment = channels * (bits // 8)
    return (
        b'RIFF' + struct.pack('<I', riff_size) + b'WAVE'
        + b'fmt ' + struct.pack('<IHHIIHH', 16, 1, channels, rate,
                                rate * alignment, alignment, bits)
        + b'LIST' + struct.pack('<I', len(metadata)) + metadata
        + b'data' + struct.pack('<I', data_size) + pcm
    )


def _replace_u32(audio, offset, value):
    return audio[:offset] + struct.pack('<I', value) + audio[offset + 4:]


def _replace_u16(audio, offset, value):
    return audio[:offset] + struct.pack('<H', value) + audio[offset + 2:]


def _extensible_wav(*, guid=bytes.fromhex('0100000000001000800000aa00389b71'), valid_bits=16,
                    extension_size=22, extra_extension=b''):
    fmt = (struct.pack('<HHIIHHHHI', 0xFFFE, 1, 24000, 48000, 2, 16,
                       extension_size, valid_bits, 4) + guid + extra_extension)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(PCM)) + PCM
    return b'RIFF' + struct.pack('<I', len(body)) + body


@pytest.mark.parametrize('rate', [16000, 24000, 48000])
def test_complete_provider_wav_is_preserved_byte_for_byte(rate):
    audio = _wav(rate=rate)
    assert finalize_tts_wav(audio) == audio
    assert read_wav(audio) == (rate, PCM)


def test_complete_extensible_pcm16_wav_is_preserved():
    audio = _extensible_wav()
    assert finalize_tts_wav(audio) == audio
    assert read_wav(audio) == (24000, PCM)


def test_extensible_pcm16_with_consistent_longer_extension_is_preserved():
    audio = _extensible_wav(extension_size=24, extra_extension=b'\x00\x00')
    assert finalize_tts_wav(audio) == audio
    assert read_wav(audio) == (24000, PCM)


@pytest.mark.parametrize('audio', [
    _extensible_wav(extension_size=0),
    _extensible_wav(extension_size=1),
    _extensible_wav(extension_size=21),
    _extensible_wav(extension_size=23),
    _extensible_wav(extension_size=24),
    _extensible_wav(extension_size=0xFFFF),
    _extensible_wav(extra_extension=b'\x00\x00'),
])
def test_extensible_extension_length_must_match_available_format_bytes(audio):
    with pytest.raises(ValueError):
        finalize_tts_wav(audio)


@pytest.mark.parametrize('remaining_format_bytes', [18, 20, 22, 38, 39])
def test_truncated_extensible_format_extension_is_not_repaired(remaining_format_bytes):
    original = _extensible_wav()
    # Rebuild the RIFF and fmt sizes honestly around the truncated format,
    # keeping otherwise-valid PCM so only the extension itself is broken.
    fmt = original[20:20 + remaining_format_bytes]
    body = (b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt
            + (b'\x00' if len(fmt) % 2 else b'') + original[60:])
    audio = b'RIFF' + struct.pack('<I', len(body)) + body
    with pytest.raises(ValueError):
        finalize_tts_wav(audio)


@pytest.mark.parametrize('audio', [
    _replace_u16(_wav(), 34, 9),
    _replace_u16(_wav(), 34, 15),
    _replace_u16(_wav(), 32, 1),
    _replace_u16(_wav(), 32, 4),
    _replace_u32(_wav(), 28, 0),
    _replace_u32(_wav(), 28, 48001),
    _extensible_wav(valid_bits=12),
    _extensible_wav(valid_bits=24),
])
def test_fmt_must_describe_exact_mono_pcm16_frames(audio):
    # Python wave rounds 9--15 advertised bits to a two-byte sample width;
    # width alone cannot prove the exact format used by the transport.
    with pytest.raises(ValueError):
        finalize_tts_wav(audio)


def test_ambiguous_duplicate_fmt_chunks_are_rejected():
    standard = _wav()
    body = standard[8:36] + standard[12:]
    duplicate = b'RIFF' + struct.pack('<I', len(body)) + body
    with pytest.raises(ValueError):
        finalize_tts_wav(duplicate)


def test_kokoro_streaming_header_is_finalized_preserving_pcm_and_metadata():
    audio = _streaming_wav()
    fixed = finalize_tts_wav(audio)
    assert len(fixed) == len(audio)
    assert struct.unpack_from('<I', fixed, 4)[0] == len(audio) - 8
    assert struct.unpack_from('<I', fixed, 74)[0] == len(PCM)
    # Header sizes are the only bytes that may change.
    assert fixed[:4] == audio[:4]
    assert fixed[8:74] == audio[8:74]
    assert fixed[78:] == PCM
    assert read_wav(fixed) == (24000, PCM)
    assert finalize_tts_wav(fixed) == fixed


def test_finalized_streaming_audio_remains_compatible_with_noise_condition():
    fixed = finalize_tts_wav(_streaming_wav())
    noisy, measurement = add_noise(fixed, snr_db=20, seed=7)
    rate, pcm = read_wav(noisy)
    assert rate == 24000 and len(pcm) == len(PCM)
    assert measurement['signal_rms_pcm16'] > 0


@pytest.mark.parametrize('audio', [
    b'', b'not wav', b'RIFF\xff\xff\xff\xffWAVE',
    _wav(pcm=b''), _wav()[:-2], _wav()[:-1],
    _wav(channels=2), _wav(width=1), _wav(rate=22050),
    _replace_u32(_wav(), 4, len(_wav()) + 100),
    _replace_u32(_wav(), 40, len(PCM) + 100),
    _streaming_wav(pcm=b''), _streaming_wav(pcm=PCM[:-1]),
    _streaming_wav(channels=2), _streaming_wav(bits=8),
    _streaming_wav(rate=22050),
    _streaming_wav(riff_size=0x7FFFFFFF),
    _streaming_wav(data_size=0x7FFFFFFF),
    _streaming_wav(data_size=len(PCM) + 100),
    _streaming_wav(riff_size=78 + len(PCM) + 100),
    _extensible_wav(guid=bytes.fromhex('0300000000001000800000aa00389b71')),
    _wav()[:20] + b'\x03\x00' + _wav()[22:],
    _replace_u32(_streaming_wav(), 40, 0xFFFFFFFF),  # Unknown LIST length is not PCM.
    b'\x00' * 10_000_001,
])
def test_invalid_or_truncated_provider_wav_is_never_repaired(audio):
    with pytest.raises(ValueError):
        finalize_tts_wav(audio)


def test_truncated_finite_data_chunk_stays_invalid_with_streaming_riff_size():
    audio = _replace_u32(_wav()[:-20], 4, 0xFFFFFFFF)
    with pytest.raises(ValueError):
        finalize_tts_wav(audio)


def test_raw_streaming_header_is_still_rejected_for_controlled_fixture_audio():
    # The transport/fixture boundary must not acquire the provider exception.
    with pytest.raises(waylo_livekit.WayloError, match='complete, nonempty mono'):
        read_wav(_streaming_wav())


def test_reference_media_requests_buffered_audio_and_finalizes_kokoro_response():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.url == 'http://kokoro.test/v1/audio/speech'
        return httpx.Response(200, content=_streaming_wav())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        media = ReferenceMediaServices(ReferenceRuntimeConfig(
            kokoro_base_url='http://kokoro.test'), client=client)
        actual = media.synthesize('Five bags, please.', voice='af_heart')
    assert requests == [{'model': 'kokoro', 'voice': 'af_heart',
                         'input': 'Five bags, please.', 'response_format': 'wav', 'stream': False}]
    assert read_wav(actual) == (24000, PCM)


@pytest.mark.parametrize('audio', [b'not wav', _wav()[:-2], _streaming_wav(pcm=PCM[:-1])])
def test_reference_media_rejects_bad_wav_without_reflecting_provider_body(audio):
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=audio))) as client:
        media = ReferenceMediaServices(ReferenceRuntimeConfig(
            kokoro_base_url='http://kokoro.test'), client=client)
        with pytest.raises(ReferenceRuntimeError) as error:
            media.synthesize('Five bags, please.')
    assert 'WAV' in str(error.value)
    assert 'not wav' not in str(error.value)


@pytest.mark.parametrize('bad_index', [0, 1])
def test_invalid_synthesized_fixed_turn_fails_before_waylo_bootstrap(tmp_path, monkeypatch, bad_index):
    from test_waylo_review_regressions import Client, Peer
    from test_waylo_target import TARGET

    response_audio = [_streaming_wav(), _streaming_wav()]
    response_audio[bad_index] = _wav()[:-2]
    posted = []
    client = Client()

    def respond(request):
        assert json.loads(request.content)['stream'] is False
        index = len(posted)
        posted.append(request)
        return httpx.Response(200, content=response_audio[index])

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    media = ReferenceMediaServices(ReferenceRuntimeConfig(
        kokoro_base_url='http://kokoro.test'), client=http_client)
    monkeypatch.setattr(execution_runner, 'ReferenceMediaServices', lambda config: media)
    monkeypatch.setattr(execution_runner, '_scenario_definition', lambda *args: {
        'id': 'case', 'caller_steps': ['First fixed turn.', 'Second fixed turn.']})
    monkeypatch.setattr('app.services.waylo_target.WayloClient', lambda *args, **kwargs: client)
    monkeypatch.setattr(waylo_livekit, 'LiveKitVoicePeer', Peer)
    payload = ExecutionRunCreateRequest(agent_id='mike', max_exchanges=2)
    with pytest.raises(waylo_livekit.WayloError):
        asyncio.run(execution_runner._execute_waylo(
            execution_run_id='exec-wav', conversation_id='conv-wav', suite_id='suite',
            scenario_id='case', payload=payload, agent_snapshot=TARGET, event_observer=None))
    assert len(posted) == bad_index + 1
    assert client.bootstraps == 0
