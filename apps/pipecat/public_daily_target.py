from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
    UserAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.daily.transport import DailyParams, DailyTransport


PUBLIC_PIPECAT_URL = 'https://www.pipecat.ai'
DEFAULT_PUBLIC_AGENT = '10-gradium'


class PublicDailyTargetError(RuntimeError):
    """Safe-to-return failure from a known public-target execution stage."""


class PublicDailyTargetRequest(BaseModel):
    caller_text: str = Field(min_length=1, max_length=2_000)
    agent: str = Field(default=DEFAULT_PUBLIC_AGENT, min_length=1, max_length=120)
    timeout_seconds: int = Field(default=90, ge=30, le=300)


class PublicDailyDuplexRequest(PublicDailyTargetRequest):
    max_turn_pairs: int = Field(default=3, ge=1, le=10)


@dataclass(slots=True)
class _DirectDailyEvidence:
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    target_joined: asyncio.Event = field(default_factory=asyncio.Event)
    bot_ready: asyncio.Event = field(default_factory=asyncio.Event)
    bot_stopped: asyncio.Event = field(default_factory=asyncio.Event)
    response_complete: asyncio.Event = field(default_factory=asyncio.Event)
    target_participant_id: str | None = None
    caller_transcripts: list[str] = field(default_factory=list)
    target_transcripts: list[str] = field(default_factory=list)
    target_output_segments: list[str] = field(default_factory=list)
    target_audio: bytearray = field(default_factory=bytearray)
    target_audio_sample_rate: int = 16_000
    target_audio_channels: int = 1
    target_audio_frames: int = 0
    app_messages: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    caller_audio_sent_at: float | None = None
    caller_audio_ended_at: float | None = None
    first_target_audio_at: float | None = None
    response_complete_at: float | None = None
    initial_bot_turn_complete: bool = False
    initial_target_transcript_count: int = 0
    initial_target_output_count: int = 0
    capture_response_audio: bool = False


class _RemoteAudioCollector(FrameProcessor):
    def __init__(self, evidence: _DirectDailyEvidence):
        super().__init__()
        self.evidence = evidence

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserAudioRawFrame):
            if self.evidence.target_participant_id and frame.user_id != self.evidence.target_participant_id:
                return
            if not self.evidence.capture_response_audio:
                return
            if self.evidence.first_target_audio_at is None:
                self.evidence.first_target_audio_at = time.perf_counter()
            self.evidence.target_audio.extend(frame.audio)
            self.evidence.target_audio_frames += 1
            self.evidence.target_audio_sample_rate = frame.sample_rate
            self.evidence.target_audio_channels = frame.num_channels
            return
        await self.push_frame(frame, direction)


def _message_type(message: Any) -> str:
    if not isinstance(message, dict):
        return ''
    return str(message.get('type') or message.get('messageType') or '').strip().lower()


