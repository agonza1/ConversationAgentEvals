from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

from pipecat.frames.frames import (
    EndFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.daily.transport import DailyParams, DailyTransport

from outbound_voice import (
    AudioFrameCallback,
    EventCallback,
    NextTurnCallback,
    OutboundTargetAudioCollector,
    OutboundVoiceRunContext,
    OutboundVoiceTargetDescriptor,
    pace_pcm,
    wav_to_pcm,
)


PUBLIC_PIPECAT_URL = 'https://www.pipecat.ai'
DEFAULT_PUBLIC_AGENT = '10-gradium'
TARGET_AUDIO_IDLE_SECONDS = 0.20
TARGET_AUDIO_DRAIN_TIMEOUT_SECONDS = 1.0


class PublicDailyTargetError(RuntimeError):
    """Safe-to-return failure from a known public-target execution stage."""


class PublicDailyTargetRequest(BaseModel):
    caller_text: str = Field(min_length=1, max_length=2_000)
    agent: str = Field(default=DEFAULT_PUBLIC_AGENT, min_length=1, max_length=120)
    timeout_seconds: int = Field(default=90, ge=30, le=300)


class PublicDailyDuplexRequest(PublicDailyTargetRequest):
    max_turn_pairs: int = Field(default=3, ge=1, le=10)


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


def _message_is_explicitly_final(message: Any) -> bool:
    data = _message_data(message)
    return any(data.get(key) is True for key in ('final', 'is_final', 'isFinal'))


def _message_dedupe_key(message: Any, text: str) -> str:
    data = _message_data(message)
    segment_id = str(data.get('segment_id') or data.get('segmentId') or '').strip()
    return f'segment:{segment_id}' if segment_id else f'text:{text}'


def _append_unique_message_text(
    messages: list[str],
    keys: set[str],
    message: Any,
    text: str,
) -> bool:
    if not text:
        return False
    key = _message_dedupe_key(message, text)
    if key in keys:
        return False
    keys.add(key)
    messages.append(text)
    return True


def _current_target_text(evidence: Any) -> str:
    completed_output = evidence.target_output_segments[evidence.initial_target_output_count:]
    fallback_transcript = evidence.target_transcripts[evidence.initial_target_transcript_count:]
    return ' '.join(completed_output or fallback_transcript).strip()


def _transport_error_message(_errors: list[str]) -> str:
    # Daily errors may contain ephemeral room credentials or network details.
    # Keep the returned execution error stable and safe for the API/UI.
    return 'Public Pipecat Daily transport failed.'


def _raise_transport_error(evidence: Any) -> None:
    if evidence.transport_error.is_set():
        raise PublicDailyTargetError(_transport_error_message(evidence.errors))


async def _wait_for_event_or_error(
    event: asyncio.Event,
    evidence: Any,
    *,
    timeout: float,
    timeout_message: str | None,
) -> bool:
    event_waiter = asyncio.create_task(event.wait())
    error_waiter = asyncio.create_task(evidence.transport_error.wait())
    try:
        done, _pending = await asyncio.wait(
            {event_waiter, error_waiter},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if error_waiter in done and evidence.transport_error.is_set():
            raise PublicDailyTargetError(_transport_error_message(evidence.errors))
        if event_waiter not in done:
            if timeout_message is not None:
                raise PublicDailyTargetError(timeout_message)
            return False
        return True
    finally:
        for waiter in (event_waiter, error_waiter):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(event_waiter, error_waiter, return_exceptions=True)


async def _wait_for_target_audio_drain(
    evidence: Any,
    *,
    completed_at: float | None = None,
    timeout_seconds: float = TARGET_AUDIO_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Allow Daily media to catch up with its independent RTVI completion stream."""
    deadline = time.monotonic() + min(
        TARGET_AUDIO_DRAIN_TIMEOUT_SECONDS,
        max(0.0, timeout_seconds),
    )
    while time.monotonic() < deadline:
        _raise_transport_error(evidence)
        last_audio_at = evidence.last_target_audio_at
        last_activity_at = completed_at
        if last_audio_at is not None and (
            last_activity_at is None or last_audio_at > last_activity_at
        ):
            last_activity_at = last_audio_at
        if last_activity_at is not None and (
            time.perf_counter() - last_activity_at >= TARGET_AUDIO_IDLE_SECONDS
        ):
            return
        await asyncio.sleep(0.02)
    _raise_transport_error(evidence)


def _remaining_session_timeout(deadline: float, configured_seconds: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PublicDailyTargetError(
            f'Public Pipecat duplex session exceeded {configured_seconds:g} seconds.'
        )
    return remaining


async def _await_with_session_timeout(
    operation: Callable[[], Awaitable[Any]],
    *,
    deadline: float,
    configured_seconds: float,
) -> Any:
    try:
        return await asyncio.wait_for(
            operation(),
            timeout=_remaining_session_timeout(deadline, configured_seconds),
        )
    except TimeoutError as exc:
        raise PublicDailyTargetError(
            f'Public Pipecat duplex session exceeded {configured_seconds:g} seconds.'
        ) from exc


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
        data = _message_data(message)
        return data.get('will_be_spoken') is True and data.get('spoken_status') == 'completed'
    return message_type == 'bot-transcription' and _message_is_explicitly_final(message)


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


async def _play_caller_turn(
    run: OutboundVoiceRunContext,
    task: PipelineTask,
    *,
    turn_pair: int,
    caller_text: str,
    caller_wav: bytes,
    caller_pcm: bytes,
    sample_rate: int,
    channels: int,
    audio_frame_callback: AudioFrameCallback | None,
) -> int:
    """Publish caller evidence at the same boundary as target playback."""
    await run.report_phase(
        'caller_speaking',
        f'Caller is speaking in exchange {turn_pair}.',
        turn_pair=turn_pair,
    )
    await run.publish_caller_audio(turn_pair, caller_text, caller_wav)
    evidence = run.evidence
    evidence.caller_audio_sent_at = time.perf_counter()
    # Capture before playback so a target that barges in is retained.
    evidence.capture_response_audio = True
    sent = await pace_pcm(
        task,
        caller_pcm,
        sample_rate,
        channels,
        audio_frame_callback=audio_frame_callback,
        turn_pair=turn_pair,
    )
    evidence.caller_audio_ended_at = time.perf_counter()
    return sent


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


class PublicDailyTargetAdapter:
    """Daily signaling adapter for the public Pipecat demo target."""

    def __init__(self, agent: str):
        self.agent = agent
        self.descriptor = OutboundVoiceTargetDescriptor(
            adapter_id='public_pipecat_daily',
            target_kind='pipecat_public_demo',
            transport='pipecat_daily_webrtc',
            selected_target=agent,
        )

    async def open(self, *, output_sample_rate: int) -> DailyTransport:
        room_url, token = await _start_public_bot(self.agent)
        return DailyTransport(
            room_url,
            token,
            'CAE Pipecat tester',
            params=DailyParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=16_000,
                audio_out_sample_rate=output_sample_rate,
                audio_in_user_tracks=True,
                audio_in_stream_on_start=True,
                microphone_out_enabled=True,
                camera_out_enabled=False,
            ),
        )


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
    next_turn: NextTurnCallback | None = None,
    event_callback: EventCallback | None = None,
    audio_frame_callback: AudioFrameCallback | None = None,
) -> dict[str, Any]:
    """Run a bounded multi-turn evaluation in one public Daily room.

    ``next_turn`` receives ``(next_turn_pair, recognized_caller_text,
    previous_target_text, previous_target_wav)`` and returns
    ``(caller_text, caller_wav)``.
    ``event_callback`` receives live audio and exchange events as soon as each
    side's current-run evidence is available.
    """
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
    caller_pcm, caller_rate, caller_channels = wav_to_pcm(caller_wav)
    adapter = PublicDailyTargetAdapter(request.agent)
    run = OutboundVoiceRunContext(adapter.descriptor, event_callback=event_callback)
    evidence = run.evidence
    try:
        transport = await adapter.open(output_sample_rate=caller_rate)
    except Exception as exc:
        raise PublicDailyTargetError(
            'Public Pipecat demo could not create a Daily call room.'
        ) from exc
    collector = OutboundTargetAudioCollector(evidence, audio_frame_callback)
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
            evidence.target_ready.set()
        elif message_type == 'user-transcription' and _message_is_final(message):
            text = _message_text(message)
            _append_unique_message_text(
                evidence.caller_transcripts,
                evidence.caller_transcript_keys,
                message,
                text,
            )
        elif message_type == 'bot-output':
            text = _completed_bot_output_text(message)
            _append_unique_message_text(
                evidence.target_output_segments,
                evidence.target_output_keys,
                message,
                text,
            )
        elif message_type == 'bot-transcription' and _message_is_final(message):
            text = _message_text(message)
            if _append_unique_message_text(
                evidence.target_transcripts,
                evidence.target_transcript_keys,
                message,
                text,
            ):
                if (
                    event_callback is not None
                    and evidence.current_turn_pair > 0
                    and evidence.caller_audio_sent_at is not None
                ):
                    live_text = ' '.join(
                        evidence.target_transcripts[evidence.initial_target_transcript_count:]
                    ).strip()
                    if live_text:
                        await run.report_phase(
                            'bot_responding',
                            f'Public Pipecat bot is responding to exchange {evidence.current_turn_pair}.',
                            turn_pair=evidence.current_turn_pair,
                        )
                        await event_callback({
                            'type': 'live_transcript',
                            'turn_pair': evidence.current_turn_pair,
                            'speaker': 'Agent',
                            'direction': 'target_to_tester',
                            'text': live_text,
                            'media_event': 'rtvi_transcript_progress',
                        })
        elif message_type == 'bot-started-speaking':
            if evidence.caller_audio_sent_at is None:
                await run.report_phase('greeting', 'Public Pipecat bot is playing its greeting.')
            else:
                if evidence.response_started_at is None:
                    evidence.response_started_at = time.perf_counter()
                await run.report_phase(
                    'bot_responding',
                    f'Public Pipecat bot is responding to exchange {evidence.current_turn_pair}.',
                    turn_pair=evidence.current_turn_pair,
                )
        elif message_type == 'bot-stopped-speaking':
            evidence.target_stopped.set()
            if evidence.caller_audio_sent_at is None:
                evidence.initial_target_turn_complete = True
            else:
                evidence.response_complete_at = time.perf_counter()
                evidence.response_complete.set()
        if completes_bot_turn and not evidence.target_stopped.is_set():
            # RTVI v2 completion events can replace bot-stopped-speaking.
            evidence.target_stopped.set()
            if evidence.caller_audio_sent_at is None:
                evidence.initial_target_turn_complete = True
            else:
                evidence.response_complete_at = time.perf_counter()
                evidence.response_complete.set()

    @transport.event_handler('on_error')
    async def on_error(_transport: DailyTransport, error: str):
        evidence.errors.append(str(error))
        evidence.transport_error.set()

    runner = PipelineRunner(handle_sigint=False, handle_sigterm=False)
    runner_task = asyncio.create_task(runner.run(task))
    try:
        await _wait_for_event_or_error(
            evidence.connected,
            evidence,
            timeout=20,
            timeout_message='Public Pipecat Daily room connection timed out.',
        )
        await _wait_for_event_or_error(
            evidence.target_joined,
            evidence,
            timeout=20,
            timeout_message='Public Pipecat room connected, but the selected bot did not join.',
        )
        await run.report_phase('bot_joined', 'Public Pipecat bot joined the Daily room.')

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
        while not evidence.target_stopped.is_set() and time.monotonic() < readiness_deadline:
            _raise_transport_error(evidence)
            if not evidence.target_ready.is_set():
                # The public bot can join Daily before its RTVI processor is listening.
                # Re-sending the same request id makes readiness delivery idempotent.
                await transport.output().send_message(client_ready)
            remaining = max(0.1, readiness_deadline - time.monotonic())
            await _wait_for_event_or_error(
                evidence.target_stopped,
                evidence,
                timeout=min(3, remaining),
                timeout_message=None,
            )
        if not evidence.target_stopped.is_set():
            observed = sorted({str(item.get('type') or '') for item in evidence.app_messages})
            suffix = f' Observed RTVI events: {", ".join(observed)}.' if observed else ''
            raise PublicDailyTargetError(
                f'Public Pipecat bot joined Daily but did not become ready.{suffix}'
            )

        # RTVI completion and Daily media use independent streams. Hold capture
        # closed until greeting media has been idle after the completion event,
        # so delayed greeting packets cannot become exchange 1 response audio.
        await _wait_for_target_audio_drain(
            evidence,
            completed_at=time.perf_counter(),
        )

        current_text = request.caller_text
        current_wav = caller_wav
        session_deadline = time.monotonic() + request.timeout_seconds
        for turn_pair in range(1, request.max_turn_pairs + 1):
            caller_pcm, caller_rate, caller_channels = wav_to_pcm(current_wav)
            caller_transcript_count = run.begin_turn(turn_pair)

            sent = await _await_with_session_timeout(
                lambda: _play_caller_turn(
                    run,
                    task,
                    turn_pair=turn_pair,
                    caller_text=current_text,
                    caller_wav=current_wav,
                    caller_pcm=caller_pcm,
                    sample_rate=caller_rate,
                    channels=caller_channels,
                    audio_frame_callback=audio_frame_callback,
                ),
                deadline=session_deadline,
                configured_seconds=request.timeout_seconds,
            )
            await _wait_for_event_or_error(
                evidence.response_complete,
                evidence,
                timeout=_remaining_session_timeout(
                    session_deadline,
                    request.timeout_seconds,
                ),
                timeout_message=(
                    f'Public Pipecat duplex session exceeded {request.timeout_seconds:g} seconds.'
                ),
            )
            await _wait_for_target_audio_drain(
                evidence,
                completed_at=evidence.response_complete_at,
                timeout_seconds=_remaining_session_timeout(
                    session_deadline,
                    request.timeout_seconds,
                ),
            )
            _remaining_session_timeout(session_deadline, request.timeout_seconds)

            caller_receipts = evidence.caller_transcripts[caller_transcript_count:]
            caller_transcript = ' '.join(caller_receipts).strip()
            if not caller_transcript:
                raise PublicDailyTargetError(
                    f'Public Pipecat bot did not transcribe tester turn {turn_pair}.'
                )
            target_text = _current_target_text(evidence)
            if not target_text:
                raise PublicDailyTargetError(
                    f'Public Pipecat bot returned audio but no completed RTVI text for response {turn_pair}.'
                )
            if not evidence.target_audio:
                raise PublicDailyTargetError(
                    f'Public Pipecat bot completed response {turn_pair}, but Daily returned no audio.'
                )
            exchange, target_wav = run.complete_exchange(
                turn_pair=turn_pair,
                caller_text=caller_transcript,
                target_text=target_text,
                caller_wav=current_wav,
                caller_audio_frames=sent,
            )
            await run.publish_exchange(exchange, target_wav)

            if turn_pair >= request.max_turn_pairs:
                break
            if next_turn is None:
                break
            current_text, current_wav = await _await_with_session_timeout(
                lambda: next_turn(
                    turn_pair + 1,
                    caller_transcript,
                    target_text,
                    target_wav,
                ),
                deadline=session_deadline,
                configured_seconds=request.timeout_seconds,
            )
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

    try:
        return run.build_result(
            connection={
                'connected': evidence.connected.is_set(),
                'target_joined': evidence.target_joined.is_set(),
                'bot_ready': evidence.target_ready.is_set(),
                'response_complete': evidence.response_complete.is_set(),
            },
            app_messages=evidence.app_messages,
            provenance={
                'live_external_connection': True,
                'browser_peer': False,
                'headless_browser': False,
                'daily_room_credentials_persisted': False,
                'fixture_backed': False,
                'tester_media': 'current_run_kokoro',
                'target_media': 'current_run_daily_webrtc',
            },
        )
    except RuntimeError as exc:
        raise PublicDailyTargetError(str(exc).replace('Outbound voice call', 'Public Pipecat call')) from exc
