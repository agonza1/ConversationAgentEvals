"""Streaming media primitives for the built-in two-agent voice evaluation.

The module deliberately keeps the media plane local: Kokoro emits PCM chunks,
Silero detects speech boundaries, and rtc-asr receives PCM16 over Local STT v1.
It does not create another RTC/media service.
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    MetricsFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
AudioCallback = Callable[[bytes, int, int], Awaitable[None]]


def rtc_asr_stream_url(base_url: str, stream_path: str = "/v1/stt/stream") -> str:
    """Return the configured Local STT websocket URL."""
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base.removeprefix("https://")
    elif base.startswith("http://"):
        base = "ws://" + base.removeprefix("http://")
    return f"{base}/{stream_path.lstrip('/')}"


class StreamingWavDecoder:
    """Incrementally decode an uncompressed PCM16 WAV byte stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._data_started = False
        self.sample_rate: int | None = None
        self.channels: int | None = None
        self.bits_per_sample: int | None = None

    def feed(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        if self._data_started:
            return chunk
        self._buffer.extend(chunk)
        if len(self._buffer) < 12:
            return b""
        if self._buffer[:4] != b"RIFF" or self._buffer[8:12] != b"WAVE":
            raise RuntimeError("Kokoro response is not a RIFF/WAVE stream.")

        offset = 12
        while len(self._buffer) >= offset + 8:
            chunk_id = bytes(self._buffer[offset : offset + 4])
            chunk_size = struct.unpack_from("<I", self._buffer, offset + 4)[0]
            body_start = offset + 8
            if chunk_id == b"data":
                if self.sample_rate is None or self.channels is None:
                    raise RuntimeError("Kokoro WAV data arrived before a valid fmt chunk.")
                payload = bytes(self._buffer[body_start:])
                self._buffer.clear()
                self._data_started = True
                return payload
            padded_size = chunk_size + (chunk_size % 2)
            if len(self._buffer) < body_start + padded_size:
                return b""
            if chunk_id == b"fmt ":
                if chunk_size < 16:
                    raise RuntimeError("Kokoro WAV fmt chunk is incomplete.")
                audio_format, channels, sample_rate, _, _, bits = struct.unpack_from(
                    "<HHIIHH", self._buffer, body_start
                )
                if audio_format != 1 or bits != 16:
                    raise RuntimeError("Kokoro must return uncompressed PCM16 WAV audio.")
                self.channels = channels
                self.sample_rate = sample_rate
                self.bits_per_sample = bits
            offset = body_start + padded_size
        return b""


class StreamingKokoroProcessor(FrameProcessor):
    """Turn one selected text frame into paced PCM frames without whole-WAV buffering."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        voice: str,
        input_type: type[Frame],
        output_frame_factory: Callable[[bytes, int, int], Frame] = OutputAudioRawFrame,
        event_callback: EventCallback | None = None,
        participant: str,
    ) -> None:
        super().__init__(name=f"{participant}_kokoro")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.input_type = input_type
        self.output_frame_factory = output_frame_factory
        self.event_callback = event_callback
        self.participant = participant
        self.text = ""
        self.audio = bytearray()
        self.sample_rate = 24000
        self.channels = 1
        self.ttfb_ms: float | None = None
        self.total_ms: float | None = None

    def can_generate_metrics(self) -> bool:
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, self.input_type):
            await self.push_frame(frame, direction)
            return

        text = str(getattr(frame, "text", "")).strip()
        if not text:
            raise RuntimeError(f"{self.participant} Kokoro received empty text.")
        self.text = text
        started = time.perf_counter()
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        decoder = StreamingWavDecoder()
        pending = bytearray()
        first_payload = True
        async with httpx.AsyncClient(timeout=90) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/audio/speech",
                json={
                    "model": self.model,
                    "voice": self.voice,
                    "input": text,
                    "response_format": "wav",
                },
            ) as response:
                response.raise_for_status()
                async for http_chunk in response.aiter_bytes():
                    pcm = decoder.feed(http_chunk)
                    if not pcm:
                        continue
                    if first_payload:
                        first_payload = False
                        self.ttfb_ms = round((time.perf_counter() - started) * 1000, 3)
                        await self.stop_ttfb_metrics()
                    self.sample_rate = decoder.sample_rate or self.sample_rate
                    self.channels = decoder.channels or self.channels
                    pending.extend(pcm)
                    frame_bytes = max(2, self.sample_rate * self.channels * 2 // 50)
                    frame_bytes -= frame_bytes % (self.channels * 2)
                    while len(pending) >= frame_bytes:
                        audio = bytes(pending[:frame_bytes])
                        del pending[:frame_bytes]
                        self.audio.extend(audio)
                        await self.push_frame(
                            self.output_frame_factory(audio, self.sample_rate, self.channels),
                            direction,
                        )
        if pending:
            alignment = max(2, self.channels * 2)
            usable = len(pending) - len(pending) % alignment
            if usable:
                audio = bytes(pending[:usable])
                self.audio.extend(audio)
                await self.push_frame(
                    self.output_frame_factory(audio, self.sample_rate, self.channels),
                    direction,
                )
        if first_payload:
            raise RuntimeError("Kokoro returned no PCM audio.")
        self.total_ms = round((time.perf_counter() - started) * 1000, 3)
        await self.stop_processing_metrics()
        if self.event_callback:
            await self.event_callback(
                {
                    "type": "metric",
                    "participant": self.participant,
                    "stage": "tts",
                    "metric": "time_to_first_audio_byte",
                    "value_ms": self.ttfb_ms,
                    "total_ms": self.total_ms,
                    "source": "pipecat_metrics",
                }
            )


class StreamingMediaBridge(FrameProcessor):
    """Pace PCM in real time, publish it, and convert output audio to input audio."""

    def __init__(
        self,
        *,
        participant: str,
        audio_callback: AudioCallback,
        first_audio_callback: EventCallback | None = None,
        target_sample_rate: int = 16000,
    ) -> None:
        super().__init__(name=f"{participant}_media_bridge")
        self.participant = participant
        self.audio_callback = audio_callback
        self.first_audio_callback = first_audio_callback
        self.target_sample_rate = target_sample_rate
        self.audio = bytearray()
        self.sample_rate = 24000
        self.channels = 1
        self.first_audio_at: float | None = None
        self.audio_ended_at: float | None = None
        self._converted_pending = bytearray()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame):
            if self.first_audio_at is None:
                self.first_audio_at = time.time()
                if self.first_audio_callback:
                    await self.first_audio_callback(
                        {
                            "type": "speech_started",
                            "participant": self.participant,
                            "first_audible_pcm_at": self.first_audio_at,
                        }
                    )
            self.sample_rate = frame.sample_rate
            self.channels = frame.num_channels
            self.audio.extend(frame.audio)
            await self.audio_callback(frame.audio, frame.sample_rate, frame.num_channels)
            duration = len(frame.audio) / max(1, frame.sample_rate * frame.num_channels * 2)
            await asyncio.sleep(duration)
            self.audio_ended_at = time.time()
            mono = pcm16_to_mono(frame.audio, frame.num_channels)
            converted = resample_pcm16(mono, frame.sample_rate, self.target_sample_rate)
            self._converted_pending.extend(converted)
            frame_bytes = self.target_sample_rate * 2 // 50
            while len(self._converted_pending) >= frame_bytes:
                audio = bytes(self._converted_pending[:frame_bytes])
                del self._converted_pending[:frame_bytes]
                await self.push_frame(
                    InputAudioRawFrame(audio, self.target_sample_rate, 1),
                    direction,
                )
            return
        if isinstance(frame, EndFrame):
            frame_bytes = self.target_sample_rate * 2 // 50
            if self._converted_pending:
                self._converted_pending.extend(
                    b"\x00" * (frame_bytes - len(self._converted_pending))
                )
                await self.push_frame(
                    InputAudioRawFrame(bytes(self._converted_pending), self.target_sample_rate, 1),
                    direction,
                )
                self._converted_pending.clear()
            # Give Silero enough paced trailing audio to establish the speech end
            # without guessing from a buffered file boundary.
            silence = b"\x00\x00" * self.target_sample_rate
            for offset in range(0, len(silence), frame_bytes):
                await self.push_frame(
                    InputAudioRawFrame(silence[offset : offset + frame_bytes], self.target_sample_rate, 1),
                    direction,
                )
                await asyncio.sleep(0.02)
        await self.push_frame(frame, direction)


class StreamingRtcAsrProcessor(FrameProcessor):
    """Pipecat processor for rtc-asr Local STT v1 with Silero turn boundaries."""

    def __init__(
        self,
        *,
        base_url: str,
        stream_path: str = "/v1/stt/stream",
        participant: str,
        final_frame_type: type[TranscriptionFrame],
        event_callback: EventCallback | None = None,
        vad_params: VADParams | None = None,
    ) -> None:
        super().__init__(name=f"{participant}_rtc_asr")
        self.url = rtc_asr_stream_url(base_url, stream_path)
        self.participant = participant
        self.final_frame_type = final_frame_type
        self.event_callback = event_callback
        self.vad = SileroVADAnalyzer(
            sample_rate=16000,
            params=vad_params
            or VADParams(confidence=0.7, start_secs=0.12, stop_secs=1.0, min_volume=0.4),
        )
        # Standalone processors do not receive TransportParams' VAD setup hook.
        self.vad.set_sample_rate(16000)
        self.websocket: Any = None
        self.receiver_task: asyncio.Task[None] | None = None
        self.ready = asyncio.Event()
        self.final_received = asyncio.Event()
        self.active = False
        self.finalizing = False
        self.closing = False
        self.protocol_error: RuntimeError | None = None
        self.previous_state = VADState.QUIET
        self.pre_roll: deque[bytes] = deque(maxlen=15)
        self.transcript = ""
        self.interims: list[str] = []
        self.speech_started_at: float | None = None
        self.speech_ended_at: float | None = None
        self.final_at: float | None = None
        self.server_timing: dict[str, Any] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            # Once rtc-asr reports a protocol failure, drain queued media until
            # EndFrame and surface the error once instead of producing one
            # Pipecat ErrorFrame for every remaining 20 ms audio frame.
            if self.protocol_error is not None:
                return
            if frame.sample_rate != 16000 or frame.num_channels != 1:
                raise RuntimeError("Streaming rtc-asr requires 16 kHz mono PCM16 frames.")
            self.pre_roll.append(frame.audio)
            state = await self.vad.analyze_audio(frame.audio)
            started_now = False
            if _should_start_utterance(
                state=state,
                previous_state=self.previous_state,
                active=self.active,
                finalizing=self.finalizing,
            ):
                await self._start_utterance(direction)
                started_now = True
            if self.active and not started_now:
                self._raise_protocol_error()
                await self.websocket.send(frame.audio)
            if (
                state == VADState.QUIET
                and self.previous_state in {VADState.SPEAKING, VADState.STOPPING}
            ):
                await self._finalize(direction, wait_for_final=False)
            self.previous_state = state
            return
        if isinstance(frame, EndFrame):
            if self.active:
                await self._finalize(direction, wait_for_final=True)
            elif self.finalizing:
                await self._wait_for_final()
            self._raise_protocol_error()
            await self._close()
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        await self._close()
        await self.vad.cleanup()
        await super().cleanup()

    async def _connect(self) -> None:
        self._raise_protocol_error()
        if self.websocket is not None:
            return
        self.websocket = await websockets.connect(self.url, open_timeout=5, close_timeout=2)
        self.receiver_task = asyncio.create_task(self._receive())

    async def _start_utterance(self, direction: FrameDirection) -> None:
        if self.active or self.finalizing:
            return
        await self._connect()
        self.ready.clear()
        self.final_received.clear()
        assert self.websocket is not None
        await self.websocket.send(
            json.dumps(
                {
                    "type": "start",
                    "version": "local-stt.v1",
                    "audio": {
                        "sample_rate": 16000,
                        "channels": 1,
                        "format": "pcm_s16le",
                        "frame_ms": 20,
                        "bytes_per_frame": 640,
                    },
                    "interim_results": True,
                    "partial_interval_ms": 100,
                    "partial_window_seconds": 2.0,
                    "max_buffer_seconds": 20.0,
                    "client_stream_id": f"{self.participant}-{time.time_ns()}",
                    "metadata": {"participant": self.participant},
                }
            )
        )
        await asyncio.wait_for(self.ready.wait(), timeout=5)
        self._raise_protocol_error()
        self.active = True
        self.speech_started_at = time.time()
        await self.push_frame(VADUserStartedSpeakingFrame(start_secs=self.vad.params.start_secs), direction)
        buffered_audio = tuple(self.pre_roll)
        self.pre_roll.clear()
        for audio in buffered_audio:
            await self.websocket.send(audio)
        if self.event_callback:
            await self.event_callback(
                {"type": "vad", "participant": self.participant, "state": "speech_started"}
            )

    async def _finalize(
        self,
        direction: FrameDirection,
        *,
        wait_for_final: bool,
    ) -> None:
        if not self.active or self.websocket is None:
            return
        self.active = False
        self.finalizing = True
        self.speech_ended_at = time.time() - self.vad.params.stop_secs
        await self.push_frame(
            VADUserStoppedSpeakingFrame(stop_secs=self.vad.params.stop_secs),
            direction,
        )
        await self.websocket.send(json.dumps({"type": "finalize"}))
        if wait_for_final:
            await self._wait_for_final()

    async def _wait_for_final(self) -> None:
        try:
            await asyncio.wait_for(self.final_received.wait(), timeout=20)
        except TimeoutError as exc:
            raise RuntimeError(
                f"{self.participant} rtc-asr did not return a final transcript within 20 seconds."
            ) from exc
        self._raise_protocol_error()

    def _raise_protocol_error(self) -> None:
        if self.protocol_error is not None:
            raise self.protocol_error

    async def _receive(self) -> None:
        assert self.websocket is not None
        try:
            async for raw in self.websocket:
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                event_type = payload.get("type")
                if event_type == "ready":
                    self.ready.set()
                    continue
                if event_type == "ping":
                    await self.websocket.send(json.dumps({"type": "pong"}))
                    continue
                if event_type != "transcript":
                    if event_type == "error":
                        self.protocol_error = RuntimeError(
                            f"{self.participant} rtc-asr: "
                            f"{payload.get('message') or payload}"
                        )
                        self.ready.set()
                        self.final_received.set()
                        return
                    continue
                text = str(payload.get("text") or "").strip()
                if not text:
                    continue
                if payload.get("is_final"):
                    self.transcript = text
                    self.final_at = time.time()
                    self.server_timing = {
                        key: payload.get(key)
                        for key in ("audio_received_ms", "audio_transcribed_ms", "revision")
                        if payload.get(key) is not None
                    }
                    await self.push_frame(
                        self.final_frame_type(
                            text=text,
                            user_id=self.participant,
                            timestamp=datetime.now(UTC).isoformat(),
                            language="en",
                            result=payload,
                            finalized=True,
                        )
                    )
                    self.finalizing = False
                    self.final_received.set()
                else:
                    self.interims.append(text)
                    await self.push_frame(
                        InterimTranscriptionFrame(
                            text=text,
                            user_id=self.participant,
                            timestamp=datetime.now(UTC).isoformat(),
                            language="en",
                            result=payload,
                        )
                    )
                if self.event_callback:
                    await self.event_callback(
                        {
                            "type": "transcript",
                            "participant": self.participant,
                            "text": text,
                            "is_final": bool(payload.get("is_final")),
                            "speech_final": bool(payload.get("speech_final")),
                            "revision": payload.get("revision"),
                            "audio_received_ms": payload.get("audio_received_ms"),
                            "audio_transcribed_ms": payload.get("audio_transcribed_ms"),
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.protocol_error = RuntimeError(
                f"{self.participant} rtc-asr stream failed: {exc}"
            )
        finally:
            if not self.closing and self.protocol_error is None:
                self.protocol_error = RuntimeError(
                    f"{self.participant} rtc-asr stream closed before the exchange completed."
                )
            self.ready.set()
            self.final_received.set()

    async def _close(self) -> None:
        self.closing = True
        websocket = self.websocket
        self.websocket = None
        if websocket is not None:
            try:
                await websocket.send(json.dumps({"type": "close"}))
            except Exception:
                pass
            await websocket.close()
        if self.receiver_task is not None:
            self.receiver_task.cancel()
            await asyncio.gather(self.receiver_task, return_exceptions=True)
            self.receiver_task = None
        self.active = False
        self.finalizing = False


class MetricsCollector(FrameProcessor):
    """Collect Pipecat MetricsFrame data without interrupting the pipeline."""

    def __init__(self) -> None:
        super().__init__(name="voice_eval_metrics")
        self.metrics: list[dict[str, Any]] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for item in frame.data:
                dumped = item.model_dump() if hasattr(item, "model_dump") else vars(item)
                value = dumped.get("value")
                if isinstance(value, (int, float)) and value <= 0:
                    continue
                dumped["metric_type"] = type(item).__name__
                self.metrics.append(dumped)
        await self.push_frame(frame, direction)


def pcm16_to_mono(payload: bytes, channels: int) -> bytes:
    """Downmix interleaved little-endian PCM16 audio to mono."""
    if channels <= 0:
        raise ValueError("Audio channel count must be positive.")
    if channels == 1:
        return payload
    usable = len(payload) - len(payload) % (channels * 2)
    samples = struct.unpack(f"<{usable // 2}h", payload[:usable])
    mono = [
        round(sum(samples[offset : offset + channels]) / channels)
        for offset in range(0, len(samples), channels)
    ]
    return struct.pack(f"<{len(mono)}h", *mono)


def _should_start_utterance(
    *,
    state: VADState,
    previous_state: VADState,
    active: bool,
    finalizing: bool,
) -> bool:
    return (
        not active
        and not finalizing
        and state == VADState.SPEAKING
        and previous_state != VADState.SPEAKING
    )


def resample_pcm16(payload: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linearly resample little-endian mono PCM16 audio."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Audio sample rates must be positive.")
    if source_rate == target_rate:
        return payload
    count = len(payload) // 2
    if not count:
        return b""
    samples = struct.unpack(f"<{count}h", payload[: count * 2])
    output_count = max(1, round(count * target_rate / source_rate))
    output: list[int] = []
    for index in range(output_count):
        position = min(index * source_rate / target_rate, count - 1)
        left = int(position)
        right = min(left + 1, count - 1)
        fraction = position - left
        output.append(round(samples[left] + (samples[right] - samples[left]) * fraction))
    return struct.pack(f"<{len(output)}h", *output)
