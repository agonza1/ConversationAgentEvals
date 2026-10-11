from __future__ import annotations

import asyncio
import json
import struct

import pytest
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import EndFrame, Frame, InputAudioRawFrame, LLMTextFrame, OutputAudioRawFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from streaming_media import (
    StreamingMediaBridge,
    StreamingKokoroProcessor,
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


@pytest.mark.parametrize(('settings', 'expected'), [
    ({}, (True, 100, 2.0)),
    ({'RTC_ASR_INTERIM_RESULTS': 'false', 'RTC_ASR_PARTIAL_INTERVAL_MS': '1000', 'RTC_ASR_PARTIAL_WINDOW_SECONDS': '5'}, (False, 1000, 5.0)),
    ({'RTC_ASR_PARTIAL_INTERVAL_MS': '100', 'RTC_ASR_PARTIAL_WINDOW_SECONDS': '0.5'}, (True, 100, 0.5)),
    ({'RTC_ASR_PARTIAL_INTERVAL_MS': '5000', 'RTC_ASR_PARTIAL_WINDOW_SECONDS': '20'}, (True, 5000, 20.0)),
])
def test_rtc_asr_start_message_uses_validated_deployment_settings(
    monkeypatch: pytest.MonkeyPatch, settings: dict[str, str], expected: tuple[bool, int, float],
) -> None:
    for name in ('RTC_ASR_INTERIM_RESULTS', 'RTC_ASR_PARTIAL_INTERVAL_MS', 'RTC_ASR_PARTIAL_WINDOW_SECONDS', 'RTC_ASR_FINAL_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    async def run() -> None:
        processor = StreamingRtcAsrProcessor(
            base_url='http://rtc-asr.test', participant='target', final_frame_type=FinalFrame,
        )
        messages: list[dict] = []

        class ReadyWebSocket:
            async def send(self, raw: str) -> None:
                messages.append(json.loads(raw))
                processor.ready.set()

        processor.websocket = ReadyWebSocket()

        async def push_frame(frame: Frame, direction: FrameDirection) -> None:
            assert direction == FrameDirection.DOWNSTREAM

        processor.push_frame = push_frame
        await processor._start_utterance(FrameDirection.DOWNSTREAM)
        assert len(messages) == 1
        start = messages[0]
        assert start['type'] == 'start'
        assert start['version'] == 'local-stt.v1'
        assert (start['interim_results'], start['partial_interval_ms'], start['partial_window_seconds']) == expected
        assert type(start['partial_interval_ms']) is int
        assert start['max_buffer_seconds'] == 20.0
        assert start['audio']['format'] == 'pcm_s16le'
        await processor.vad.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize(('name', 'value'), [
    ('RTC_ASR_INTERIM_RESULTS', '1'), ('RTC_ASR_INTERIM_RESULTS', ''),
    ('RTC_ASR_INTERIM_RESULTS', 'yes'),
    ('RTC_ASR_PARTIAL_INTERVAL_MS', '99'), ('RTC_ASR_PARTIAL_INTERVAL_MS', '5001'),
    ('RTC_ASR_PARTIAL_INTERVAL_MS', '100.0'), ('RTC_ASR_PARTIAL_INTERVAL_MS', ''),
    ('RTC_ASR_PARTIAL_WINDOW_SECONDS', '0.49'), ('RTC_ASR_PARTIAL_WINDOW_SECONDS', '20.1'),
    ('RTC_ASR_PARTIAL_WINDOW_SECONDS', 'nan'), ('RTC_ASR_PARTIAL_WINDOW_SECONDS', 'inf'),
    ('RTC_ASR_PARTIAL_WINDOW_SECONDS', ''),
    ('RTC_ASR_FINAL_TIMEOUT_SECONDS', '4.99'), ('RTC_ASR_FINAL_TIMEOUT_SECONDS', '120.1'),
    ('RTC_ASR_FINAL_TIMEOUT_SECONDS', 'nan'), ('RTC_ASR_FINAL_TIMEOUT_SECONDS', 'inf'),
    ('RTC_ASR_FINAL_TIMEOUT_SECONDS', ''), ('RTC_ASR_FINAL_TIMEOUT_SECONDS', 'slow'),
])
def test_rtc_asr_rejects_invalid_deployment_settings(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    for setting in ('RTC_ASR_INTERIM_RESULTS', 'RTC_ASR_PARTIAL_INTERVAL_MS', 'RTC_ASR_PARTIAL_WINDOW_SECONDS', 'RTC_ASR_FINAL_TIMEOUT_SECONDS'):
        monkeypatch.delenv(setting, raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        StreamingRtcAsrProcessor(
            base_url='http://rtc-asr.test', participant='target', final_frame_type=FinalFrame,
        )


@pytest.mark.parametrize(('configured', 'expected'), [(None, 20.0), ('60', 60.0), ('5', 5.0), ('120', 120.0), ('60.5', 60.5)])
@pytest.mark.parametrize('times_out', [False, True])
def test_rtc_asr_final_wait_uses_configured_deadline(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: float, times_out: bool,
) -> None:
    monkeypatch.delenv('RTC_ASR_FINAL_TIMEOUT_SECONDS', raising=False)
    if configured is not None:
        monkeypatch.setenv('RTC_ASR_FINAL_TIMEOUT_SECONDS', configured)

    async def run() -> None:
        processor = StreamingRtcAsrProcessor(
            base_url='http://rtc-asr.test', participant='target', final_frame_type=FinalFrame,
        )
        observed_timeouts: list[float] = []

        async def wait_for(awaitable, *, timeout: float):
            observed_timeouts.append(timeout)
            if times_out:
                awaitable.close()
                raise TimeoutError('Internal backend timeout detail')
            processor.final_received.set()
            return await awaitable

        monkeypatch.setattr('streaming_media.asyncio.wait_for', wait_for)
        if times_out:
            with pytest.raises(RuntimeError) as error:
                await processor._wait_for_final()
            assert str(error.value) == f'target rtc-asr did not return a final transcript within {expected:g} seconds.'
        else:
            await processor._wait_for_final()
        assert observed_timeouts == [expected]
        await processor.vad.cleanup()

    asyncio.run(run())


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


def test_rtc_asr_stale_duplicate_does_not_complete_current_stream() -> None:
    async def run() -> None:
        stale_yielded = asyncio.Event()
        release_current = asyncio.Event()

        class TranscriptWebSocket:
            async def __aiter__(self):
                yield '{"type":"transcript","text":"first","is_final":true,"revision":2,"metadata":{"client_stream_id":"utterance-1"}}'
                stale_yielded.set()
                await release_current.wait()
                yield '{"type":"transcript","text":"second","is_final":true,"revision":2,"metadata":{"client_stream_id":"utterance-2"}}'
                await asyncio.Future()

        processor = StreamingRtcAsrProcessor(
            base_url="http://rtc-asr.test",
            participant="target",
            final_frame_type=FinalFrame,
            end_type=TurnEndFrame,
        )
        processor.websocket = TranscriptWebSocket()
        processor.current_stream_id = "utterance-2"
        processor.finalizing = True
        processor.final_segments = ["first"]
        processor.transcript = "first"
        # A new media turn resets the per-turn duplicate set. The stream id
        # still has to reject a late final from the previous turn.
        processor.final_segment_event_ids = set()

        receive_task = asyncio.create_task(processor._receive())
        await stale_yielded.wait()
        await asyncio.sleep(0)

        assert processor.finalizing
        assert not processor.final_received.is_set()
        assert processor.final_result == {}

        release_current.set()
        await asyncio.wait_for(processor.final_received.wait(), timeout=1)

        assert not processor.finalizing
        assert processor.final_result["metadata"]["client_stream_id"] == "utterance-2"
        assert processor.transcript == "first second"

        processor.closing = True
        receive_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await receive_task

    asyncio.run(run())


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


@pytest.mark.parametrize('final', [False, True])
def test_adaptive_tts_skips_punctuation_without_losing_following_words(final: bool) -> None:
    assert _next_tts_chunk("?", first_chunk=False, final=final) == ("", "")
    assert _next_tts_chunk("? Please keep it on file.", first_chunk=False, final=final) == (
        "Please keep it on file.", "",
    )
    assert _next_tts_chunk("? unfinished words", first_chunk=False, final=final) == (
        ("unfinished words", "") if final else ("", "unfinished words")
    )


@pytest.mark.parametrize('next_sentence', ['', ' Please keep it on file.'])
def test_streaming_kokoro_does_not_synthesize_delayed_punctuation(next_sentence: str) -> None:
    """A word-cap chunk, later question mark and later words keep one media turn."""
    async def run() -> None:
        requests: list[str] = []
        frames: list[Frame] = []
        pcm = struct.pack('<2h', 100, -100)

        class KokoroResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def raise_for_status(self) -> None:
                pass

            async def aiter_bytes(self):
                yield _wav_header(data_size=len(pcm)) + pcm

        class KokoroClient:
            def stream(self, method: str, url: str, *, json: dict):
                assert method == 'POST' and url == 'http://kokoro.test/v1/audio/speech'
                requests.append(json['input'])
                return KokoroResponse()

        processor = StreamingKokoroProcessor(
            base_url='http://kokoro.test', model='kokoro', voice='af_heart',
            participant='tester', input_type=LLMTextFrame, start_type=TurnEndFrame,
            end_type=EndFrame, client=KokoroClient(),
        )

        async def capture(frame: Frame, direction: FrameDirection) -> None:
            assert direction == FrameDirection.DOWNSTREAM
            frames.append(frame)

        async def no_metrics() -> None:
            pass

        processor.push_frame = capture
        processor.start_processing_metrics = no_metrics
        processor.stop_processing_metrics = no_metrics
        processor.start_ttfb_metrics = no_metrics
        processor.stop_ttfb_metrics = no_metrics
        prefix = '26 Alder Street is correct. Could you read that back to me'
        await processor.process_frame(TurnEndFrame(), FrameDirection.DOWNSTREAM)
        await processor.process_frame(LLMTextFrame(prefix), FrameDirection.DOWNSTREAM)
        assert requests == [prefix]
        await processor.process_frame(LLMTextFrame('?'), FrameDirection.DOWNSTREAM)
        assert requests == [prefix]
        assert processor._pending_text == ''
        if next_sentence:
            await processor.process_frame(LLMTextFrame(next_sentence), FrameDirection.DOWNSTREAM)
        await processor.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        expected = [prefix] + ([next_sentence.strip()] if next_sentence else [])
        assert requests == expected
        assert processor.chunks == expected
        assert processor.text == prefix + '?' + next_sentence
        assert bytes(processor.audio) == pcm * len(expected)
        assert sum(isinstance(frame, OutputAudioRawFrame) for frame in frames) == len(expected)
        assert sum(isinstance(frame, EndFrame) for frame in frames) == 1
        assert isinstance(frames[-1], EndFrame)

    asyncio.run(run())