def _message_data(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    data = message.get('data')
    return data if isinstance(data, dict) else message


def _message_text(message: Any) -> str:
    data = _message_data(message)
    value = data.get('text') or data.get('transcript') or data.get('content')
    if isinstance(value, dict):
        value = value.get('text') or value.get('content')
    return str(value or '').strip()


def _message_is_final(message: Any) -> bool:
    data = _message_data(message)
    return bool(data.get('final', data.get('is_final', data.get('isFinal', True))))


def _completed_bot_output_text(message: Any) -> str:
    """Return only the completed spoken representation from an RTVI bot-output event."""
    data = _message_data(message)
    if data.get('spoken') is True:
        return _message_text(message)
    if data.get('will_be_spoken') is True and data.get('spoken_status') == 'completed':
        progress = data.get('spoken_progress')
        if isinstance(progress, dict):
            accumulated = str(progress.get('accumulated_text') or '').strip()
            if accumulated:
                return accumulated
        return _message_text(message)
    return ''


def _message_completes_bot_turn(message_type: str, message: Any) -> bool:
    """Recognize both RTVI generations' end-of-bot-turn signals.

    The public demo no longer consistently emits ``bot-stopped-speaking``.
    A completed spoken ``bot-output`` or final ``bot-transcription`` carries
    the same completion evidence and keeps the transport from waiting for an
    event that may never arrive.
    """
    if message_type == 'bot-output':
        return bool(_completed_bot_output_text(message))
    return message_type == 'bot-transcription' and _message_is_final(message)


def _wav_to_pcm(payload: bytes) -> tuple[bytes, int, int]:
    with wave.open(io.BytesIO(payload), 'rb') as source:
        if source.getsampwidth() != 2:
            raise RuntimeError('Public Pipecat tester requires 16-bit PCM WAV from Kokoro.')
        return source.readframes(source.getnframes()), source.getframerate(), source.getnchannels()


def _pcm_to_wav(payload: bytes, sample_rate: int, channels: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(payload)
    return output.getvalue()


async def _synthesize_caller(text: str, *, kokoro_base_url: str, model: str, voice: str) -> bytes:
    if not kokoro_base_url:
        raise RuntimeError('Set KOKORO_BASE_URL to synthesize the public Pipecat tester utterance.')
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f'{kokoro_base_url.rstrip("/")}/v1/audio/speech',
            json={'model': model, 'voice': voice, 'input': text, 'response_format': 'wav'},
        )
        response.raise_for_status()
    if not response.content:
        raise RuntimeError('Kokoro returned no tester audio for the public Pipecat run.')
    return response.content


async def _start_public_bot(agent: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f'{PUBLIC_PIPECAT_URL}/api/start', json={'agent': agent})
        response.raise_for_status()
        payload = response.json()
    room_url = str(payload.get('dailyRoom') or '').strip()
    token = str(payload.get('dailyToken') or '').strip()
    if not room_url or not token:
        raise RuntimeError('The public Pipecat start endpoint did not return Daily room credentials.')
    return room_url, token


async def _queue_pcm(task: PipelineTask, pcm: bytes, sample_rate: int, channels: int) -> int:
    bytes_per_chunk = max(2, int(sample_rate * channels * 2 * 0.02))
    frames_sent = 0
    for offset in range(0, len(pcm), bytes_per_chunk):
        await task.queue_frame(OutputAudioRawFrame(pcm[offset:offset + bytes_per_chunk], sample_rate, channels))
        frames_sent += 1
        await asyncio.sleep(0.02)
    return frames_sent


def _concatenate_wavs(chunks: list[bytes]) -> bytes:
    if not chunks:
        raise RuntimeError('No public Pipecat audio was captured.')
    output = io.BytesIO()
    params: tuple[int, int, int] | None = None
    frames: list[bytes] = []
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), 'rb') as source:
            current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
            if params is None:
                params = current
            if current != params:
                raise RuntimeError('Public Pipecat WAV segments did not use a consistent format.')
            frames.append(source.readframes(source.getnframes()))
    assert params is not None
    with wave.open(output, 'wb') as target:
        target.setnchannels(params[0])
        target.setsampwidth(params[1])
        target.setframerate(params[2])
        target.writeframes(b''.join(frames))
    return output.getvalue()


async def run_public_daily_target(
    request: PublicDailyTargetRequest,
    *,
    kokoro_base_url: str,
    kokoro_model: str,
    kokoro_voice: str,
) -> dict[str, Any]:
    return await run_public_daily_duplex(
        PublicDailyDuplexRequest(**request.model_dump(), max_turn_pairs=1),
        kokoro_base_url=kokoro_base_url,
        kokoro_model=kokoro_model,
        kokoro_voice=kokoro_voice,
    )


