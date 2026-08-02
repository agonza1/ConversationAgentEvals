from __future__ import annotations

import asyncio
import struct

import pytest
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import EndFrame, Frame, InputAudioRawFrame, OutputAudioRawFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from streaming_media import (
    StreamingMediaBridge,
    StreamingRtcAsrProcessor,
    StreamingWavDecoder,
    _next_tts_chunk,
    _should_start_utterance,
    resample_pcm16,
    rtc_asr_stream_url,
)


class TurnEndFrame(Frame):
    pass


class FinalFrame(TranscriptionFrame):
    pass


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


def test_media_bridge_surfaces_worker_failure_at_turn_boundary() -> None:
    async def run() -> None:
        async def disconnected_listener(
            audio: bytes,
            sample_rate: int,
            channels: int,
        ) -> None:
            assert audio and sample_rate == 24000 and channels == 1
            raise RuntimeError("listener disconnected")

        bridge = StreamingMediaBridge(
            participant="target",
            audio_callback=disconnected_listener,
            end_type=TurnEndFrame,
        )
        await bridge.process_frame(
            OutputAudioRawFrame(b"\x01\x00" * 480, 24000, 1),
            FrameDirection.DOWNSTREAM,
        )

        with pytest.raises(RuntimeError, match="listener disconnected"):
            await asyncio.wait_for(
                bridge.process_frame(TurnEndFrame(), FrameDirection.DOWNSTREAM),
                timeout=1,
            )
        await bridge.cleanup()

    asyncio.run(run())


def test_rtc_asr_surfaces_protocol_error_at_custom_turn_boundary() -> None:
    async def run() -> None:
        processor = StreamingRtcAsrProcessor(
            base_url="http://rtc-asr.test",
            participant="target",
            final_frame_type=type("FinalFrame", (), {}),
            end_type=TurnEndFrame,
        )
        processor.protocol_error = RuntimeError("rtc-asr websocket failed")

        await processor.process_frame(
            InputAudioRawFrame(b"\x01\x00" * 320, 16000, 1),
            FrameDirection.DOWNSTREAM,
        )
        with pytest.raises(RuntimeError, match="rtc-asr websocket failed"):
            await processor.process_frame(TurnEndFrame(), FrameDirection.DOWNSTREAM)

    asyncio.run(run())


@pytest.mark.parametrize(('active', 'finalizing'), [(True, False), (False, True)])
def test_rtc_asr_waits_for_final_transcript_before_custom_turn_boundary(
    active: bool,
    finalizing: bool,
) -> None:
    async def run() -> None:
        processor = StreamingRtcAsrProcessor(
            base_url="http://rtc-asr.test",
            participant="tester",
            final_frame_type=FinalFrame,
            end_type=TurnEndFrame,
        )
        processor.active = active
        processor.finalizing = finalizing
        processor.previous_state = VADState.SPEAKING
        processor.pre_roll.append(b"old turn")
        events: list[str] = []

        async def finalize(direction: FrameDirection, *, wait_for_final: bool) -> None:
            assert direction == FrameDirection.DOWNSTREAM
            assert wait_for_final
            events.append("finalized")
            processor.active = False
            processor.finalizing = False
            processor.transcript = "final receipt"

        async def wait_for_final() -> None:
            events.append("waited")
            processor.finalizing = False
            processor.transcript = "final receipt"

        async def push_frame(frame: Frame, direction: FrameDirection) -> None:
            assert direction == FrameDirection.DOWNSTREAM
            events.append(type(frame).__name__)

        processor._finalize = finalize
        processor._wait_for_final = wait_for_final
        processor.push_frame = push_frame

        await processor.process_frame(TurnEndFrame(), FrameDirection.DOWNSTREAM)

        assert events == (["finalized"] if active else ["waited"]) + [
            "FinalFrame",
            "TurnEndFrame",
        ]
        assert processor.transcript == "final receipt"
        assert not processor.pre_roll
        assert processor.previous_state == VADState.QUIET

    asyncio.run(run())


def test_rtc_asr_emits_one_aggregated_transcript_per_media_turn() -> None:
    async def run() -> None:
        observed_frames: list[Frame] = []
        observed_events: list[dict[str, object]] = []

        async def event_callback(event: dict[str, object]) -> None:
            observed_events.append(event)

        processor = StreamingRtcAsrProcessor(
            base_url="http://rtc-asr.test",
            participant="target",
            final_frame_type=FinalFrame,
            end_type=TurnEndFrame,
            event_callback=event_callback,
        )
        processor.turn_open = True
        processor.final_segments = ["first clause", "second clause"]
        processor.transcript = "first clause second clause"
        processor.final_result = {"revision": 2, "audio_received_ms": 1200}

        async def push_frame(frame: Frame, direction: FrameDirection) -> None:
            assert direction == FrameDirection.DOWNSTREAM
            observed_frames.append(frame)

        processor.push_frame = push_frame

        await processor.process_frame(TurnEndFrame(), FrameDirection.DOWNSTREAM)

        finals = [frame for frame in observed_frames if isinstance(frame, FinalFrame)]
        assert len(finals) == 1
        assert finals[0].text == "first clause second clause"
        assert isinstance(observed_frames[-1], TurnEndFrame)
        assert observed_events == [
            {
                "type": "transcript",
                "participant": "target",
                "text": "first clause second clause",
                "is_final": True,
                "speech_final": True,
                "revision": 2,
                "audio_received_ms": 1200,
                "audio_transcribed_ms": None,
            }
        ]

    asyncio.run(run())


def test_rtc_asr_preserves_repeated_text_from_distinct_streams() -> None:
    processor = StreamingRtcAsrProcessor(
        base_url="http://rtc-asr.test",
        participant="target",
        final_frame_type=FinalFrame,
        end_type=TurnEndFrame,
    )

    first = {
        "text": "yes",
        "is_final": True,
        "revision": 2,
        "metadata": {"client_stream_id": "utterance-1"},
    }
    second = {
        "text": "yes",
        "is_final": True,
        "revision": 2,
        "metadata": {"client_stream_id": "utterance-2"},
    }

    assert processor._record_final_segment("yes", first)
    assert not processor._record_final_segment("yes", first)
    assert processor._record_final_segment("yes", second)
    assert processor.final_segments == ["yes", "yes"]
    assert processor.transcript == "yes yes"


def test_rtc_asr_without_media_turn_boundaries_does_not_reemit_final_on_end() -> None:
    async def run() -> None:
        observed_frames: list[Frame] = []
        processor = StreamingRtcAsrProcessor(
            base_url="http://rtc-asr.test",
            participant="target",
            final_frame_type=FinalFrame,
        )
        processor.turn_open = True
        processor.transcript = "already emitted"
        processor.final_result = {"revision": 1}

        async def push_frame(frame: Frame, direction: FrameDirection) -> None:
            assert direction == FrameDirection.DOWNSTREAM
            observed_frames.append(frame)

        processor.push_frame = push_frame
        async def close() -> None:
            return None

        processor._close = close

        await processor.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)

        assert not any(isinstance(frame, FinalFrame) for frame in observed_frames)
        assert len(observed_frames) == 1
        assert isinstance(observed_frames[0], EndFrame)

    asyncio.run(run())


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


def test_adaptive_tts_discards_punctuation_only_final_remainder() -> None:
    assert _next_tts_chunk("?", first_chunk=False, final=True) == ("", "")
