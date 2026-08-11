"""Reusable contracts and media evidence for outbound voice-agent tests.

Target adapters own signaling and target-specific events. This module owns the
transport-neutral PCM, VAD, callback, and evidence primitives shared by every
outbound voice test.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState
from pipecat.frames.frames import Frame, OutputAudioRawFrame, UserAudioRawFrame
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


AudioDirection = Literal['tester_to_target', 'target_to_tester']
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
AudioFrameCallback = Callable[[AudioDirection, bytes, int, int, int], Awaitable[None]]
NextTurnCallback = Callable[[int, str, bytes], Awaitable[tuple[str, bytes]]]


@dataclass(frozen=True, slots=True)
class OutboundVoiceTargetDescriptor:
    adapter_id: str
    target_kind: str
    transport: str
    selected_target: str


@runtime_checkable
class OutboundVoiceTransport(Protocol):
    """Minimal Pipecat transport surface required by an outbound adapter."""

    def input(self) -> FrameProcessor: ...

    def output(self) -> FrameProcessor: ...


@runtime_checkable
class OutboundVoiceTargetAdapter(Protocol):
    """Connection seam for a voice target; signaling stays adapter-specific."""

    descriptor: OutboundVoiceTargetDescriptor

    async def open(self, *, output_sample_rate: int) -> OutboundVoiceTransport: ...


@dataclass(slots=True)
class OutboundVoiceEvidence:
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    target_joined: asyncio.Event = field(default_factory=asyncio.Event)
    target_ready: asyncio.Event = field(default_factory=asyncio.Event)
    target_stopped: asyncio.Event = field(default_factory=asyncio.Event)
    response_complete: asyncio.Event = field(default_factory=asyncio.Event)
    transport_error: asyncio.Event = field(default_factory=asyncio.Event)
    target_participant_id: str | None = None
    caller_transcripts: list[str] = field(default_factory=list)
    target_transcripts: list[str] = field(default_factory=list)
    target_output_segments: list[str] = field(default_factory=list)
    caller_transcript_keys: set[str] = field(default_factory=set)
    target_transcript_keys: set[str] = field(default_factory=set)
    target_output_keys: set[str] = field(default_factory=set)
    target_audio: bytearray = field(default_factory=bytearray)
    target_audio_sample_rate: int = 16_000
    target_audio_channels: int = 1
    target_audio_frames: int = 0
    app_messages: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    caller_audio_sent_at: float | None = None
    caller_audio_ended_at: float | None = None
    response_started_at: float | None = None
    first_target_audio_at: float | None = None
    first_target_speech_at: float | None = None
    last_target_audio_at: float | None = None
    response_complete_at: float | None = None
    initial_target_turn_complete: bool = False
    initial_target_transcript_count: int = 0
    initial_target_output_count: int = 0
    current_turn_pair: int = 0
    capture_response_audio: bool = False
    reported_phases: set[str] = field(default_factory=set)


@dataclass(slots=True)
class OutboundVoiceRunContext:
    """Transport-neutral turn orchestration and result assembly."""

    descriptor: OutboundVoiceTargetDescriptor
    event_callback: EventCallback | None = None
    evidence: OutboundVoiceEvidence = field(default_factory=OutboundVoiceEvidence)
    started_at: float = field(default_factory=time.perf_counter)
    caller_audio_frames: int = 0
    caller_wavs: list[bytes] = field(default_factory=list)
    target_wavs: list[bytes] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    exchanges: list[dict[str, Any]] = field(default_factory=list)

    async def report_phase(self, phase: str, text: str, *, turn_pair: int = 0) -> None:
        phase_key = f'{turn_pair}:{phase}'
        if phase_key in self.evidence.reported_phases:
            return
        self.evidence.reported_phases.add(phase_key)
        if self.event_callback is not None:
            await self.event_callback({
                'type': 'phase',
                'phase': phase,
                'text': text,
                'turn_pair': turn_pair,
            })

    async def publish_caller_audio(self, turn_pair: int, text: str, wav: bytes) -> None:
        if self.event_callback is None:
            return
        await self.event_callback({
            'type': 'live_audio',
            'turn_pair': turn_pair,
            'speaker': 'Caller',
            'direction': 'tester_to_target',
            'text': text,
            'audio_wav_base64': base64.b64encode(wav).decode(),
            'media_event': 'tester_audio_ready',
        })

    def begin_turn(self, turn_pair: int) -> int:
        evidence = self.evidence
        evidence.current_turn_pair = turn_pair
        evidence.caller_audio_sent_at = None
        caller_transcript_count = len(evidence.caller_transcripts)
        evidence.target_stopped.clear()
        evidence.response_complete.clear()
        evidence.initial_target_transcript_count = len(evidence.target_transcripts)
        evidence.initial_target_output_count = len(evidence.target_output_segments)
        evidence.caller_transcript_keys.clear()
        evidence.target_transcript_keys.clear()
        evidence.target_output_keys.clear()
        evidence.target_audio.clear()
        evidence.target_audio_frames = 0
        evidence.capture_response_audio = False
        evidence.caller_audio_ended_at = None
        evidence.response_started_at = None
        evidence.first_target_audio_at = None
        evidence.first_target_speech_at = None
        evidence.last_target_audio_at = None
        evidence.response_complete_at = None
        return caller_transcript_count

    def complete_exchange(
        self,
        *,
        turn_pair: int,
        caller_text: str,
        target_text: str,
        caller_wav: bytes,
        caller_audio_frames: int,
    ) -> tuple[dict[str, Any], bytes]:
        evidence = self.evidence
        target_wav = pcm_to_wav(
            bytes(evidence.target_audio),
            evidence.target_audio_sample_rate,
            evidence.target_audio_channels,
        )
        first_media_frame_offset_ms = _offset_ms(
            evidence.first_target_audio_at,
            evidence.caller_audio_ended_at,
        )
        first_speech_offset_ms = _offset_ms(
            evidence.first_target_speech_at,
            evidence.caller_audio_ended_at,
        )
        first_speech_ms = (
            max(0.0, first_speech_offset_ms) if first_speech_offset_ms is not None else None
        )
        response_started_offset_ms = _offset_ms(
            evidence.response_started_at,
            evidence.caller_audio_ended_at,
        )
        overlap_offset_ms = (
            first_speech_offset_ms
            if first_speech_offset_ms is not None and first_speech_offset_ms < 0
            else response_started_offset_ms
        )
        response_complete_offset_ms = _offset_ms(
            evidence.response_complete_at,
            evidence.caller_audio_ended_at,
        )
        response_complete_ms = (
            max(0.0, response_complete_offset_ms)
            if response_complete_offset_ms is not None
            else None
        )
        exchange = {
            'turn_pair': turn_pair,
            'caller': {'text': caller_text},
            'target': {'text': target_text},
            'latency': {
                'tester_speech_end_to_first_target_audio_received_ms': first_speech_ms,
                'tester_speech_end_to_first_target_speech_received_ms': first_speech_ms,
                'first_target_media_frame_latency_ms': (
                    max(0.0, first_media_frame_offset_ms)
                    if first_media_frame_offset_ms is not None
                    else None
                ),
                'signal_boundary': 'silero_vad_speech_onset',
                'response_complete_latency_ms': response_complete_ms,
                'response_started_before_tester_speech_end': (
                    overlap_offset_ms is not None and overlap_offset_ms < 0
                ),
                'response_overlap_ms': (
                    round(abs(overlap_offset_ms), 2)
                    if overlap_offset_ms is not None and overlap_offset_ms < 0
                    else 0.0
                ),
                'measurement_scope': 'remote_target_observed_at_tester',
                'remote_target': True,
            },
            'media': {
                'caller_audio_wav_base64': base64.b64encode(caller_wav).decode(),
                'target_audio_wav_base64': base64.b64encode(target_wav).decode(),
                'caller_audio_frames': caller_audio_frames,
                'target_audio_frames': evidence.target_audio_frames,
            },
        }
        self.caller_audio_frames += caller_audio_frames
        self.caller_wavs.append(caller_wav)
        self.target_wavs.append(target_wav)
        self.turns.extend([
            {'speaker': 'caller', 'text': caller_text, 'turn_pair': turn_pair},
            {'speaker': 'agent', 'text': target_text, 'turn_pair': turn_pair},
        ])
        self.exchanges.append(exchange)
        return exchange, target_wav

    async def publish_exchange(self, exchange: dict[str, Any], target_wav: bytes) -> None:
        if self.event_callback is None:
            return
        turn_pair = int(exchange['turn_pair'])
        await self.event_callback({
            'type': 'live_audio',
            'turn_pair': turn_pair,
            'speaker': 'Agent',
            'direction': 'target_to_tester',
            'text': str(exchange['target']['text']),
            'audio_wav_base64': base64.b64encode(target_wav).decode(),
            'media_event': 'target_response_complete',
            'latency': exchange['latency'],
        })
        await self.event_callback({'type': 'exchange', **exchange})

    def build_result(
        self,
        *,
        connection: dict[str, Any],
        provenance: dict[str, Any],
        app_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.exchanges:
            raise RuntimeError('Outbound voice call completed without an exchange.')
        first_speech_values = [
            item['latency']['tester_speech_end_to_first_target_audio_received_ms']
            for item in self.exchanges
            if isinstance(
                item['latency']['tester_speech_end_to_first_target_audio_received_ms'],
                (int, float),
            )
        ]
        caller_recording = concatenate_wavs(self.caller_wavs)
        target_recording = concatenate_wavs(self.target_wavs)
        return {
            'status': 'pass',
            'target': {
                'kind': self.descriptor.target_kind,
                'selected_agent': self.descriptor.selected_target,
                'transport': self.descriptor.transport,
                'adapter_id': self.descriptor.adapter_id,
            },
            'turns': self.turns,
            'exchanges': self.exchanges,
            'latency_metrics': {
                'tester_speech_end_to_first_target_audio_received_ms': first_speech_values[0]
                if first_speech_values else None,
                'tester_speech_end_to_first_target_speech_received_ms': first_speech_values[0]
                if first_speech_values else None,
                'average_target_response_latency_ms': round(
                    sum(first_speech_values) / len(first_speech_values), 2
                ) if first_speech_values else None,
                'max_target_response_latency_ms': max(first_speech_values)
                if first_speech_values else None,
                'total_run_ms': round((time.perf_counter() - self.started_at) * 1000, 2),
            },
            'connection': connection,
            'media': {
                'caller_audio_wav_base64': base64.b64encode(caller_recording).decode(),
                'target_audio_wav_base64': base64.b64encode(target_recording).decode(),
                'target_audio_sample_rate': self.evidence.target_audio_sample_rate,
                'target_audio_channels': self.evidence.target_audio_channels,
                'target_audio_bytes': sum(len(item) for item in self.target_wavs),
                'caller_audio_frames': self.caller_audio_frames,
                'target_audio_frames': sum(
                    int(item['media']['target_audio_frames']) for item in self.exchanges
                ),
            },
            'app_messages': app_messages,
            'provenance': provenance,
        }


class OutboundTargetAudioCollector(FrameProcessor):
    """Publish all target PCM while retaining response audio and speech timing."""

    def __init__(
        self,
        evidence: OutboundVoiceEvidence,
        audio_frame_callback: AudioFrameCallback | None = None,
    ):
        super().__init__()
        self.evidence = evidence
        self.audio_frame_callback = audio_frame_callback
        self.vad = SileroVADAnalyzer(
            sample_rate=16_000,
            params=VADParams(confidence=0.7, start_secs=0.12, stop_secs=0.5, min_volume=0.4),
        )
        self.vad.set_sample_rate(16_000)
        self.previous_vad_state = VADState.QUIET
        self.speech_candidate_at: float | None = None
        self.vad_turn_pair = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserAudioRawFrame):
            if self.evidence.target_participant_id and frame.user_id != self.evidence.target_participant_id:
                return
            received_at = time.perf_counter()
            if self.audio_frame_callback is not None:
                await self.audio_frame_callback(
                    'target_to_tester',
                    frame.audio,
                    frame.sample_rate,
                    frame.num_channels,
                    self.evidence.current_turn_pair,
                )
            await self._observe_speech(frame, received_at)
            # Track target media even while response capture is closed. The
            # public Daily adapter uses this to drain delayed greeting packets
            # before opening exchange 1 capture.
            self.evidence.last_target_audio_at = received_at
            if not self.evidence.capture_response_audio:
                return
            if self.evidence.first_target_audio_at is None:
                self.evidence.first_target_audio_at = received_at
            self.evidence.target_audio.extend(frame.audio)
            self.evidence.target_audio_frames += 1
            self.evidence.target_audio_sample_rate = frame.sample_rate
            self.evidence.target_audio_channels = frame.num_channels
            return
        await self.push_frame(frame, direction)

    async def _observe_speech(self, frame: UserAudioRawFrame, received_at: float) -> None:
        if frame.sample_rate != 16_000 or frame.num_channels != 1:
            return
        if self.vad_turn_pair != self.evidence.current_turn_pair:
            self.vad_turn_pair = self.evidence.current_turn_pair
            self.speech_candidate_at = None
        vad_state = await self.vad.analyze_audio(frame.audio)
        frame_duration = len(frame.audio) / (frame.sample_rate * frame.num_channels * 2)
        if vad_state == VADState.STARTING and self.previous_vad_state != VADState.STARTING:
            self.speech_candidate_at = received_at - frame_duration
        elif vad_state == VADState.QUIET:
            self.speech_candidate_at = None
        if (
            vad_state == VADState.SPEAKING
            and self.previous_vad_state != VADState.SPEAKING
            and self.speech_candidate_at is not None
            and self.evidence.current_turn_pair > 0
            and self.evidence.caller_audio_sent_at is not None
            and self.evidence.first_target_speech_at is None
        ):
            self.evidence.first_target_speech_at = self.speech_candidate_at
        self.previous_vad_state = vad_state

    async def cleanup(self) -> None:
        await self.vad.cleanup()
        await super().cleanup()


def wav_to_pcm(payload: bytes) -> tuple[bytes, int, int]:
    with wave.open(io.BytesIO(payload), 'rb') as source:
        if source.getsampwidth() != 2:
            raise RuntimeError('Outbound voice testing requires 16-bit PCM WAV audio.')
        return source.readframes(source.getnframes()), source.getframerate(), source.getnchannels()


def pcm_to_wav(payload: bytes, sample_rate: int, channels: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(payload)
    return output.getvalue()


async def pace_pcm(
    task: PipelineTask,
    pcm: bytes,
    sample_rate: int,
    channels: int,
    *,
    audio_frame_callback: AudioFrameCallback | None = None,
    turn_pair: int = 0,
) -> int:
    """Queue PCM to the target in real time while prebuffering live observers.

    The WebRTC listener track already owns playout pacing. Publishing the caller
    audio to it one 20 ms frame at a time couples that track to Daily's send loop
    and lets scheduler jitter produce audible silence insertions. Give observers
    the complete utterance first, then independently pace only the target output.
    """
    if audio_frame_callback is not None and pcm:
        await audio_frame_callback('tester_to_target', pcm, sample_rate, channels, turn_pair)

    bytes_per_chunk = max(2, int(sample_rate * channels * 2 * 0.02))
    bytes_per_second = sample_rate * channels * 2
    frames_sent = 0
    started_at = time.perf_counter()
    for offset in range(0, len(pcm), bytes_per_chunk):
        chunk = pcm[offset:offset + bytes_per_chunk]
        await task.queue_frame(OutputAudioRawFrame(chunk, sample_rate, channels))
        frames_sent += 1
        playback_deadline = started_at + min(offset + len(chunk), len(pcm)) / bytes_per_second
        delay = playback_deadline - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
    return frames_sent


def concatenate_wavs(chunks: list[bytes]) -> bytes:
    if not chunks:
        raise RuntimeError('No outbound voice audio was captured.')
    output = io.BytesIO()
    params: tuple[int, int, int] | None = None
    frames: list[bytes] = []
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), 'rb') as source:
            current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
            if params is None:
                params = current
            if current != params:
                raise RuntimeError('Outbound voice WAV segments did not use a consistent format.')
            frames.append(source.readframes(source.getnframes()))
    assert params is not None
    with wave.open(output, 'wb') as target:
        target.setnchannels(params[0])
        target.setsampwidth(params[1])
        target.setframerate(params[2])
        target.writeframes(b''.join(frames))
    return output.getvalue()


def _offset_ms(observed_at: float | None, baseline_at: float | None) -> float | None:
    if observed_at is None or baseline_at is None:
        return None
    return round((observed_at - baseline_at) * 1000, 2)