async def run_public_daily_duplex(
    request: PublicDailyDuplexRequest,
    *,
    kokoro_base_url: str,
    kokoro_model: str,
    kokoro_voice: str,
    next_turn: Any | None = None,
    event_callback: Any | None = None,
) -> dict[str, Any]:
    """Run a bounded multi-turn evaluation in one public Daily room.

    ``next_turn`` receives ``(next_turn_pair, previous_target_wav)`` and returns
    ``(caller_text, caller_wav)``. ``event_callback`` receives live audio and
    exchange events as soon as each side's current-run evidence is available.
    """
    started = time.perf_counter()
    try:
        caller_wav = await _synthesize_caller(
            request.caller_text,
            kokoro_base_url=kokoro_base_url,
            model=kokoro_model,
            voice=kokoro_voice,
        )
    except Exception as exc:
        raise PublicDailyTargetError(
            'Public Pipecat tester audio synthesis failed; verify Kokoro is reachable.'
        ) from exc
    initial_caller_published = False
    if event_callback is not None:
        await event_callback({
            'type': 'live_audio',
            'turn_pair': 1,
            'speaker': 'Caller',
            'direction': 'tester_to_target',
            'text': request.caller_text,
            'audio_wav_base64': base64.b64encode(caller_wav).decode(),
            'media_event': 'tester_audio_ready',
        })
        initial_caller_published = True
    caller_pcm, caller_rate, caller_channels = _wav_to_pcm(caller_wav)
    try:
        room_url, token = await _start_public_bot(request.agent)
    except Exception as exc:
        raise PublicDailyTargetError(
            'Public Pipecat demo could not create a Daily call room.'
        ) from exc
    evidence = _DirectDailyEvidence()
    transport = DailyTransport(
        room_url,
        token,
        'CAE Pipecat tester',
        params=DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16_000,
            audio_out_sample_rate=caller_rate,
            audio_in_user_tracks=True,
            audio_in_stream_on_start=True,
            microphone_out_enabled=True,
            camera_out_enabled=False,
        ),
    )
    collector = _RemoteAudioCollector(evidence)
    pipeline = Pipeline([transport.input(), collector, transport.output()])
    task = PipelineTask(
        pipeline,
        enable_rtvi=False,
        enable_turn_tracking=False,
        params=PipelineParams(audio_in_sample_rate=16_000, audio_out_sample_rate=caller_rate),
    )

    async def capture_participant(participant: dict[str, Any]) -> None:
        participant_id = str(participant.get('id') or '').strip()
        if (
            not participant_id
            or participant_id == transport.participant_id
            or participant.get('local') is True
        ):
            return
        evidence.target_participant_id = participant_id
        await transport.capture_participant_audio(participant_id, sample_rate=16_000)
        evidence.target_joined.set()

    @transport.event_handler('on_joined')
    async def on_joined(_transport: DailyTransport, _data: dict[str, Any]):
        evidence.connected.set()
        for participant in transport.participants().values():
            if isinstance(participant, dict):
                await capture_participant(participant)

    @transport.event_handler('on_participant_joined')
    async def on_participant_joined(_transport: DailyTransport, participant: dict[str, Any]):
        await capture_participant(participant)

    @transport.event_handler('on_app_message')
    async def on_app_message(_transport: DailyTransport, message: Any, sender: str):
        message_type = _message_type(message)
        data = _message_data(message)
        completes_bot_turn = _message_completes_bot_turn(message_type, message)
        if message_type in {
            'bot-ready',
            'bot-started-speaking',
            'bot-stopped-speaking',
            'user-started-speaking',
            'user-stopped-speaking',
            'user-transcription',
            'bot-output',
            'bot-transcription',
            'error',
        }:
            evidence.app_messages.append({
                'type': message_type,
                'sender': '<target-participant>' if sender else None,
                'data': {
                    key: value
                    for key, value in data.items()
                    if key in {
                        'text',
                        'transcript',
                        'final',
                        'is_final',
                        'isFinal',
                        'aggregated_by',
                        'segment_id',
                        'spoken',
                        'will_be_spoken',
                        'spoken_status',
                    }
                },
            })
        if message_type == 'bot-ready':
            evidence.bot_ready.set()
        elif message_type == 'user-transcription' and _message_is_final(message):
            text = _message_text(message)
            if text and (not evidence.caller_transcripts or evidence.caller_transcripts[-1] != text):
                evidence.caller_transcripts.append(text)
        elif message_type == 'bot-output':
            text = _completed_bot_output_text(message)
            if text and (not evidence.target_output_segments or evidence.target_output_segments[-1] != text):
                evidence.target_output_segments.append(text)
        elif message_type == 'bot-transcription' and _message_is_final(message):
            text = _message_text(message)
            if text and (not evidence.target_transcripts or evidence.target_transcripts[-1] != text):
                evidence.target_transcripts.append(text)
        elif message_type == 'bot-started-speaking' and evidence.caller_audio_sent_at is not None:
            evidence.capture_response_audio = True
        elif message_type == 'bot-stopped-speaking':
            evidence.bot_stopped.set()
            if evidence.caller_audio_sent_at is None:
                evidence.initial_bot_turn_complete = True
            else:
                evidence.response_complete_at = time.perf_counter()
                evidence.response_complete.set()
        if completes_bot_turn and not evidence.bot_stopped.is_set():
            # RTVI v2 completion events can replace bot-stopped-speaking.
            evidence.bot_stopped.set()
            if evidence.caller_audio_sent_at is None:
                evidence.initial_bot_turn_complete = True
            else:
                evidence.response_complete_at = time.perf_counter()
                evidence.response_complete.set()

    @transport.event_handler('on_error')
    async def on_error(_transport: DailyTransport, error: str):
        evidence.errors.append(str(error))

    runner = PipelineRunner(handle_sigint=False, handle_sigterm=False)
    runner_task = asyncio.create_task(runner.run(task))
    caller_audio_frames = 0
    all_caller_wavs: list[bytes] = []
    all_target_wavs: list[bytes] = []
    turns: list[dict[str, Any]] = []
    exchanges: list[dict[str, Any]] = []
    try:
        try:
            await asyncio.wait_for(evidence.connected.wait(), timeout=20)
        except TimeoutError as exc:
            raise PublicDailyTargetError(
                'Public Pipecat Daily room connection timed out.'
            ) from exc
        try:
            await asyncio.wait_for(evidence.target_joined.wait(), timeout=20)
        except TimeoutError as exc:
            raise PublicDailyTargetError(
                'Public Pipecat room connected, but the selected bot did not join.'
            ) from exc

        client_ready = OutputTransportMessageUrgentFrame({
            'label': 'rtvi-ai',
            'type': 'client-ready',
            'id': uuid.uuid4().hex[:8],
            'data': {
                'version': '1.2.0',
                'about': {'library': 'conversation-agent-evals', 'library_version': 'direct-daily-v1'},
            },
        })
        readiness_deadline = time.monotonic() + 30
        while not evidence.bot_stopped.is_set() and time.monotonic() < readiness_deadline:
            if not evidence.bot_ready.is_set():
                # The public bot can join Daily before its RTVI processor is listening.
                # Re-sending the same request id makes readiness delivery idempotent.
                await transport.output().send_message(client_ready)
            remaining = max(0.1, readiness_deadline - time.monotonic())
            try:
                await asyncio.wait_for(evidence.bot_stopped.wait(), timeout=min(3, remaining))
            except TimeoutError:
                continue
        if not evidence.bot_stopped.is_set():
            observed = sorted({str(item.get('type') or '') for item in evidence.app_messages})
            suffix = f' Observed RTVI events: {", ".join(observed)}.' if observed else ''
            raise PublicDailyTargetError(
                f'Public Pipecat bot joined Daily but did not become ready.{suffix}'
            )

        current_text = request.caller_text
        current_wav = caller_wav
        for turn_pair in range(1, request.max_turn_pairs + 1):
            caller_pcm, caller_rate, caller_channels = _wav_to_pcm(current_wav)
            caller_transcript_count = len(evidence.caller_transcripts)
            evidence.bot_stopped.clear()
            evidence.response_complete.clear()
            evidence.initial_target_transcript_count = len(evidence.target_transcripts)
            evidence.initial_target_output_count = len(evidence.target_output_segments)
            evidence.target_audio.clear()
            evidence.target_audio_frames = 0
            # Daily media and RTVI app messages are independent streams. Capture
            # before caller playback so leading target frames cannot arrive ahead
            # of the bot-started-speaking notification and be discarded.
            evidence.capture_response_audio = True
            evidence.first_target_audio_at = None
            evidence.response_complete_at = None

            if event_callback is not None and not (turn_pair == 1 and initial_caller_published):
                await event_callback({
                    'type': 'live_audio',
                    'turn_pair': turn_pair,
                    'speaker': 'Caller',
                    'direction': 'tester_to_target',
                    'text': current_text,
                    'audio_wav_base64': base64.b64encode(current_wav).decode(),
                    'media_event': 'tester_audio_ready',
                })
            evidence.caller_audio_sent_at = time.perf_counter()
            sent = await _queue_pcm(task, caller_pcm, caller_rate, caller_channels)
            caller_audio_frames += sent
            evidence.caller_audio_ended_at = time.perf_counter()
            try:
                await asyncio.wait_for(evidence.response_complete.wait(), timeout=request.timeout_seconds)
            except TimeoutError as exc:
                raise PublicDailyTargetError(
                    f'Public Pipecat bot did not complete response {turn_pair} before the run timeout.'
                ) from exc

            caller_receipts = evidence.caller_transcripts[caller_transcript_count:]
            caller_transcript = ' '.join(caller_receipts).strip()
            if not caller_transcript:
                raise PublicDailyTargetError(
                    f'Public Pipecat bot did not transcribe tester turn {turn_pair}.'
                )
            response_outputs = evidence.target_output_segments[evidence.initial_target_output_count:]
            response_transcripts = (
                response_outputs
                or evidence.target_transcripts[evidence.initial_target_transcript_count:]
            )
            target_text = ' '.join(response_transcripts).strip()
            if not target_text:
                raise PublicDailyTargetError(
                    f'Public Pipecat bot returned audio but no completed RTVI text for response {turn_pair}.'
                )
            if not evidence.target_audio:
                raise PublicDailyTargetError(
                    f'Public Pipecat bot completed response {turn_pair}, but Daily returned no audio.'
                )
            target_wav = _pcm_to_wav(
                bytes(evidence.target_audio),
                evidence.target_audio_sample_rate,
                evidence.target_audio_channels,
            )
            first_audio_offset_ms = (
                round((evidence.first_target_audio_at - evidence.caller_audio_ended_at) * 1000, 2)
                if evidence.first_target_audio_at is not None and evidence.caller_audio_ended_at is not None
                else None
            )
            first_audio_ms = max(0.0, first_audio_offset_ms) if first_audio_offset_ms is not None else None
            response_complete_ms = (
                round(max(0.0, evidence.response_complete_at - evidence.caller_audio_ended_at) * 1000, 2)
                if evidence.response_complete_at is not None and evidence.caller_audio_ended_at is not None
                else None
            )
            exchange = {
                'turn_pair': turn_pair,
                'caller': {'text': caller_transcript},
                'target': {'text': target_text},
                'latency': {
                    'tester_speech_end_to_first_target_audio_received_ms': first_audio_ms,
                    'response_complete_latency_ms': response_complete_ms,
                    'response_started_before_tester_speech_end': (
                        first_audio_offset_ms is not None and first_audio_offset_ms < 0
                    ),
                    'response_overlap_ms': (
                        round(abs(first_audio_offset_ms), 2)
                        if first_audio_offset_ms is not None and first_audio_offset_ms < 0
                        else 0.0
                    ),
                },
                'media': {
                    'caller_audio_wav_base64': base64.b64encode(current_wav).decode(),
                    'target_audio_wav_base64': base64.b64encode(target_wav).decode(),
                    'caller_audio_frames': sent,
                    'target_audio_frames': evidence.target_audio_frames,
                },
            }
            turns.extend([
                {'speaker': 'caller', 'text': caller_transcript, 'turn_pair': turn_pair},
                {'speaker': 'agent', 'text': target_text, 'turn_pair': turn_pair},
            ])
            exchanges.append(exchange)
            all_caller_wavs.append(current_wav)
            all_target_wavs.append(target_wav)
            if event_callback is not None:
                await event_callback({
                    'type': 'live_audio',
                    'turn_pair': turn_pair,
                    'speaker': 'Agent',
                    'direction': 'target_to_tester',
                    'text': target_text,
                    'audio_wav_base64': base64.b64encode(target_wav).decode(),
                    'media_event': 'target_response_complete',
                    'latency': exchange['latency'],
                })
                await event_callback({'type': 'exchange', **exchange})

            if turn_pair >= request.max_turn_pairs:
                break
            if next_turn is None:
                break
            current_text, current_wav = await next_turn(turn_pair + 1, target_wav)
            if not str(current_text).strip() or not current_wav:
                raise PublicDailyTargetError(
                    f'Public Pipecat tester graph returned no caller media for turn {turn_pair + 1}.'
                )
    finally:
        await task.queue_frame(EndFrame())
        try:
            await asyncio.wait_for(runner_task, timeout=15)
        except TimeoutError:
            await task.cancel()
            runner_task.cancel()

    if not exchanges:
        raise PublicDailyTargetError('Public Pipecat call completed without an exchange.')
    first_audio_values = [
        item['latency']['tester_speech_end_to_first_target_audio_received_ms']
        for item in exchanges
        if isinstance(item['latency']['tester_speech_end_to_first_target_audio_received_ms'], (int, float))
    ]
    caller_recording = _concatenate_wavs(all_caller_wavs)
    target_recording = _concatenate_wavs(all_target_wavs)
    return {
        'status': 'pass',
        'target': {
            'kind': 'pipecat_public_demo',
            'selected_agent': request.agent,
            'transport': 'pipecat_daily_webrtc',
        },
        'turns': turns,
        'exchanges': exchanges,
        'latency_metrics': {
            'tester_speech_end_to_first_target_audio_received_ms': first_audio_values[0]
            if first_audio_values else None,
            'average_target_response_latency_ms': round(sum(first_audio_values) / len(first_audio_values), 2)
            if first_audio_values else None,
            'max_target_response_latency_ms': max(first_audio_values) if first_audio_values else None,
            'total_run_ms': round((time.perf_counter() - started) * 1000, 2),
        },
        'connection': {
            'connected': evidence.connected.is_set(),
            'target_joined': evidence.target_joined.is_set(),
            'bot_ready': evidence.bot_ready.is_set(),
            'response_complete': evidence.response_complete.is_set(),
        },
        'media': {
            'caller_audio_wav_base64': base64.b64encode(caller_recording).decode(),
            'target_audio_wav_base64': base64.b64encode(target_recording).decode(),
            'target_audio_sample_rate': evidence.target_audio_sample_rate,
            'target_audio_channels': evidence.target_audio_channels,
            'target_audio_bytes': sum(len(item) for item in all_target_wavs),
            'caller_audio_frames': caller_audio_frames,
            'target_audio_frames': sum(
                int(item['media']['target_audio_frames']) for item in exchanges
            ),
        },
        'app_messages': evidence.app_messages,
        'provenance': {
            'live_external_connection': True,
            'browser_peer': False,
            'headless_browser': False,
            'daily_room_credentials_persisted': False,
            'fixture_backed': False,
            'tester_media': 'current_run_kokoro',
            'target_media': 'current_run_daily_webrtc',
        },
    }
