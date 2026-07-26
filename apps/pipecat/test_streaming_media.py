from __future__ import annotations

import struct

from pipecat.audio.vad.vad_analyzer import VADState

from streaming_media import (
    StreamingWavDecoder,
    _should_start_utterance,
    resample_pcm16,
    rtc_asr_stream_url,
)


def _wav_header(*, sample_rate: int = 24000, channels: int = 1, data_size: int = 4) -> bytes:
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, 16)
        + b"data"
        + struct.pack("<I", data_size)
    )


def test_rtc_asr_stream_url_uses_local_stt_v1() -> None:
    assert rtc_asr_stream_url("http://localhost:8080/") == "ws://localhost:8080/v1/stt/stream"
    assert rtc_asr_stream_url("https://asr.example") == "wss://asr.example/v1/stt/stream"
    assert rtc_asr_stream_url("http://localhost:8080/", "/custom/stream") == (
        "ws://localhost:8080/custom/stream"
    )


def test_streaming_wav_decoder_handles_split_header_and_audio() -> None:
    pcm = struct.pack("<2h", 100, -100)
    payload = _wav_header(data_size=len(pcm)) + pcm
    decoder = StreamingWavDecoder()

    assert decoder.feed(payload[:17]) == b""
    assert decoder.feed(payload[17:43]) == b""
    assert decoder.feed(payload[43:]) == pcm
    assert decoder.sample_rate == 24000
    assert decoder.channels == 1


def test_pcm_resampler_preserves_duration() -> None:
    source = struct.pack("<240h", *range(240))
    converted = resample_pcm16(source, 24000, 16000)
    assert len(converted) == 320


def test_vad_recovery_does_not_restart_active_rtc_asr_stream() -> None:
    assert not _should_start_utterance(
        state=VADState.SPEAKING,
        previous_state=VADState.STOPPING,
        active=True,
        finalizing=False,
    )
    assert _should_start_utterance(
        state=VADState.SPEAKING,
        previous_state=VADState.QUIET,
        active=False,
        finalizing=False,
    )
