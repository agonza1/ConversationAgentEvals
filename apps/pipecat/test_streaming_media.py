from __future__ import annotations

import asyncio
import struct

from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

from streaming_media import (
    StreamingRtcAsrProcessor,
    StreamingWavDecoder,
    _next_tts_chunk,
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


def test_vad_restarts_asr_when_speech_resumes_during_finalization() -> None:
    async def run() -> None:
        processor = StreamingRtcAsrProcessor(
            base_url="http://rtc-asr.test",
            participant="target",
            final_frame_type=type("FinalFrame", (), {}),
        )

        class SpeakingVad:
            async def analyze_audio(self, audio: bytes) -> VADState:
                assert audio
                return VADState.SPEAKING

        processor.vad = SpeakingVad()
        processor.finalizing = True
        processor.previous_state = VADState.QUIET
        restarted = asyncio.Event()

        async def start_utterance(direction: FrameDirection) -> None:
            assert direction == FrameDirection.DOWNSTREAM
            assert not processor.finalizing
            processor.active = True
            processor.pre_roll.clear()
            restarted.set()

        processor._start_utterance = start_utterance
        process_task = asyncio.create_task(
            processor.process_frame(
                InputAudioRawFrame(b"\x01\x00" * 320, 16000, 1),
                FrameDirection.DOWNSTREAM,
            )
        )
        await asyncio.sleep(0)

        assert not process_task.done()
        assert not restarted.is_set()
        assert processor.pre_roll

        processor.finalizing = False
        processor.final_received.set()
        await process_task

        assert restarted.is_set()
        assert processor.active
        assert processor.previous_state == VADState.SPEAKING

    asyncio.run(run())


def test_adaptive_tts_uses_short_first_chunk_then_complete_sentences() -> None:
    chunk, remainder = _next_tts_chunk(
        "I can help with your billing address change today. What is your postal code?",
        first_chunk=True,
    )
    assert chunk == "I can help with your billing address change today."
    assert remainder == "What is your postal code?"

    chunk, remainder = _next_tts_chunk(
        remainder,
        first_chunk=False,
        final=False,
    )
    assert chunk == "What is your postal code?"
    assert remainder == ""


def test_adaptive_tts_caps_unpunctuated_first_chunk() -> None:
    chunk, remainder = _next_tts_chunk(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen",
        first_chunk=True,
    )
    assert chunk.split() == (
        "one two three four five six seven eight nine ten eleven twelve".split()
    )
    assert remainder.strip() == "thirteen fourteen"
