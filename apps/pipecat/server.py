from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

_env_candidates = [*Path(__file__).resolve().parents, Path.cwd()]
for _parent in _env_candidates:
    _env_path = _parent / '.env'
    if _env_path.exists():
        load_dotenv(_env_path)
        break

try:
    import aiohttp
    from aiortc import RTCIceServer, RTCSessionDescription
    from aiortc.sdp import candidate_from_sdp
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.frames.frames import (
        EndFrame,
        ErrorFrame,
        Frame,
        InputAudioRawFrame,
        LLMContextFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        OutputAudioRawFrame,
        TextFrame,
        TranscriptionFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.services.llm_service import FunctionCallParams
    from pipecat.services.openai.realtime.events import (
        AudioConfiguration,
        AudioInput,
        AudioOutput,
        InputAudioTranscription,
        SessionProperties,
        TurnDetection,
    )
    from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.transport import RawAudioTrack, SmallWebRTCTransport
    from streaming_media import (
        MetricsCollector,
        StreamingKokoroProcessor,
        StreamingMediaBridge,
        StreamingRtcAsrProcessor,
        pcm16_to_mono,
        resample_pcm16,
    )
    try:
        from pipecat.services.heygen.api_liveavatar import LiveAvatarNewSessionRequest
        from pipecat.services.heygen.client import ServiceType
        from pipecat.services.heygen.video import HeyGenVideoService
    except (ImportError, ModuleNotFoundError):
        LiveAvatarNewSessionRequest = None  # type: ignore[assignment]
        HeyGenVideoService = None  # type: ignore[assignment]
        ServiceType = None  # type: ignore[assignment]
    PIPECAT_RUNTIME_AVAILABLE = True
except Exception:  # pragma: no cover - fallback for non-pipecat test envs
    aiohttp = None  # type: ignore[assignment]
    RTCSessionDescription = None  # type: ignore[assignment]
    RTCIceServer = None  # type: ignore[assignment]
    candidate_from_sdp = None  # type: ignore[assignment]
    FunctionSchema = None  # type: ignore[assignment]
    FunctionCallParams = Any  # type: ignore[misc, assignment]
    ToolsSchema = None  # type: ignore[assignment]
    AudioConfiguration = None  # type: ignore[assignment]
    AudioInput = None  # type: ignore[assignment]
    AudioOutput = None  # type: ignore[assignment]
    InputAudioTranscription = None  # type: ignore[assignment]
    OpenAIRealtimeLLMService = None  # type: ignore[assignment]
    LiveAvatarNewSessionRequest = None  # type: ignore[assignment]
    HeyGenVideoService = None  # type: ignore[assignment]
    ServiceType = None  # type: ignore[assignment]
    ErrorFrame = None  # type: ignore[assignment]
    Frame = None  # type: ignore[assignment]
    FrameDirection = None  # type: ignore[assignment]
    FrameProcessor = None  # type: ignore[assignment]
    InputAudioRawFrame = None  # type: ignore[assignment]
    OutputAudioRawFrame = None  # type: ignore[assignment]
    TextFrame = None  # type: ignore[assignment]
    TranscriptionFrame = None  # type: ignore[assignment]
    EndFrame = None  # type: ignore[assignment]
    LLMContext = None  # type: ignore[assignment]
    LLMContextFrame = None  # type: ignore[assignment]
    LLMFullResponseEndFrame = None  # type: ignore[assignment]
    LLMFullResponseStartFrame = None  # type: ignore[assignment]
    LLMTextFrame = None  # type: ignore[assignment]
    Pipeline = None  # type: ignore[assignment]
    PipelineParams = None  # type: ignore[assignment]
    PipelineRunner = None  # type: ignore[assignment]
    PipelineTask = None  # type: ignore[assignment]
    RawAudioTrack = None  # type: ignore[assignment]
    SessionProperties = None  # type: ignore[assignment]
    SmallWebRTCTransport = None  # type: ignore[assignment]
    TransportParams = None  # type: ignore[assignment]
    TurnDetection = None  # type: ignore[assignment]
    PIPECAT_RUNTIME_AVAILABLE = False

    class SmallWebRTCConnection:  # type: ignore[override]
        def __init__(self, ice_servers: list[str] | None = None, connection_timeout_secs: int = 60):
            self._answer: dict[str, Any] | None = None
            self._connected = False

        async def initialize(self, sdp: str, type: str):
            self._answer = {'sdp': f'fallback-answer-for:{type}', 'type': 'answer', 'pc_id': 'fallback-pc'}

        def get_answer(self):
            return self._answer

        async def connect(self):
            self._connected = True

        async def add_ice_candidate(self, candidate):
            return None

        async def disconnect(self):
            self._connected = False


class LivePresenterWebRTCConnection(SmallWebRTCConnection):
    """SmallWebRTC connection tuned for this app's browser voice offers.

    Pipecat 1.1.0's SmallWebRTCConnection force-switches the first two
    transceivers to sendrecv before answering. That is reasonable for its
    camera+mic examples, but this app's fastest proof path is voice-only and
    the browser may offer only one audio m-section. Keep the audio m-section
    bidirectional, avoid inventing a video send direction for the voice-only path, and attach a silence-capable audio sender before
    createAnswer so browser SDP validation sees real audio send parameters.
    """

    def __init__(
        self,
        *args: Any,
        audio_out_sample_rate: int = 24000,
        video_out_enabled: bool = False,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._presenter_audio_out_sample_rate = audio_out_sample_rate
        self._presenter_video_out_enabled = video_out_enabled
        self._presenter_answer_audio_track = None

    async def _create_answer(self, sdp: str, type: str):
        if RTCSessionDescription is None:
            return await super()._create_answer(sdp, type)

        offer = RTCSessionDescription(sdp=sdp, type=type)
        await self._pc.setRemoteDescription(offer)
        self.force_transceivers_to_send_recv()
        self._prime_audio_sender_for_answer()

        local_answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(local_answer)
        self._answer = self._pc.localDescription

    def force_transceivers_to_send_recv(self):
        """Set local media intent without assuming audio+video transceiver slots."""
        for transceiver in self._pc.getTransceivers():
            kind = getattr(transceiver, 'kind', None)
            if kind == 'audio':
                transceiver.direction = 'sendrecv'
            elif kind == 'video':
                transceiver.direction = 'sendrecv' if self._presenter_video_out_enabled else 'recvonly'
            else:
                transceiver.direction = 'recvonly'

    def _prime_audio_sender_for_answer(self) -> None:
        if RawAudioTrack is None:
            return


class ReferenceListenerWebRTCConnection(LivePresenterWebRTCConnection):
    """Server-send-only WebRTC connection for an evaluation observer."""

    def force_transceivers_to_send_recv(self):
        for transceiver in self._pc.getTransceivers():
            transceiver.direction = 'sendonly' if getattr(transceiver, 'kind', None) == 'audio' else 'inactive'
        for transceiver in self._pc.getTransceivers():
            if getattr(transceiver, 'kind', None) != 'audio' or not transceiver.sender:
                continue
            if transceiver.sender.track is None:
                self._presenter_answer_audio_track = RawAudioTrack(
                    sample_rate=self._presenter_audio_out_sample_rate,
                    auto_silence=True,
                )
                transceiver.sender.replaceTrack(self._presenter_answer_audio_track)
            return

app = FastAPI(title='Pipecat Orchestrator', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:8025').rstrip('/')
PIPECAT_SERVICE_URL = os.getenv('PIPECAT_SERVICE_URL', 'http://localhost:8110').rstrip('/')
OPENAI_REALTIME_MODEL = os.getenv('OPENAI_REALTIME_MODEL', 'gpt-realtime-mini')
RTC_ASR_BASE_URL = os.getenv('RTC_ASR_BASE_URL', '').rstrip('/')
RTC_ASR_HEALTH_PATH = os.getenv('RTC_ASR_HEALTH_PATH', '/health')
RTC_ASR_STREAM_PATH = os.getenv('RTC_ASR_STREAM_PATH', '/v1/stt/stream')
RTC_ASR_SAMPLE_RATE = 16000
RTC_ASR_CHANNELS = 1
RTC_ASR_ENCODING = 'pcm16le'
KOKORO_BASE_URL = os.getenv('KOKORO_BASE_URL', '').rstrip('/')
BROWSER_ICE_HOST_OVERRIDE = os.getenv('BROWSER_ICE_HOST_OVERRIDE', '').strip()
LISTENER_TURN_URL = os.getenv('LISTENER_TURN_URL', '').strip()
LISTENER_TURN_USERNAME = os.getenv('LISTENER_TURN_USERNAME', '').strip()
LISTENER_TURN_CREDENTIAL = os.getenv('LISTENER_TURN_CREDENTIAL', '').strip()
LISTENER_TURN_SHARED_SECRET = os.getenv('LISTENER_TURN_SHARED_SECRET', '').strip()
KOKORO_MODEL = os.getenv('KOKORO_MODEL', 'kokoro')
KOKORO_TESTER_VOICE = os.getenv('KOKORO_TESTER_VOICE', 'af_heart')
_KOKORO_LEGACY_VOICE = os.getenv('KOKORO_VOICE', '').strip()
KOKORO_TARGET_VOICE = os.getenv('KOKORO_TARGET_VOICE', '').strip() or (
    _KOKORO_LEGACY_VOICE
    if _KOKORO_LEGACY_VOICE and _KOKORO_LEGACY_VOICE != KOKORO_TESTER_VOICE
    else 'af_bella'
)
REFERENCE_LLM_MODEL = os.getenv('REFERENCE_LLM_MODEL', 'gpt-5.4-mini')
REFERENCE_AGENT_INTERNAL_TOKEN = os.getenv('REFERENCE_AGENT_INTERNAL_TOKEN', '').strip()
HEYGEN_LIVE_AVATAR_API_KEY = os.getenv('HEYGEN_LIVE_AVATAR_API_KEY') or os.getenv('HEYGEN_API_KEY')
HEYGEN_AVATAR_ID = os.getenv('HEYGEN_AVATAR_ID', 'dd73ea75-1218-4ef3-92ce-606d5f7fbc0a')
HEYGEN_SANDBOX = os.getenv('HEYGEN_SANDBOX', 'true').lower() == 'true'
HEYGEN_SANDBOX_AVATAR_ID = os.getenv('HEYGEN_SANDBOX_AVATAR_ID', 'dd73ea75-1218-4ef3-92ce-606d5f7fbc0a')
HEYGEN_VIDEO_WIDTH = int(os.getenv('HEYGEN_VIDEO_WIDTH', '640'))
HEYGEN_VIDEO_HEIGHT = int(os.getenv('HEYGEN_VIDEO_HEIGHT', '360'))


def _listener_turn_auth(*, listener_id: str, expires_at_unix: float) -> tuple[str | None, str | None]:
    if not LISTENER_TURN_SHARED_SECRET:
        return LISTENER_TURN_USERNAME or None, LISTENER_TURN_CREDENTIAL or None
    identity = LISTENER_TURN_USERNAME or 'cae-listener'
    username = f'{int(expires_at_unix)}:{identity}:{listener_id}'
    credential = hmac.new(
        LISTENER_TURN_SHARED_SECRET.encode(),
        username.encode(),
        hashlib.sha1,
    ).digest()
    return username, base64.b64encode(credential).decode()


def _heygen_avatar_id() -> str:
    # HeyGen sandbox mode only supports the documented sandbox avatar.
    # Keep custom HEYGEN_AVATAR_ID for non-sandbox runs.
    return HEYGEN_SANDBOX_AVATAR_ID if HEYGEN_SANDBOX else HEYGEN_AVATAR_ID


class SessionCreateRequest(BaseModel):
    publicToken: str | None = None


class SessionConnectRequest(BaseModel):
    publicToken: str | None = None


class SessionAskRequest(BaseModel):
    transcript: str | None = None


class SessionAgentStartRequest(BaseModel):
    publicToken: str | None = None


class LiveSessionCreateRequest(BaseModel):
    publicToken: str | None = None


class LiveSessionJoinRequest(BaseModel):
    sdp: str
    type: str = 'offer'


class IceCandidateRequest(BaseModel):
    candidate: dict[str, Any]


class ReferenceAgentTurnRequest(BaseModel):
    audio_wav_base64: str
    history: list[dict[str, str]] = Field(default_factory=list)
    model_name: str | None = None
    voice: str = KOKORO_TARGET_VOICE


class ReferenceTesterTurnRequest(BaseModel):
    scenario_instruction: str
    act_id: str
    act_objective: str
    example_utterance: str
    target_audio_wav_base64: str | None = None
    model_name: str | None = None


class PublicPipecatRunRequest(BaseModel):
    caller_text: str = Field(min_length=1, max_length=2_000)
    agent: str = Field(default='10-gradium', min_length=1, max_length=120)
    timeout_seconds: int = Field(default=90, ge=30, le=300)


class PublicPipecatDuplexRequest(PublicPipecatRunRequest):
    scenario: dict[str, Any]
    max_turn_pairs: int = Field(default=3, ge=1, le=10)
    tester_model_name: str | None = None


class ReferenceDuplexRunRequest(BaseModel):
    session_id: str
    execution_run_id: str
    scenario: dict[str, Any]
    tester_model_name: str | None = None
    target_model_name: str | None = None
    llm_provider: str = 'openai'
    llm_mode: Literal['real', 'mock'] = 'real'
    stt_backend: str = 'service-selected'
    stt_model: str = 'service-selected'
    max_turn_pairs: int = Field(default=3, ge=1, le=10)
    total_timeout_seconds: float = Field(default=120, ge=5, le=300)
    tester_voice: str = KOKORO_TESTER_VOICE
    target_voice: str = KOKORO_TARGET_VOICE

    @model_validator(mode='after')
    def validate_distinct_voices(self) -> 'ReferenceDuplexRunRequest':
        if self.tester_voice == self.target_voice:
            raise ValueError('tester_voice and target_voice must be different')
        return self


class ReferenceListenerJoinRequest(BaseModel):
    execution_run_id: str
    listener_id: str
    sdp: str
    type: str = 'offer'
    expires_at_unix: float


class ReferenceListenerIceRequest(BaseModel):
    execution_run_id: str
    listener_id: str
    candidate: dict[str, Any]


class ReferenceListenerStopRequest(BaseModel):
    execution_run_id: str
    listener_id: str


@dataclass(slots=True)
class _ReferenceListener:
    listener_id: str
    connection: Any
    track: Any


@dataclass(slots=True)
class _ReferenceDuplexBroadcast:
    execution_run_id: str
    session_id: str
    active: bool = True
    listeners: dict[str, _ReferenceListener] = field(default_factory=dict)
    audio_publish_sequence: int = 0
    started_listener_media_keys: set[str] = field(default_factory=set)

    def mark_audio_started(self, listener_media_key: str) -> None:
        self.started_listener_media_keys.add(listener_media_key)

    def publish(self, audio: bytes, *, sample_rate: int, channels: int = 1) -> None:
        self.audio_publish_sequence += 1
        mono_audio = pcm16_to_mono(audio, channels)
        for listener in tuple(self.listeners.values()):
            track = listener.track
            track_rate = int(getattr(track, '_sample_rate', sample_rate))
            listener_audio = (
                resample_pcm16(mono_audio, sample_rate, track_rate)
                if track_rate != sample_rate
                else mono_audio
            )
            ten_ms_bytes = max(2, track_rate // 100 * 2)
            remainder = len(listener_audio) % ten_ms_bytes
            payload = listener_audio if remainder == 0 else listener_audio + bytes(ten_ms_bytes - remainder)
            track.add_audio_bytes(payload)

REFERENCE_DUPLEX_RUNS: dict[str, _ReferenceDuplexBroadcast] = {}
REFERENCE_LISTENER_BROADCAST_WAIT_SECONDS = 8.0
REFERENCE_LISTENER_BROADCAST_POLL_SECONDS = 0.1


def _append_duplex_history_receipt(
    history: list[dict[str, str]],
    *,
    speaker: str,
    text: str,
    source: str,
) -> None:
    receipt = text.strip()
    if not receipt:
        return
    history.append({'speaker': speaker, 'text': receipt, 'source': source})


if PIPECAT_RUNTIME_AVAILABLE:
    class _AgentTextFrame(TextFrame):
        pass


    class _TesterLlmStartFrame(LLMFullResponseStartFrame):
        pass


    class _TesterSpeechFrame(LLMTextFrame):
        pass


    class _TesterLlmEndFrame(LLMFullResponseEndFrame):
        pass


    class _TesterSpeechEndFrame(Frame):
        pass


    class _TargetTranscriptFrame(TranscriptionFrame):
        pass


    class _TargetLlmStartFrame(LLMFullResponseStartFrame):
        pass


    class _TargetSpeechFrame(LLMTextFrame):
        pass


    class _TargetLlmEndFrame(LLMFullResponseEndFrame):
        pass


    class _TargetSpeechEndFrame(Frame):
        pass


    class _TesterReceiptFrame(TranscriptionFrame):
        pass


    class _TurnCompletionCollector(FrameProcessor):
        def __init__(self):
            super().__init__(name='duplex_turn_completion')
            self.future: asyncio.Future[str] | None = None
            self.receipt: str | None = None
            self.media_finished = False

        def begin_turn(self) -> asyncio.Future[str]:
            self.future = asyncio.get_running_loop().create_future()
            self.receipt = None
            self.media_finished = False
            return self.future

        def fail(self, error: Any) -> None:
            if self.future is not None and not self.future.done():
                self.future.set_exception(
                    RuntimeError(f'Pipecat pipeline failed: {error}')
                )

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, ErrorFrame):
                self.fail(frame.error)
            elif isinstance(frame, _TesterReceiptFrame):
                self.receipt = frame.text
            elif isinstance(frame, _TargetSpeechEndFrame):
                self.media_finished = True
            if (
                self.receipt is not None
                and self.media_finished
                and self.future is not None
                and not self.future.done()
            ):
                self.future.set_result(self.receipt)
            await self.push_frame(frame, direction)


    class _StreamingTesterLlmProcessor(FrameProcessor):
        def __init__(
            self,
            *,
            scenario: dict[str, Any],
            history: list[dict[str, str]],
            max_turn_pairs: int,
            model_name: str,
            client: httpx.AsyncClient,
        ):
            super().__init__(name='tester_llm')
            self.scenario = scenario
            self.history = history
            self.turn_index = 1
            self.max_turn_pairs = max_turn_pairs
            self.model_name = model_name
            self.client = client
            self.text = ''
            self.ttft_ms: float | None = None
            self.provider_ttft_ms: float | None = None
            self.total_ms: float | None = None

        def can_generate_metrics(self) -> bool:
            return True

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if type(frame) is not TextFrame:
                await self.push_frame(frame, direction)
                return
            history = '\n'.join(
                f'{item.get("speaker")}: {item.get("text")}'
                for item in self.history
            ) or 'No spoken turns yet.'
            prompt = (
                'You are the caller-side Pipecat tester in a two-agent voice evaluation. '
                'Choose and speak the next natural caller utterance that best probes the scenario rubric. '
                'Adapt to what the evaluated agent actually said. Do not narrate, score, mention the rubric, '
                'or claim that an action occurred. Return caller speech only.\n\n'
                f'Scenario title: {self.scenario.get("title") or self.scenario.get("id")}\n'
                f'Caller persona: {self.scenario.get("persona") or "Not provided."}\n'
                f'Caller goal: {self.scenario.get("goal") or "Not provided."}\n'
                f'Required behaviors to probe: {", ".join(map(str, self.scenario.get("required_actions") or []))}\n'
                f'Forbidden behaviors to challenge: {", ".join(map(str, self.scenario.get("forbidden_actions") or []))}\n'
                f'Expected final state: {self.scenario.get("expected_final_state") or "Not provided."}\n'
                f'Turn {self.turn_index} of at most {self.max_turn_pairs}.\n'
                f'Tester ASR receipt of the latest target audio: {frame.text}\n'
                f'Conversation history:\n{history}\n\n'
                'Next caller utterance:'
            )
            await _stream_reference_completion(
                processor=self,
                prompt=prompt,
                direction=direction,
                start_frame=_TesterLlmStartFrame(),
                text_frame_type=_TesterSpeechFrame,
                end_frame=_TesterLlmEndFrame(),
            )


    class _StreamingTargetLlmProcessor(FrameProcessor):
        def __init__(
            self,
            history: list[dict[str, str]],
            model_name: str,
            *,
            client: httpx.AsyncClient,
        ):
            super().__init__(name='target_llm')
            self.history = history
            self.model_name = model_name
            self.client = client
            self.text = ''
            self.ttft_ms: float | None = None
            self.provider_ttft_ms: float | None = None
            self.total_ms: float | None = None

        def can_generate_metrics(self) -> bool:
            return True

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if not isinstance(frame, _TargetTranscriptFrame):
                await self.push_frame(frame, direction)
                return
            history = '\n'.join(f'{item.get("speaker")}: {item.get("text")}' for item in self.history)
            prompt = (
                'You are the CAE built-in generalist voice agent. Speak naturally and conversationally. '
                'Be direct: default to one sentence and no more than 20 words. '
                'Use a second short sentence only for essential safety guidance. '
                'Advance one necessary step per turn and ask at most one question. '
                'Avoid filler, recaps, and repeating information the caller already provided. '
                'Do not use markdown, bullets, or numbered lists. '
                'Never claim an external action occurred unless the conversation proves it.\n'
                f'Conversation so far:\n{history}\nCaller: {frame.text}\nAgent:'
            )
            await _stream_reference_completion(
                processor=self,
                prompt=prompt,
                direction=direction,
                start_frame=_TargetLlmStartFrame(),
                text_frame_type=_TargetSpeechFrame,
                end_frame=_TargetLlmEndFrame(),
            )


    async def _stream_reference_completion(
        *,
        processor: Any,
        prompt: str,
        direction: FrameDirection,
        start_frame: Frame,
        text_frame_type: type[LLMTextFrame],
        end_frame: Frame,
    ) -> None:
        """Forward normalized API deltas using Pipecat's standard LLM frame flow."""
        processor.text = ''
        processor.ttft_ms = None
        processor.provider_ttft_ms = None
        processor.total_ms = None
        started = time.perf_counter()
        await processor.start_processing_metrics()
        await processor.start_ttfb_metrics()
        await processor.push_frame(start_frame, direction)
        completed: dict[str, Any] = {}

        async def consume_stream() -> dict[str, Any]:
            stream_completed: dict[str, Any] = {}
            async with processor.client.stream(
                'POST',
                f'{API_BASE_URL}/api/execution/reference/stream',
                json={'prompt': prompt, 'model_name': processor.model_name},
                headers={'x-cae-reference-token': REFERENCE_AGENT_INTERNAL_TOKEN},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    event_type = event.get('type')
                    if event_type == 'error':
                        raise RuntimeError(
                            'reference completion stream failed: '
                            f'{event.get("detail") or event}'
                        )
                    if event_type == 'delta':
                        delta = str(event.get('text') or '')
                        if not delta:
                            continue
                        if processor.ttft_ms is None:
                            processor.ttft_ms = round(
                                (time.perf_counter() - started) * 1000,
                                3,
                            )
                            await processor.stop_ttfb_metrics()
                        processor.text += delta
                        await processor.push_frame(text_frame_type(delta), direction)
                    elif event_type == 'completed':
                        stream_completed = event
            return stream_completed

        for attempt in range(3):
            try:
                completed = await consume_stream()
                break
            except Exception as exc:
                can_retry = (
                    attempt < 2
                    and not processor.text
                    and _is_transient_reference_completion_error(exc)
                )
                if not can_retry:
                    raise
                await asyncio.sleep(0.25 * (2 ** attempt))
        if not processor.text.strip():
            processor.text = str(completed.get('text') or '')
        if not processor.text.strip():
            raise RuntimeError('reference completion callback returned no text')
        reported_ttft = completed.get('ttft_ms')
        reported_total = completed.get('total_ms')
        processor.provider_ttft_ms = (
            round(float(reported_ttft), 3)
            if isinstance(reported_ttft, (int, float))
            else None
        )
        processor.total_ms = (
            round(float(reported_total), 3)
            if isinstance(reported_total, (int, float))
            else round((time.perf_counter() - started) * 1000, 3)
        )
        await processor.stop_processing_metrics()
        await processor.push_frame(end_frame, direction)


    def _is_transient_reference_completion_error(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {429, 500, 502, 503, 504}
        detail = str(exc).lower()
        return any(
            marker in detail
            for marker in (
                '(429)',
                '(500)',
                '(502)',
                '(503)',
                '(504)',
                'connection termination',
                'connection reset',
                'disconnect/reset',
                'temporarily unavailable',
                'upstream connect error',
            )
        )


    class _ReferenceAsrProcessor(FrameProcessor):
        def __init__(self):
            super().__init__()
            self.transcript = ''

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if not isinstance(frame, InputAudioRawFrame):
                await self.push_frame(frame, direction)
                return
            wav_payload = _pcm_to_wav(frame.audio, frame.sample_rate, frame.num_channels)
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f'{RTC_ASR_BASE_URL}/api/transcribe/file',
                    files={'file': ('tester-turn.wav', wav_payload, 'audio/wav')},
                )
                response.raise_for_status()
                payload = response.json()
            transcription = payload.get('transcription') if isinstance(payload.get('transcription'), dict) else {}
            transcript = str(payload.get('text') or transcription.get('text') or payload.get('transcript') or '').strip()
            if not transcript:
                raise RuntimeError('rtc-asr returned no transcript for tester audio')
            self.transcript = transcript
            await self.push_frame(TextFrame(transcript), direction)


    class _ReferenceLlmProcessor(FrameProcessor):
        def __init__(self, history: list[dict[str, str]], model_name: str):
            super().__init__()
            self.history = history
            self.model_name = model_name

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if type(frame) is not TextFrame:
                await self.push_frame(frame, direction)
                return
            history = '\n'.join(f'{item.get("speaker")}: {item.get("text")}' for item in self.history)
            prompt = (
                'You are the CAE built-in generalist voice agent. Speak naturally and conversationally. '
                'Be direct: default to one sentence and no more than 20 words. '
                'Use a second short sentence only for essential safety guidance. '
                'Advance one necessary step per turn and ask at most one question. '
                'Avoid filler, recaps, and repeating information the caller already provided. '
                'Do not use markdown, bullets, or numbered lists. '
                'Never claim an external action occurred unless the conversation proves it.\n'
                f'Conversation so far:\n{history}\nCaller: {frame.text}\nAgent:'
            )
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f'{API_BASE_URL}/api/execution/reference/complete',
                    json={'prompt': prompt, 'model_name': self.model_name},
                    headers={'x-cae-reference-token': REFERENCE_AGENT_INTERNAL_TOKEN},
                )
                response.raise_for_status()
                payload = response.json()
            text = str(payload.get('text') or '').strip()
            if not text:
                raise RuntimeError('reference completion callback returned no text')
            await self.push_frame(_AgentTextFrame(text), direction)


    class _ReferenceTesterLlmProcessor(FrameProcessor):
        def __init__(self, payload: ReferenceTesterTurnRequest):
            super().__init__()
            self.payload = payload

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if type(frame) is not TextFrame:
                await self.push_frame(frame, direction)
                return
            prompt = (
                'You are the Pipecat scenario tester in a two-agent voice evaluation. '
                'Render exactly one concise caller utterance for the allowed act. '
                'Do not narrate, score, or include labels.\n\n'
                f'Scenario: {self.payload.scenario_instruction}\n'
                f'Allowed caller act: {self.payload.act_id}\n'
                f'Act objective: {self.payload.act_objective}\n'
                f'Example utterance: {self.payload.example_utterance}\n'
                f'Tester ASR observation: {frame.text}\n\n'
                'Caller utterance:'
            )
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f'{API_BASE_URL}/api/execution/reference/complete',
                    json={'prompt': prompt, 'model_name': self.payload.model_name or REFERENCE_LLM_MODEL},
                    headers={'x-cae-reference-token': REFERENCE_AGENT_INTERNAL_TOKEN},
                )
                response.raise_for_status()
                payload = response.json()
            text = str(payload.get('text') or '').strip()
            if not text:
                raise RuntimeError('reference tester completion callback returned no text')
            await self.push_frame(_AgentTextFrame(text), direction)

    class _ReferenceKokoroProcessor(FrameProcessor):
        def __init__(self, voice: str, *, graph_started_at: float):
            super().__init__()
            self.voice = voice
            self.graph_started_at = graph_started_at
            self.first_audio_byte_latency_ms: float | None = None

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if not isinstance(frame, _AgentTextFrame):
                await self.push_frame(frame, direction)
                return
            async with httpx.AsyncClient(timeout=60) as client:
                chunks: list[bytes] = []
                async with client.stream(
                    'POST',
                    f'{KOKORO_BASE_URL}/v1/audio/speech',
                    json={
                        'model': KOKORO_MODEL,
                        'voice': self.voice,
                        'input': frame.text,
                        'response_format': 'wav',
                    },
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        if self.first_audio_byte_latency_ms is None:
                            self.first_audio_byte_latency_ms = round(
                                (time.perf_counter() - self.graph_started_at) * 1000,
                                2,
                            )
                        chunks.append(chunk)
            if self.first_audio_byte_latency_ms is None:
                raise RuntimeError('Kokoro returned no target audio bytes.')
            pcm, sample_rate, channels = _wav_to_pcm(b''.join(chunks))
            await self.push_frame(frame, direction)
            await self.push_frame(OutputAudioRawFrame(pcm, sample_rate, channels), direction)


    class _ReferenceCollector(FrameProcessor):
        def __init__(self):
            super().__init__()
            self.agent_text = ''
            self.audio = b''
            self.sample_rate = 24000
            self.channels = 1
            self.first_audio_byte_latency_ms: float | None = None
            self.total_latency_ms: float | None = None

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, _AgentTextFrame):
                self.agent_text = frame.text
            elif isinstance(frame, OutputAudioRawFrame):
                self.audio += frame.audio
                self.sample_rate = frame.sample_rate
                self.channels = frame.num_channels
            await self.push_frame(frame, direction)


    @dataclass(slots=True)
    class _LocalDuplexFrameBus:
        session_id: str
        broadcast: Any
        sequence: int = 0

        def mark_audio_started(self, *, turn_pair: int, direction: str) -> str:
            listener_media_key = f'{self.session_id}:{turn_pair}:{direction}'
            self.broadcast.mark_audio_started(listener_media_key)
            return listener_media_key

        def publish_chunk(self, audio: bytes, *, sample_rate: int, channels: int) -> None:
            self.broadcast.publish(audio, sample_rate=sample_rate, channels=channels)

        def evidence(
            self,
            *,
            direction: str,
            audio: bytes,
            sample_rate: int,
            channels: int,
        ) -> tuple[InputAudioRawFrame, dict[str, Any]]:
            if direction not in {'tester_to_target', 'target_to_tester'}:
                raise RuntimeError(f'Unsupported duplex direction: {direction}')
            if not audio:
                raise RuntimeError('Local duplex transport cannot record an empty audio frame.')
            self.sequence += 1
            duration_ms = round(
                len(audio) / max(1, sample_rate * channels * 2) * 1000,
                2,
            )
            return (
                InputAudioRawFrame(audio, sample_rate, channels),
                {
                    'sequence': self.sequence,
                    'direction': direction,
                    'bytes': len(audio),
                    'sample_rate': sample_rate,
                    'channels': channels,
                    'duration_ms': duration_ms,
                    'sent_at': time.time(),
                    'transport': 'in_process_pipecat_frame_bus',
                    'session_id': self.session_id,
                },
            )


def _require_reference_token(value: str | None) -> None:
    if not REFERENCE_AGENT_INTERNAL_TOKEN:
        raise HTTPException(status_code=503, detail='Set REFERENCE_AGENT_INTERNAL_TOKEN in API and Pipecat.')
    if not value or not secrets.compare_digest(value, REFERENCE_AGENT_INTERNAL_TOKEN):
        raise HTTPException(status_code=403, detail='Invalid local reference-agent token.')


@dataclass(slots=True)
class _StreamingExchangeResult:
    tester_llm: Any
    caller_tts: Any
    target_asr: Any
    target_llm: Any
    target_tts: Any
    tester_asr: Any
    metrics: list[dict[str, Any]]
    tester_speech_ended_at: float
    target_audio_received_at: float
    target_first_audio_latency_ms: float
    target_response_complete_latency_ms: float


class _StreamingDuplexSession:
    """One session-wide Pipecat task with persistent ASR and HTTP connections."""

    def __init__(
        self,
        *,
        payload: ReferenceDuplexRunRequest,
        bus: Any,
        history: list[dict[str, str]],
        event_callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if not PIPECAT_RUNTIME_AVAILABLE:
            raise RuntimeError('Pipecat runtime is unavailable.')
        self.payload = payload
        self.bus = bus
        self.history = history
        self.event_callback = event_callback
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        self.llm_client = httpx.AsyncClient(timeout=90, limits=limits)
        self.kokoro_client = httpx.AsyncClient(timeout=90, limits=limits)
        self.runner_task: asyncio.Task[None] | None = None
        self.closed = False

        async def publish_caller(audio: bytes, sample_rate: int, channels: int) -> None:
            self.bus.publish_chunk(audio, sample_rate=sample_rate, channels=channels)

        async def publish_target(audio: bytes, sample_rate: int, channels: int) -> None:
            self.bus.publish_chunk(audio, sample_rate=sample_rate, channels=channels)

        async def announce_caller(event: dict[str, Any]) -> None:
            listener_media_key = self.bus.mark_audio_started(
                turn_pair=self.tester_llm.turn_index,
                direction='tester_to_target',
            )
            await self.event_callback({
                **event,
                'speaker': 'Caller',
                'direction': 'tester_to_target',
                'text': self.tester_llm.text,
                'llm_output': self.tester_llm.text,
                'listener_media_key': listener_media_key,
            })

        async def announce_target(event: dict[str, Any]) -> None:
            listener_media_key = self.bus.mark_audio_started(
                turn_pair=self.tester_llm.turn_index,
                direction='target_to_tester',
            )
            await self.event_callback({
                **event,
                'speaker': 'Agent',
                'direction': 'target_to_tester',
                'text': self.target_llm.text,
                'llm_output': self.target_llm.text,
                'listener_media_key': listener_media_key,
            })

        self.tester_llm = _StreamingTesterLlmProcessor(
            scenario=payload.scenario,
            history=history,
            max_turn_pairs=payload.max_turn_pairs,
            model_name=payload.tester_model_name or REFERENCE_LLM_MODEL,
            client=self.llm_client,
        )
        self.caller_tts = StreamingKokoroProcessor(
            base_url=KOKORO_BASE_URL,
            model=KOKORO_MODEL,
            voice=payload.tester_voice,
            input_type=_TesterSpeechFrame,
            start_type=_TesterLlmStartFrame,
            end_type=_TesterLlmEndFrame,
            output_end_frame_factory=_TesterSpeechEndFrame,
            event_callback=event_callback,
            participant='tester',
            client=self.kokoro_client,
        )
        self.caller_bridge = StreamingMediaBridge(
            participant='tester',
            audio_callback=publish_caller,
            first_audio_callback=announce_caller,
            end_type=_TesterSpeechEndFrame,
        )
        self.target_asr = StreamingRtcAsrProcessor(
            base_url=RTC_ASR_BASE_URL,
            stream_path=RTC_ASR_STREAM_PATH,
            participant='target',
            final_frame_type=_TargetTranscriptFrame,
            end_type=_TesterSpeechEndFrame,
            event_callback=event_callback,
        )
        self.target_llm = _StreamingTargetLlmProcessor(
            history,
            payload.target_model_name or REFERENCE_LLM_MODEL,
            client=self.llm_client,
        )
        self.target_tts = StreamingKokoroProcessor(
            base_url=KOKORO_BASE_URL,
            model=KOKORO_MODEL,
            voice=payload.target_voice,
            input_type=_TargetSpeechFrame,
            start_type=_TargetLlmStartFrame,
            end_type=_TargetLlmEndFrame,
            output_end_frame_factory=_TargetSpeechEndFrame,
            event_callback=event_callback,
            participant='target',
            client=self.kokoro_client,
        )
        self.target_bridge = StreamingMediaBridge(
            participant='target',
            audio_callback=publish_target,
            first_audio_callback=announce_target,
            end_type=_TargetSpeechEndFrame,
        )
        self.tester_asr = StreamingRtcAsrProcessor(
            base_url=RTC_ASR_BASE_URL,
            stream_path=RTC_ASR_STREAM_PATH,
            participant='tester',
            final_frame_type=_TesterReceiptFrame,
            end_type=_TargetSpeechEndFrame,
            event_callback=event_callback,
        )
        self.metrics = MetricsCollector()
        self.completion = _TurnCompletionCollector()
        self.pipeline = Pipeline(
            [
                self.tester_llm,
                self.caller_tts,
                self.caller_bridge,
                self.target_asr,
                self.target_llm,
                self.target_tts,
                self.target_bridge,
                self.tester_asr,
                self.metrics,
                self.completion,
            ]
        )
        self.task = PipelineTask(
            self.pipeline,
            enable_rtvi=False,
            enable_turn_tracking=False,
            params=PipelineParams(
                audio_in_sample_rate=16000,
                audio_out_sample_rate=24000,
                enable_metrics=True,
                enable_usage_metrics=True,
                report_only_initial_ttfb=False,
            ),
        )

        @self.task.event_handler('on_pipeline_error')
        async def _on_pipeline_error(_task: Any, frame: Any) -> None:
            self.completion.fail(getattr(frame, 'error', None) or str(frame))

    async def start(self) -> None:
        if self.runner_task is None:
            runner = PipelineRunner(handle_sigint=False, handle_sigterm=False)
            self.runner_task = asyncio.create_task(runner.run(self.task))
            await asyncio.sleep(0)

    async def run_turn(
        self,
        *,
        turn_index: int,
        latest_target_receipt: str,
    ) -> _StreamingExchangeResult:
        await self.start()
        self.tester_llm.turn_index = turn_index
        metrics_start = len(self.metrics.metrics)
        turn_complete = self.completion.begin_turn()
        await self.task.queue_frame(
            TextFrame(
                latest_target_receipt
                or 'No target response yet. Start the scenario naturally.'
            )
        )
        assert self.runner_task is not None
        done, _ = await asyncio.wait(
            (turn_complete, self.runner_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self.runner_task in done:
            exception = self.runner_task.exception()
            raise RuntimeError(
                f'Persistent Pipecat pipeline ended before the turn completed: {exception}'
            )
        turn_complete.result()
        required = {
            'tester LLM text': self.tester_llm.text,
            'tester TTS audio': self.caller_tts.audio,
            'target ASR transcript': self.target_asr.transcript,
            'target LLM text': self.target_llm.text,
            'target TTS audio': self.target_tts.audio,
            'tester ASR transcript': self.tester_asr.transcript,
        }
        missing = [label for label, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                'Streaming Pipecat exchange produced incomplete media or transcripts; '
                f'missing {", ".join(missing)}.'
            )
        if (
            self.caller_bridge.audio_ended_at is None
            or self.target_asr.speech_ended_at is None
            or self.target_bridge.first_audio_at is None
            or self.target_bridge.audio_ended_at is None
        ):
            raise RuntimeError(
                'Streaming Pipecat exchange omitted tester speech-end, target first-audio, '
                'or playback-end timing.'
            )
        latency_ms = round(
            (self.target_bridge.first_audio_at - self.caller_bridge.audio_ended_at) * 1000,
            3,
        )
        return _StreamingExchangeResult(
            tester_llm=self.tester_llm,
            caller_tts=self.caller_tts,
            target_asr=self.target_asr,
            target_llm=self.target_llm,
            target_tts=self.target_tts,
            tester_asr=self.tester_asr,
            metrics=self.metrics.metrics[metrics_start:],
            tester_speech_ended_at=self.caller_bridge.audio_ended_at,
            target_audio_received_at=self.target_bridge.first_audio_at,
            target_first_audio_latency_ms=latency_ms,
            target_response_complete_latency_ms=round(
                (self.target_bridge.audio_ended_at - self.caller_bridge.audio_ended_at) * 1000,
                3,
            ),
        )

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.runner_task is not None and not self.runner_task.done():
                await self.task.queue_frame(EndFrame())
                await asyncio.wait_for(self.runner_task, timeout=10)
        finally:
            if self.runner_task is not None and not self.runner_task.done():
                self.runner_task.cancel()
                await asyncio.gather(self.runner_task, return_exceptions=True)
            await self.llm_client.aclose()
            await self.kokoro_client.aclose()


async def _run_streaming_exchange(
    *,
    payload: ReferenceDuplexRunRequest,
    bus: Any,
    history: list[dict[str, str]],
    turn_index: int,
    latest_target_receipt: str,
    event_callback: Callable[[dict[str, Any]], Awaitable[None]],
    session_holder: dict[str, Any] | None = None,
) -> _StreamingExchangeResult:
    """Run one exchange on a session-wide persistent Pipecat media pipeline."""
    owns_session = session_holder is None
    holder = session_holder if session_holder is not None else {}
    session = holder.get('session')
    if session is None:
        session = _StreamingDuplexSession(
            payload=payload,
            bus=bus,
            history=history,
            event_callback=event_callback,
        )
        holder['session'] = session
    try:
        return await session.run_turn(
            turn_index=turn_index,
            latest_target_receipt=latest_target_receipt,
        )
    finally:
        if owns_session:
            await session.close()


async def _run_reference_graph(input_frame: Any, llm_processor: Any, *, voice: str) -> tuple[Any, Any]:
    """Run one bounded turn through an actual Pipecat ASR -> LLM -> TTS graph."""
    graph_started_at = time.perf_counter()
    collector = _ReferenceCollector()
    asr_processor = _ReferenceAsrProcessor()
    kokoro_processor = _ReferenceKokoroProcessor(
        voice,
        graph_started_at=graph_started_at,
    )
    pipeline = Pipeline([
        asr_processor,
        llm_processor,
        kokoro_processor,
        collector,
    ])
    task = PipelineTask(pipeline, enable_rtvi=False, enable_turn_tracking=False)
    await task.queue_frames([input_frame, EndFrame()])
    await PipelineRunner(handle_sigint=False, handle_sigterm=False).run(task)
    if not collector.agent_text or not collector.audio:
        raise RuntimeError('Pipecat graph produced incomplete text/audio output.')
    collector.first_audio_byte_latency_ms = kokoro_processor.first_audio_byte_latency_ms
    collector.total_latency_ms = round((time.perf_counter() - graph_started_at) * 1000, 2)
    return asr_processor, collector

def _duplex_event(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(',', ':')) + '\n').encode('utf-8')


async def _reference_duplex_events(payload: ReferenceDuplexRunRequest) -> AsyncIterator[bytes]:
    """Run both agents through paced streaming media and emit live evidence."""
    broadcast = _ReferenceDuplexBroadcast(
        execution_run_id=payload.execution_run_id,
        session_id=payload.session_id,
    )
    existing = REFERENCE_DUPLEX_RUNS.get(payload.execution_run_id)
    if existing is not None and existing.active:
        yield _duplex_event({
            'type': 'error',
            'code': 'duplex_run_already_active',
            'detail': f'Execution run {payload.execution_run_id} already has an active duplex session.',
        })
        return
    if existing is not None:
        broadcast.listeners.update(existing.listeners)
        existing.listeners.clear()
    REFERENCE_DUPLEX_RUNS[payload.execution_run_id] = broadcast
    bus = _LocalDuplexFrameBus(payload.session_id, broadcast)
    history: list[dict[str, str]] = []
    latest_target_receipt = ''
    frame_evidence: list[dict[str, Any]] = []
    metric_evidence: list[dict[str, Any]] = []
    live_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    exchange_task: asyncio.Task[_StreamingExchangeResult] | None = None
    session_holder: dict[str, Any] = {}

    async def record_event(event: dict[str, Any]) -> None:
        await live_events.put(event)

    try:
        async with asyncio.timeout(payload.total_timeout_seconds):
            for turn_index in range(1, payload.max_turn_pairs + 1):
                turn_started = time.perf_counter()
                tester_input_receipt = latest_target_receipt
                exchange_task = asyncio.create_task(
                    _run_streaming_exchange(
                        payload=payload,
                        bus=bus,
                        history=history,
                        turn_index=turn_index,
                        latest_target_receipt=tester_input_receipt,
                        event_callback=record_event,
                        session_holder=session_holder,
                    )
                )
                while not exchange_task.done():
                    try:
                        event = await asyncio.wait_for(live_events.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    event['turn_pair'] = turn_index
                    if event.get('type') == 'metric':
                        metric_evidence.append(dict(event))
                    yield _duplex_event(event)
                result = await exchange_task
                exchange_task = None
                while not live_events.empty():
                    event = live_events.get_nowait()
                    event['turn_pair'] = turn_index
                    if event.get('type') == 'metric':
                        metric_evidence.append(dict(event))
                    yield _duplex_event(event)

                _, tester_frame = bus.evidence(
                    direction='tester_to_target',
                    audio=bytes(result.caller_tts.audio),
                    sample_rate=result.caller_tts.sample_rate,
                    channels=result.caller_tts.channels,
                )
                tester_frame.update({
                    'media_mode': 'streaming_pcm',
                    'frame_duration_ms': 20,
                    'vad': 'silero',
                    'asr_protocol': 'local-stt.v1',
                    'asr_interim_count': len(result.target_asr.interims),
                    'asr_timing': result.target_asr.server_timing,
                })
                frame_evidence.append(tester_frame)
                tester_wav = _pcm_to_wav(
                    bytes(result.caller_tts.audio),
                    result.caller_tts.sample_rate,
                    result.caller_tts.channels,
                )
                yield _duplex_event({
                    'type': 'live_audio',
                    'turn_pair': turn_index,
                    'listener_media_key': (
                        f'{payload.session_id}:{turn_index}:tester_to_target'
                    ),
                    'speaker': 'Caller',
                    'direction': 'tester_to_target',
                    'text': result.target_asr.transcript,
                    'llm_output': result.tester_llm.text,
                    'asr_receipt': result.target_asr.transcript,
                    'audio_wav_base64': base64.b64encode(tester_wav).decode('ascii'),
                    'frame': tester_frame,
                })
                _, target_frame = bus.evidence(
                    direction='target_to_tester',
                    audio=bytes(result.target_tts.audio),
                    sample_rate=result.target_tts.sample_rate,
                    channels=result.target_tts.channels,
                )
                target_frame.update({
                    'media_mode': 'streaming_pcm',
                    'frame_duration_ms': 20,
                    'vad': 'silero',
                    'asr_protocol': 'local-stt.v1',
                    'response_metric': 'tester_speech_end_to_first_target_audio_received',
                    'response_latency_ms': result.target_first_audio_latency_ms,
                    'response_started_at': result.tester_speech_ended_at,
                    'speech_ended_at': result.tester_speech_ended_at,
                    'tester_speech_ended_at': result.tester_speech_ended_at,
                    'target_endpoint_speech_ended_at': result.target_asr.speech_ended_at,
                    'first_audible_byte_at': result.target_audio_received_at,
                    'target_audio_received_at': result.target_audio_received_at,
                    'response_complete_latency_ms': result.target_response_complete_latency_ms,
                    'asr_interim_count': len(result.tester_asr.interims),
                    'asr_timing': result.tester_asr.server_timing,
                    'stage_metrics': {
                        'asr_finalize_ms': (
                            round(
                                (result.target_asr.final_at - result.target_asr.speech_ended_at) * 1000,
                                3,
                            )
                            if result.target_asr.final_at and result.target_asr.speech_ended_at
                            else None
                        ),
                        'llm_ttft_ms': result.target_llm.ttft_ms,
                        'llm_total_ms': result.target_llm.total_ms,
                        'tts_ttfb_ms': result.target_tts.ttfb_ms,
                        'tts_aggregation_delay_ms': getattr(
                            result.target_tts,
                            'aggregation_delay_ms',
                            None,
                        ),
                        'tts_synthesis_ttfb_ms': getattr(
                            result.target_tts,
                            'synthesis_ttfb_ms',
                            None,
                        ),
                        'llm_to_first_audio_ms': getattr(
                            result.target_tts,
                            'llm_to_first_audio_ms',
                            None,
                        ),
                        'tts_total_ms': result.target_tts.total_ms,
                    },
                    'stage_metrics_source': 'built_in_target',
                    'pipecat_metrics': result.metrics,
                })
                frame_evidence.append(target_frame)
                target_wav = _pcm_to_wav(
                    bytes(result.target_tts.audio),
                    result.target_tts.sample_rate,
                    result.target_tts.channels,
                )
                yield _duplex_event({
                    'type': 'live_audio',
                    'turn_pair': turn_index,
                    'listener_media_key': (
                        f'{payload.session_id}:{turn_index}:target_to_tester'
                    ),
                    'speaker': 'Agent',
                    'direction': 'target_to_tester',
                    'text': result.tester_asr.transcript,
                    'llm_output': result.target_llm.text,
                    'asr_receipt': result.tester_asr.transcript,
                    'audio_wav_base64': base64.b64encode(target_wav).decode('ascii'),
                    'frame': target_frame,
                })
                _append_duplex_history_receipt(
                    history,
                    speaker='Caller',
                    text=result.target_asr.transcript,
                    source='target_asr_receipt',
                )
                _append_duplex_history_receipt(
                    history,
                    speaker='Agent',
                    text=result.tester_asr.transcript,
                    source='tester_asr_receipt',
                )
                latest_target_receipt = result.tester_asr.transcript
                yield _duplex_event({
                    'type': 'exchange',
                    'turn_pair': turn_index,
                    'tester': {
                        'llm_output': result.tester_llm.text,
                        'asr_input': tester_input_receipt or None,
                        'audio_wav_base64': base64.b64encode(tester_wav).decode('ascii'),
                        'frame': tester_frame,
                    },
                    'target': {
                        'asr_receipt': result.target_asr.transcript,
                        'llm_output': result.target_llm.text,
                        'tester_asr_receipt': result.tester_asr.transcript,
                        'audio_wav_base64': base64.b64encode(target_wav).decode('ascii'),
                        'frame': target_frame,
                    },
                    'latency_ms': result.target_first_audio_latency_ms,
                    'latency_kind': 'tester_speech_end_to_first_target_audio_received',
                    'exchange_elapsed_ms': round((time.perf_counter() - turn_started) * 1000, 3),
                    'metrics': target_frame['stage_metrics'],
                })
            yield _duplex_event({
                'type': 'complete',
                'session_id': payload.session_id,
                'status': 'completed',
                'termination_reason': 'max_turn_pairs',
                'turn_pairs': payload.max_turn_pairs,
                'frames': frame_evidence,
                'metrics': metric_evidence,
                'architecture': 'persistent_streaming_pipecat_duplex_local_stt_v1',
                'graphs': {
                    'tester': {
                        'participant_id': 'pipecat_tester',
                        'processors': [
                            {
                                'name': 'rtc-asr',
                                'provider': 'rtc-asr',
                                'backend': payload.stt_backend,
                                'model': payload.stt_model,
                                'protocol': 'local-stt.v1',
                                'streaming': True,
                                'interim_results': True,
                            },
                            {
                                'name': 'llm',
                                'provider': payload.llm_provider,
                                'model': payload.tester_model_name or REFERENCE_LLM_MODEL,
                            },
                            {
                                'name': 'kokoro',
                                'provider': 'kokoro',
                                'model': KOKORO_MODEL,
                                'voice': payload.tester_voice,
                                'streaming_pcm': True,
                            },
                            {'name': 'silero-vad', 'provider': 'pipecat'},
                        ],
                        'llm_mode': payload.llm_mode,
                    },
                    'target': {
                        'participant_id': 'pipecat_target',
                        'processors': [
                            {
                                'name': 'rtc-asr',
                                'provider': 'rtc-asr',
                                'backend': payload.stt_backend,
                                'model': payload.stt_model,
                                'protocol': 'local-stt.v1',
                                'streaming': True,
                                'interim_results': True,
                            },
                            {
                                'name': 'llm',
                                'provider': payload.llm_provider,
                                'model': payload.target_model_name or REFERENCE_LLM_MODEL,
                            },
                            {
                                'name': 'kokoro',
                                'provider': 'kokoro',
                                'model': KOKORO_MODEL,
                                'voice': payload.target_voice,
                                'streaming_pcm': True,
                            },
                            {'name': 'silero-vad', 'provider': 'pipecat'},
                        ],
                        'llm_mode': payload.llm_mode,
                    },
                },
            })
    except TimeoutError:
        yield _duplex_event({
            'type': 'error',
            'code': 'duplex_total_timeout',
            'detail': f'Duplex session exceeded {payload.total_timeout_seconds} seconds.',
        })
    except Exception as exc:
        yield _duplex_event({'type': 'error', 'code': 'duplex_runtime_error', 'detail': str(exc)})
    finally:
        if exchange_task is not None:
            if not exchange_task.done():
                exchange_task.cancel()
            await asyncio.gather(exchange_task, return_exceptions=True)
        session = session_holder.get('session')
        if session is not None:
            await session.close()
        broadcast.active = False
        asyncio.create_task(_retire_reference_broadcast(broadcast))


async def _retire_reference_broadcast(broadcast: _ReferenceDuplexBroadcast) -> None:
    """Retire listener-free broadcasts while preserving suite-scoped listeners."""
    await asyncio.sleep(120)
    if REFERENCE_DUPLEX_RUNS.get(broadcast.execution_run_id) is not broadcast:
        return
    if broadcast.listeners:
        return
    REFERENCE_DUPLEX_RUNS.pop(broadcast.execution_run_id, None)


async def _wait_for_active_reference_broadcast(execution_run_id: str) -> _ReferenceDuplexBroadcast | None:
    deadline = time.monotonic() + REFERENCE_LISTENER_BROADCAST_WAIT_SECONDS
    while True:
        broadcast = REFERENCE_DUPLEX_RUNS.get(execution_run_id)
        if broadcast is not None and broadcast.active:
            return broadcast
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(REFERENCE_LISTENER_BROADCAST_POLL_SECONDS)


@app.get('/reference-agent/readiness')
async def reference_agent_readiness(x_cae_reference_token: str | None = Header(default=None)):
    _require_reference_token(x_cae_reference_token)
    return {
        'ready': bool(
            PIPECAT_RUNTIME_AVAILABLE
            and RTC_ASR_BASE_URL
            and KOKORO_BASE_URL
            and KOKORO_TESTER_VOICE != KOKORO_TARGET_VOICE
        ),
        'pipeline_runtime': PIPECAT_RUNTIME_AVAILABLE,
        'rtc_asr_configured': bool(RTC_ASR_BASE_URL),
        'kokoro_configured': bool(KOKORO_BASE_URL),
        'tester_voice': KOKORO_TESTER_VOICE,
        'target_voice': KOKORO_TARGET_VOICE,
        'voices_distinct': KOKORO_TESTER_VOICE != KOKORO_TARGET_VOICE,
        'duplex_route_ready': True,
        'listener_webrtc_ready': bool(PIPECAT_RUNTIME_AVAILABLE),
        'route': '/reference-duplex/run',
        'listener_route': '/reference-duplex/listen',
    }


@app.post('/public-pipecat/run')
async def public_pipecat_run(
    payload: PublicPipecatRunRequest,
    x_cae_reference_token: str | None = Header(default=None),
):
    """Join the public demo's Daily room directly as a Pipecat tester participant."""
    _require_reference_token(x_cae_reference_token)
    if not KOKORO_BASE_URL:
        raise HTTPException(status_code=503, detail='Public Pipecat execution requires KOKORO_BASE_URL.')
    try:
        from public_daily_target import (
            PublicDailyTargetError,
            PublicDailyTargetRequest,
            run_public_daily_target,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise HTTPException(
            status_code=503,
            detail='Install the Pipecat daily transport extra to run the public target directly.',
        ) from exc
    try:
        return await run_public_daily_target(
            PublicDailyTargetRequest(**payload.model_dump()),
            kokoro_base_url=KOKORO_BASE_URL,
            kokoro_model=KOKORO_MODEL,
            kokoro_voice=KOKORO_TESTER_VOICE,
        )
    except HTTPException:
        raise
    except PublicDailyTargetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        # Daily failures can include ephemeral room details. Keep them out of the API response.
        raise HTTPException(status_code=502, detail='Public Pipecat direct execution failed.') from exc


async def _public_pipecat_duplex_events(
    payload: PublicPipecatDuplexRequest,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def publish(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def next_turn(turn_pair: int, target_text: str, target_wav: bytes) -> tuple[str, bytes]:
        actions = payload.scenario.get('required_actions') or []
        objective = str(actions[min(turn_pair - 1, len(actions) - 1)]) if actions else str(
            payload.scenario.get('goal') or 'Continue the scenario naturally.'
        )
        request = ReferenceTesterTurnRequest(
            scenario_instruction=(
                f'{payload.scenario.get("id") or "public-pipecat"}: '
                f'{payload.scenario.get("goal") or objective}'
            ),
            act_id=f'scenario-turn-{turn_pair}',
            act_objective=objective,
            example_utterance=payload.caller_text,
            target_audio_wav_base64=base64.b64encode(target_wav).decode('ascii'),
            model_name=payload.tester_model_name,
        )
        try:
            # The public target already supplies an authoritative RTVI
            # transcript. Feed it through the existing tester LLM -> Kokoro
            # graph instead of lossy re-transcription of the same Daily audio.
            _asr, collector = await _run_reference_graph(
                TextFrame(target_text),
                _ReferenceTesterLlmProcessor(request),
                voice=KOKORO_TESTER_VOICE,
            )
        except Exception as exc:
            from public_daily_target import PublicDailyTargetError
            raise PublicDailyTargetError(
                f'Public Pipecat tester could not generate turn {turn_pair}.'
            ) from exc
        caller_wav = _pcm_to_wav(collector.audio, collector.sample_rate, collector.channels)
        return collector.agent_text.strip(), caller_wav

    async def execute() -> None:
        try:
            from public_daily_target import (
                PublicDailyDuplexRequest,
                PublicDailyTargetError,
                run_public_daily_duplex,
            )
            result = await run_public_daily_duplex(
                PublicDailyDuplexRequest(
                    caller_text=payload.caller_text,
                    agent=payload.agent,
                    timeout_seconds=payload.timeout_seconds,
                    max_turn_pairs=payload.max_turn_pairs,
                ),
                kokoro_base_url=KOKORO_BASE_URL,
                kokoro_model=KOKORO_MODEL,
                kokoro_voice=KOKORO_TESTER_VOICE,
                next_turn=next_turn,
                event_callback=publish,
            )
            await publish({'type': 'complete', 'result': result})
        except (ImportError, ModuleNotFoundError):
            await publish({
                'type': 'error',
                'detail': 'Install the Pipecat daily transport extra to run the public target directly.',
            })
        except Exception as exc:
            detail = (
                str(exc)
                if exc.__class__.__name__ == 'PublicDailyTargetError'
                else 'Public Pipecat direct execution failed.'
            )
            await publish({'type': 'error', 'detail': detail})

    task = asyncio.create_task(execute())
    try:
        while True:
            event = await queue.get()
            yield json.dumps(event, separators=(',', ':')) + '\n'
            if event.get('type') in {'complete', 'error'}:
                break
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@app.post('/public-pipecat/duplex')
async def public_pipecat_duplex(
    payload: PublicPipecatDuplexRequest,
    x_cae_reference_token: str | None = Header(default=None),
):
    """Stream a multi-turn CAE tester session through one public Daily room."""
    _require_reference_token(x_cae_reference_token)
    if not PIPECAT_RUNTIME_AVAILABLE or not RTC_ASR_BASE_URL or not KOKORO_BASE_URL:
        raise HTTPException(
            status_code=503,
            detail='Public Pipecat duplex requires Pipecat, rtc-asr, and Kokoro.',
        )
    return StreamingResponse(
        _public_pipecat_duplex_events(payload),
        media_type='application/x-ndjson',
        headers={'Cache-Control': 'no-store'},
    )


@app.post('/reference-duplex/run')
async def reference_duplex_run(
    payload: ReferenceDuplexRunRequest,
    x_cae_reference_token: str | None = Header(default=None),
):
    """Run the tester and target through one streaming Pipecat exchange graph."""
    _require_reference_token(x_cae_reference_token)
    if not PIPECAT_RUNTIME_AVAILABLE:
        raise HTTPException(status_code=503, detail='Pipecat runtime is unavailable.')
    missing = []
    if not RTC_ASR_BASE_URL:
        missing.append('RTC_ASR_BASE_URL')
    if not KOKORO_BASE_URL:
        missing.append('KOKORO_BASE_URL')
    if missing:
        raise HTTPException(status_code=503, detail=f'Reference duplex missing configuration: {", ".join(missing)}')
    return StreamingResponse(
        _reference_duplex_events(payload),
        media_type='application/x-ndjson',
        headers={'Cache-Control': 'no-store'},
    )


@app.post('/reference-duplex/listen')
async def reference_duplex_listen(
    payload: ReferenceListenerJoinRequest,
    x_cae_reference_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Attach a receive-only browser WebRTC peer to an active duplex run."""
    _require_reference_token(x_cae_reference_token)
    if not PIPECAT_RUNTIME_AVAILABLE:
        raise HTTPException(status_code=503, detail='Pipecat WebRTC runtime is unavailable.')
    broadcast = await _wait_for_active_reference_broadcast(payload.execution_run_id)
    if broadcast is None:
        raise HTTPException(status_code=409, detail='The duplex run is not active; retry while it is running.')
    if payload.listener_id in broadcast.listeners:
        raise HTTPException(status_code=409, detail='This listener is already attached.')
    attach_started_audio_sequence = broadcast.audio_publish_sequence
    ice_servers = []
    if LISTENER_TURN_URL and RTCIceServer is not None:
        turn_username, turn_credential = _listener_turn_auth(
            listener_id=payload.listener_id,
            expires_at_unix=payload.expires_at_unix,
        )
        ice_servers.append(RTCIceServer(
            urls=LISTENER_TURN_URL,
            username=turn_username,
            credential=turn_credential,
        ))
    connection = ReferenceListenerWebRTCConnection(
        audio_out_sample_rate=24000,
        ice_servers=ice_servers,
    )
    try:
        await connection.initialize(payload.sdp, payload.type)
        answer = connection.get_answer()
        track = getattr(connection, '_presenter_answer_audio_track', None)
        if not answer or track is None:
            raise RuntimeError('Pipecat did not produce a send-only audio answer.')
        attached_after_audio_sequence = broadcast.audio_publish_sequence
        pre_attach_listener_media_keys = sorted(broadcast.started_listener_media_keys)
        broadcast.listeners[payload.listener_id] = _ReferenceListener(
            listener_id=payload.listener_id,
            connection=connection,
            track=track,
        )
        asyncio.create_task(connection.connect())
        asyncio.create_task(_expire_reference_listener(
            payload.execution_run_id,
            payload.listener_id,
            payload.expires_at_unix,
        ))
        return {
            'status': 'listening',
            'read_only': True,
            'requires_microphone': False,
            'audio_published_during_attach': (
                attached_after_audio_sequence > attach_started_audio_sequence
            ),
            'pre_attach_listener_media_keys': pre_attach_listener_media_keys,
            'answer': answer,
        }
    except HTTPException:
        raise
    except Exception as exc:
        try:
            await connection.disconnect()
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f'Listener WebRTC negotiation failed: {exc}') from exc


async def _expire_reference_listener(
    execution_run_id: str,
    listener_id: str,
    expires_at_unix: float,
) -> None:
    await asyncio.sleep(max(0.0, expires_at_unix - time.time()))
    broadcast = REFERENCE_DUPLEX_RUNS.get(execution_run_id)
    if broadcast is None:
        return
    listener = broadcast.listeners.pop(listener_id, None)
    if listener is not None:
        try:
            await listener.connection.disconnect()
        except Exception:
            pass
    if (
        not broadcast.active
        and not broadcast.listeners
        and REFERENCE_DUPLEX_RUNS.get(execution_run_id) is broadcast
    ):
        REFERENCE_DUPLEX_RUNS.pop(execution_run_id, None)


@app.post('/reference-duplex/listen/ice')
async def reference_duplex_listener_ice(
    payload: ReferenceListenerIceRequest,
    x_cae_reference_token: str | None = Header(default=None),
) -> dict[str, str]:
    _require_reference_token(x_cae_reference_token)
    broadcast = REFERENCE_DUPLEX_RUNS.get(payload.execution_run_id)
    listener = broadcast.listeners.get(payload.listener_id) if broadcast else None
    if listener is None:
        raise HTTPException(status_code=404, detail='Listener WebRTC session not found.')
    await listener.connection.add_ice_candidate(_coerce_ice_candidate(payload.candidate))
    return {'status': 'ok'}


@app.post('/reference-duplex/listen/stop')
async def reference_duplex_listener_stop(
    payload: ReferenceListenerStopRequest,
    x_cae_reference_token: str | None = Header(default=None),
) -> dict[str, str]:
    _require_reference_token(x_cae_reference_token)
    broadcast = REFERENCE_DUPLEX_RUNS.get(payload.execution_run_id)
    listener = broadcast.listeners.pop(payload.listener_id, None) if broadcast else None
    if listener is not None:
        await listener.connection.disconnect()
    if (
        broadcast is not None
        and not broadcast.active
        and not broadcast.listeners
        and REFERENCE_DUPLEX_RUNS.get(payload.execution_run_id) is broadcast
    ):
        REFERENCE_DUPLEX_RUNS.pop(payload.execution_run_id, None)
    return {'status': 'stopped'}


@app.post('/reference-agent/turn')
async def reference_agent_turn(
    payload: ReferenceAgentTurnRequest,
    x_cae_reference_token: str | None = Header(default=None),
):
    """Run one target turn through a real Pipecat Pipeline object."""
    _require_reference_token(x_cae_reference_token)
    if not PIPECAT_RUNTIME_AVAILABLE:
        raise HTTPException(status_code=503, detail='Pipecat runtime is unavailable.')
    missing = []
    if not RTC_ASR_BASE_URL:
        missing.append('RTC_ASR_BASE_URL')
    if not KOKORO_BASE_URL:
        missing.append('KOKORO_BASE_URL')
    if missing:
        raise HTTPException(status_code=503, detail=f'Reference agent missing configuration: {", ".join(missing)}')
    try:
        wav_payload = base64.b64decode(payload.audio_wav_base64, validate=True)
        pcm, sample_rate, channels = _wav_to_pcm(wav_payload)
        asr_processor, collector = await _run_reference_graph(
            InputAudioRawFrame(pcm, sample_rate, channels),
            _ReferenceLlmProcessor(payload.history, payload.model_name or REFERENCE_LLM_MODEL),
            voice=payload.voice,
        )
        output_wav = _pcm_to_wav(collector.audio, collector.sample_rate, collector.channels)
        return {
            'caller_transcript': asr_processor.transcript,
            'agent_text': collector.agent_text,
            'agent_audio_wav_base64': base64.b64encode(output_wav).decode('ascii'),
            'first_audio_byte_latency_ms': collector.first_audio_byte_latency_ms,
            'response_complete_latency_ms': collector.total_latency_ms,
            'pipeline': {'provider': 'pipecat', 'processors': ['rtc-asr', 'llm', 'kokoro'], 'current_run': True},
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Reference Pipecat pipeline failed: {exc}') from exc


@app.post('/reference-tester/turn')
async def reference_tester_turn(
    payload: ReferenceTesterTurnRequest,
    x_cae_reference_token: str | None = Header(default=None),
):
    """Run tester ASR, LLM, and Kokoro through a real Pipecat Pipeline."""
    _require_reference_token(x_cae_reference_token)
    if not PIPECAT_RUNTIME_AVAILABLE:
        raise HTTPException(status_code=503, detail='Pipecat runtime is unavailable.')
    if not RTC_ASR_BASE_URL or not KOKORO_BASE_URL:
        raise HTTPException(status_code=503, detail='Reference tester requires rtc-asr and Kokoro.')
    try:
        if payload.target_audio_wav_base64:
            wav_payload = base64.b64decode(payload.target_audio_wav_base64, validate=True)
            pcm, sample_rate, channels = _wav_to_pcm(wav_payload)
            first_frame: Frame = InputAudioRawFrame(pcm, sample_rate, channels)
        else:
            first_frame = TextFrame('No target response yet; start the conversation from the scenario instruction.')
        asr_processor, collector = await _run_reference_graph(
            first_frame,
            _ReferenceTesterLlmProcessor(payload),
            voice=KOKORO_TESTER_VOICE,
        )
        output_wav = _pcm_to_wav(collector.audio, collector.sample_rate, collector.channels)
        return {
            'tester_asr_receipt': asr_processor.transcript or None,
            'tester_text': collector.agent_text,
            'tester_audio_wav_base64': base64.b64encode(output_wav).decode('ascii'),
            'pipeline': {'provider': 'pipecat', 'processors': ['rtc-asr', 'llm', 'kokoro'], 'current_run': True},
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Reference Pipecat tester pipeline failed: {exc}') from exc


def _wav_to_pcm(payload: bytes) -> tuple[bytes, int, int]:
    with wave.open(io.BytesIO(payload), 'rb') as source:
        return source.readframes(source.getnframes()), source.getframerate(), source.getnchannels()


def _pcm_to_wav(payload: bytes, sample_rate: int, channels: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(payload)
    return output.getvalue()


@dataclass
class PipecatSessionState:
    session_id: str
    public_token: str | None
    status: str
    connected: bool
    agent_status: str = 'idle'
    transport_mode: str = 'server-orchestrated'
    tool_state: dict[str, Any] = field(default_factory=dict)
    live_session: dict[str, Any] = field(default_factory=dict)
    frontend_contract: dict[str, Any] = field(default_factory=dict)
    contract: dict[str, Any] = field(default_factory=dict)
    instructions: str | None = None
    avatar: dict[str, Any] = field(default_factory=dict)
    realtime: dict[str, Any] = field(default_factory=dict)
    tool_manifest: list[dict[str, Any]] = field(default_factory=list)
    pipecat_plan: dict[str, Any] = field(default_factory=dict)
    last_transcript: str | None = None
    last_answer: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()


@dataclass
class LivePresenterSession:
    session_id: str
    public_token: str | None
    webrtc: SmallWebRTCConnection
    state: str = 'idle'
    runtime_status: str = 'idle'
    transport_ready: bool = False
    asr_ready: bool = False
    openai_ready: bool = False
    video_ready: bool = False
    pipeline_ready: bool = False
    video_pipeline_enabled: bool = False
    heygen_ready: bool = False
    heygen_join: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_error: str | None = None
    asr_contract: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    runtime_task: asyncio.Task | None = field(default=None, repr=False, compare=False)
    pipeline_task: Any | None = field(default=None, repr=False, compare=False)
    pipeline_runner: Any | None = field(default=None, repr=False, compare=False)
    heygen_service: Any | None = field(default=None, repr=False, compare=False)
    aiohttp_session: Any | None = field(default=None, repr=False, compare=False)

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_event(self, type_: str, **payload: Any) -> None:
        self.events.append({
            'type': type_,
            'payload': payload,
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
        self.events = self.events[-25:]
        self.touch()


SESSIONS: dict[str, PipecatSessionState] = {}
LIVE_SESSIONS: dict[str, LivePresenterSession] = {}


@app.get('/health')
def health() -> dict[str, Any]:
    return {
        'status': 'ok',
        'service': 'pipecat-orchestrator',
        'apiBaseUrl': API_BASE_URL,
        'sessionCount': len(SESSIONS),
        'providers': {
            'videoConfigured': _heygen_video_service_enabled(),
            'openaiConfigured': bool(os.getenv('OPENAI_API_KEY')),
            'heygenConfigured': bool(HEYGEN_LIVE_AVATAR_API_KEY),
            'pipecatRuntimeAvailable': PIPECAT_RUNTIME_AVAILABLE,
            'asr': _build_asr_contract(status='configured' if RTC_ASR_BASE_URL else 'not_configured'),
        },
    }


@app.post('/sessions/{session_id}/bootstrap')
def bootstrap(session_id: str, payload: SessionCreateRequest) -> dict[str, Any]:
    contract = _fetch_contract(session_id)
    instructions_payload = _fetch_instructions(session_id)
    state = _upsert_session(
        session_id=session_id,
        public_token=payload.publicToken or contract.get('public_token'),
        contract=contract,
        instructions=instructions_payload.get('instructions'),
        connected=False,
        status='ready',
    )
    return _build_bootstrap_response(state)


@app.post('/sessions/{session_id}/connect')
def connect(session_id: str, payload: SessionConnectRequest | None = None) -> dict[str, Any]:
    existing = SESSIONS.get(session_id)
    contract = existing.contract if existing else _fetch_contract(session_id)
    instructions_payload = {'instructions': existing.instructions} if existing and existing.instructions else _fetch_instructions(session_id)

    state = _upsert_session(
        session_id=session_id,
        public_token=(payload.publicToken if payload else None) or (existing.public_token if existing else None) or contract.get('public_token'),
        contract=contract,
        instructions=instructions_payload.get('instructions'),
        connected=True,
        status='connected',
    )

    return {
        'status': 'connected',
        'sessionId': session_id,
        'publicToken': state.public_token,
        'message': 'Pipecat logical session connected.',
        'transport': {
            'provider': 'pipecat',
            'mode': 'server-orchestrated',
            'model': state.realtime.get('model') or OPENAI_REALTIME_MODEL,
            'connect_url': f'/sessions/{session_id}/connect',
        },
        'voice': {
            'status': 'listening',
            'mode': 'pipecat-orchestrated',
            'asr': _build_asr_contract(status='configured' if RTC_ASR_BASE_URL else 'not_configured'),
            'start_endpoint': f'/sessions/{session_id}/connect',
            'ask_endpoint': f'/sessions/{session_id}/ask',
            'stop_endpoint': f'/sessions/{session_id}/disconnect',
        },
        'avatar': None,
        'realtime': {
            **state.realtime,
            'connect_url': f'/sessions/{session_id}/connect',
            'status': 'live_ready' if state.realtime.get('enabled') else 'configured',
        },
        'tool_manifest': state.tool_manifest,
        'pipecat_plan': state.pipecat_plan,
        'instructions': state.instructions,
        'connected': True,
        'created_at': state.created_at,
        'updated_at': state.updated_at,
    }


@app.post('/sessions/{session_id}/live/create')
async def create_live_session(session_id: str, payload: LiveSessionCreateRequest | None = None) -> dict[str, Any]:
    existing = SESSIONS.get(session_id)
    # Do not call FastAPI /api/bootstrap from inside the Pipecat live-create path:
    # that endpoint may call back into this Pipecat service to build its bootstrap,
    # which can deadlock the single-worker local dev server. The realtime contract
    # already contains the avatar/realtime/pipecat plan data needed to create the
    # browser WebRTC session.
    contract = existing.contract if existing else _fetch_contract(session_id)
    instructions_payload = {'instructions': existing.instructions} if existing and existing.instructions else _fetch_instructions(session_id)
    state = _upsert_session(
        session_id=session_id,
        public_token=(payload.publicToken if payload else None) or (existing.public_token if existing else None) or contract.get('public_token'),
        contract=contract,
        instructions=instructions_payload.get('instructions'),
        connected=False,
        status='ready',
    )

    existing_live = LIVE_SESSIONS.get(session_id)
    video_ready = _heygen_video_service_enabled()
    if existing_live and existing_live.state not in {'error', 'ended'}:
        existing_video_out = bool(getattr(existing_live.webrtc, '_presenter_video_out_enabled', False))
        if existing_video_out == video_ready:
            existing_live.add_event('live_session_reused', state=existing_live.state, video_ready=existing_live.video_ready)
            state.live_session = _serialize_live_session(existing_live)
            state.frontend_contract = _build_agent_contract(state)
            return {
                'status': 'ready',
                'sessionId': session_id,
                'publicToken': state.public_token,
                'live': _serialize_live_session(existing_live),
                'agent': state.frontend_contract,
                'transport': {
                    'provider': 'smallwebrtc',
                    'join_url': f'/sessions/{session_id}/live/join',
                    'ice_url': f'/sessions/{session_id}/live/ice',
                    'state_url': f'/sessions/{session_id}/live/state',
                    'stop_url': f'/sessions/{session_id}/live/stop',
                },
                'providers': {
                    'openai_realtime_ready': existing_live.openai_ready,
                    'asr_ready': existing_live.asr_ready,
                    'asr': existing_live.asr_contract or _build_asr_contract(status='unknown'),
                    'video_ready': existing_live.video_ready,
                },
                'nextStep': 'Reuse the existing browser WebRTC live session and POST the browser offer to /live/join.',
            }
        existing_live.add_event('live_session_replaced_for_video_negotiation', previous_video_ready=existing_live.video_ready, next_video_ready=video_ready)
        try:
            await _stop_live_runtime(existing_live)
        except Exception:
            pass
        LIVE_SESSIONS.pop(session_id, None)
        existing_live = None
    if existing_live is not None:
        try:
            await _stop_live_runtime(existing_live)
        except Exception:
            pass
        LIVE_SESSIONS.pop(session_id, None)

    asr_contract = await _resolve_asr_contract()
    live = LivePresenterSession(
        session_id=session_id,
        public_token=state.public_token,
        webrtc=LivePresenterWebRTCConnection(video_out_enabled=video_ready),
        state='connecting',
        asr_ready=asr_contract['status'] == 'ready',
        openai_ready=bool(os.getenv('OPENAI_API_KEY')),
        video_ready=video_ready,
        asr_contract=asr_contract,
    )
    live.add_event(
        'live_session_created',
        asr_ready=live.asr_ready,
        asr_status=asr_contract['status'],
        openai_ready=live.openai_ready,
        video_ready=live.video_ready,
    )
    if not live.asr_ready:
        live.add_event('rtc_asr_skipped', reason=asr_contract.get('reason') or asr_contract['status'])
    LIVE_SESSIONS[session_id] = live

    state.live_session = _serialize_live_session(live)
    state.frontend_contract = _build_agent_contract(state)

    return {
        'status': 'ready',
        'sessionId': session_id,
        'publicToken': state.public_token,
        'live': _serialize_live_session(live),
        'agent': state.frontend_contract,
        'transport': {
            'provider': 'smallwebrtc',
            'join_url': f'/sessions/{session_id}/live/join',
            'ice_url': f'/sessions/{session_id}/live/ice',
            'state_url': f'/sessions/{session_id}/live/state',
            'stop_url': f'/sessions/{session_id}/live/stop',
        },
        'providers': {
            'openai_realtime_ready': live.openai_ready,
            'asr_ready': live.asr_ready,
            'asr': live.asr_contract,
            'video_ready': live.video_ready,
        },
        'nextStep': 'Create a browser WebRTC offer and POST it to /live/join so Pipecat can answer and own the live session.',
    }



@app.post('/sessions/{session_id}/heygen/start')
async def start_heygen_live_session(session_id: str, payload: LiveSessionCreateRequest | None = None) -> dict[str, Any]:
    if not _heygen_video_service_enabled():
        raise HTTPException(status_code=503, detail='Pipecat HeyGen video service is not configured. Set HEYGEN_LIVE_AVATAR_API_KEY and install pipecat-ai[heygen].')

    existing = SESSIONS.get(session_id)
    contract = existing.contract if existing else _fetch_contract(session_id)
    instructions_payload = {'instructions': existing.instructions} if existing and existing.instructions else _fetch_instructions(session_id)
    state = _upsert_session(
        session_id=session_id,
        public_token=(payload.publicToken if payload else None) or (existing.public_token if existing else None) or contract.get('public_token'),
        contract=contract,
        instructions=instructions_payload.get('instructions'),
        connected=True,
        status='connected',
    )

    live = LIVE_SESSIONS.get(session_id)
    if not live:
        live = LivePresenterSession(
            session_id=session_id,
            public_token=state.public_token,
            webrtc=LivePresenterWebRTCConnection(video_out_enabled=True),
            state='connecting',
            asr_ready=False,
            openai_ready=bool(os.getenv('OPENAI_API_KEY')),
            video_ready=True,
            asr_contract=_build_asr_contract(status='unknown'),
        )
        LIVE_SESSIONS[session_id] = live

    live.video_ready = True
    live.add_event('heygen_live_session_start_requested', openai_ready=live.openai_ready)
    await _ensure_live_runtime(live, state)
    live.state = 'connected' if live.heygen_ready else live.state

    state.live_session = _serialize_live_session(live)
    state.frontend_contract = _build_agent_contract(state)
    return {
        'status': 'ready' if live.heygen_ready else 'starting',
        'sessionId': session_id,
        'live': _serialize_live_session(live),
        'heygen': live.heygen_join,
        'nextStep': 'Start the existing Pipecat WebRTC voice connection; HeyGen avatar video is returned on that WebRTC stream.',
    }


@app.post('/sessions/{session_id}/live/join')
async def join_live_session(session_id: str, payload: LiveSessionJoinRequest) -> dict[str, Any]:
    live = LIVE_SESSIONS.get(session_id)
    if not live:
        raise HTTPException(status_code=404, detail='Live session not found. Create it first.')
    state = SESSIONS.get(session_id)
    try:
        await live.webrtc.initialize(payload.sdp, payload.type)
        answer = live.webrtc.get_answer()
        if not answer:
            raise HTTPException(status_code=502, detail='Pipecat did not produce a WebRTC answer.')
    except HTTPException:
        raise
    except asyncio.CancelledError:
        live.transport_ready = False
        live.state = 'error'
        live.runtime_status = 'transport_negotiation_cancelled'
        live.last_error = 'WebRTC negotiation was cancelled, usually because the live session was replaced or the browser retried during startup.'
        live.add_event('join_cancelled', error=live.last_error)
        if state:
            state.connected = False
            state.status = 'error'
            state.agent_status = 'transport_failed'
            state.live_session = _serialize_live_session(live)
            state.frontend_contract = _build_agent_contract(state)
        raise HTTPException(status_code=409, detail=live.last_error)
    except Exception as exc:
        live.transport_ready = False
        live.state = 'error'
        live.runtime_status = 'transport_negotiation_failed'
        live.last_error = str(exc)
        live.add_event('join_failed', error=str(exc))
        if state:
            state.connected = False
            state.status = 'error'
            state.agent_status = 'transport_failed'
            state.live_session = _serialize_live_session(live)
            state.frontend_contract = _build_agent_contract(state)
        raise HTTPException(status_code=502, detail=f'Live WebRTC negotiation failed: {exc}') from exc

    # Return the SDP answer as soon as signaling succeeds. Starting the optional
    # Pipecat/OpenAI media pipeline can perform network/provider work and must
    # not hold the browser's WebRTC answer hostage during the voice-only proof.
    if state:
        asyncio.create_task(_ensure_live_runtime(live, state))
    else:
        asyncio.create_task(live.webrtc.connect())
    live.transport_ready = True
    live.state = 'ready' if live.last_error is None else 'degraded'
    live.add_event(
        'browser_joined',
        pc_id=answer.get('pc_id'),
        runtime_status=live.runtime_status,
        pipeline_ready=live.pipeline_ready,
        video_pipeline_enabled=live.video_pipeline_enabled,
    )
    if state:
        state.connected = True
        state.status = 'connected'
        state.agent_status = 'ready' if live.pipeline_ready else 'transport_ready'
        state.live_session = _serialize_live_session(live)
        state.frontend_contract = _build_agent_contract(state)
    return {
        'status': 'ready',
        'sessionId': session_id,
        'answer': answer,
        'live': _serialize_live_session(live),
    }


@app.post('/sessions/{session_id}/live/ice')
async def add_live_ice_candidate(session_id: str, payload: IceCandidateRequest) -> dict[str, Any]:
    live = LIVE_SESSIONS.get(session_id)
    if not live:
        raise HTTPException(status_code=404, detail='Live session not found. Create it first.')
    try:
        await live.webrtc.add_ice_candidate(_coerce_ice_candidate(payload.candidate))
        live.add_event('ice_candidate_added')
        return {'status': 'ok', 'sessionId': session_id}
    except Exception as exc:
        live.last_error = str(exc)
        live.add_event('ice_candidate_failed', error=str(exc))
        raise HTTPException(status_code=500, detail=f'Adding ICE candidate failed: {exc}') from exc


@app.get('/sessions/{session_id}/live/state')
def get_live_state(session_id: str) -> dict[str, Any]:
    live = LIVE_SESSIONS.get(session_id)
    if not live:
        raise HTTPException(status_code=404, detail='Live session not found. Create it first.')
    state = SESSIONS.get(session_id)
    if state:
        state.live_session = _serialize_live_session(live)
        state.frontend_contract = _build_agent_contract(state)
    return {
        'status': live.state,
        'sessionId': session_id,
        'live': _serialize_live_session(live),
        'agent': state.frontend_contract if state else None,
    }


@app.post('/sessions/{session_id}/live/stop')
async def stop_live_session(session_id: str) -> dict[str, Any]:
    live = LIVE_SESSIONS.get(session_id)
    if not live:
        return {'status': 'idle', 'sessionId': session_id}
    try:
        await _stop_live_runtime(live)
    except Exception:
        pass
    live.transport_ready = False
    live.state = 'ended'
    live.add_event('live_session_stopped')
    serialized = _serialize_live_session(live)
    LIVE_SESSIONS.pop(session_id, None)
    state = SESSIONS.get(session_id)
    if state:
        state.connected = False
        state.status = 'disconnected'
        state.agent_status = 'ended'
        state.live_session = {}
        state.frontend_contract = _build_agent_contract(state)
    return {
        'status': 'ended',
        'sessionId': session_id,
        'live': serialized,
    }


@app.post('/sessions/{session_id}/present-current')
async def present_current_slide(session_id: str) -> dict[str, Any]:
    state = SESSIONS.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail='Pipecat session not found. Start the agent first.')

    live = LIVE_SESSIONS.get(session_id)
    if not live:
        raise HTTPException(status_code=404, detail='Live session not found. Start live voice first.')

    if not live.pipeline_task or not live.runtime_task or live.runtime_task.done():
        await _ensure_live_runtime(live, state)

    if not live.pipeline_task or not live.pipeline_ready or not PIPECAT_RUNTIME_AVAILABLE:
        raise HTTPException(status_code=409, detail='Live voice pipeline is not ready to speak yet.')

    prompt = (
        'Start the presentation now. Briefly greet the audience, then present the current slide. '
        'Use the current slide content and keep it concise. If a slide tool is available, use get_current_slide before speaking.'
    )
    context = LLMContext(messages=[{'role': 'user', 'content': prompt}])
    await live.pipeline_task.queue_frame(LLMContextFrame(context))

    state.status = 'connected'
    state.agent_status = 'speaking'
    state.last_transcript = prompt
    state.live_session = _serialize_live_session(live)
    state.frontend_contract = _build_agent_contract(state)
    state.touch()
    live.add_event('initial_presenter_prompt_queued')

    return {
        'status': 'queued',
        'sessionId': session_id,
        'agent_status': state.agent_status,
        'live': _serialize_live_session(live),
    }


@app.post('/sessions/{session_id}/agent/start')
def start_agent(session_id: str, payload: SessionAgentStartRequest | None = None) -> dict[str, Any]:
    existing = SESSIONS.get(session_id)
    contract = existing.contract if existing else _fetch_contract(session_id)
    instructions_payload = {'instructions': existing.instructions} if existing and existing.instructions else _fetch_instructions(session_id)

    state = _upsert_session(
        session_id=session_id,
        public_token=(payload.publicToken if payload else None) or (existing.public_token if existing else None) or contract.get('public_token'),
        contract=contract,
        instructions=instructions_payload.get('instructions'),
        connected=True,
        status='connected',
    )
    state.agent_status = 'listening'
    state.live_session = {
        'orchestrator': 'pipecat',
        'session_id': session_id,
        'transport_mode': state.transport_mode,
        'tool_manifest_count': len(state.tool_manifest),
    }
    state.frontend_contract = _build_agent_contract(state)
    state.touch()
    return state.frontend_contract


@app.get('/sessions/{session_id}/agent/state')
def get_agent_state(session_id: str) -> dict[str, Any]:
    state = SESSIONS.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail='Pipecat session not found. Start the agent first.')
    state.frontend_contract = _build_agent_contract(state)
    return state.frontend_contract


@app.post('/sessions/{session_id}/agent/stop')
def stop_agent(session_id: str) -> dict[str, Any]:
    state = SESSIONS.get(session_id)
    if not state:
        return {
            'status': 'disconnected',
            'sessionId': session_id,
            'connected': False,
            'agent_status': 'disconnected',
        }
    state.connected = False
    state.status = 'disconnected'
    state.agent_status = 'disconnected'
    state.touch()
    state.frontend_contract = _build_agent_contract(state)
    return state.frontend_contract


@app.post('/sessions/{session_id}/ask')
def ask(session_id: str, payload: SessionAskRequest) -> dict[str, Any]:
    state = SESSIONS.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail='Pipecat session not found. Connect the session first.')

    transcript = (payload.transcript or '').strip()
    if not transcript:
        raise HTTPException(status_code=400, detail='transcript is required')

    state.status = 'answering'
    state.agent_status = 'thinking'
    state.last_transcript = transcript
    state.touch()

    tool_result = _maybe_handle_directive_tool_call(state=state, transcript=transcript)
    active_tool_state: dict[str, Any] | None = None
    if tool_result is not None:
        answer_text = tool_result['answer']
        citations = tool_result.get('citations') or []
        state.tool_state = {
            'last_tool_result': tool_result,
            'last_tool_at': datetime.now(timezone.utc).isoformat(),
        }
        active_tool_state = state.tool_state
    else:
        answer_payload = _post_json(
            f'{API_BASE_URL}/api/sessions/{session_id}/ask',
            {'question': transcript},
        )
        answer_text = str(answer_payload.get('answer') or '').strip()
        citations = answer_payload.get('citations') if isinstance(answer_payload.get('citations'), list) else []

    if not answer_text:
        raise HTTPException(status_code=502, detail='Grounded answer payload was empty.')

    state.last_answer = answer_text
    state.status = 'connected'
    state.agent_status = 'speaking'
    state.frontend_contract = _build_agent_contract(state)
    state.touch()

    return {
        'status': 'answered',
        'sessionId': session_id,
        'transcript': transcript,
        'answer': answer_text,
        'citations': citations,
        'connected': state.connected,
        'avatar': None,
        'realtime': state.realtime,
        'tool_manifest': state.tool_manifest,
        'pipecat_plan': state.pipecat_plan,
        'agent_status': state.agent_status,
        'tool_state': active_tool_state,
    }


@app.post('/sessions/{session_id}/disconnect')
async def disconnect(session_id: str) -> dict[str, Any]:
    live = LIVE_SESSIONS.get(session_id)
    live_payload: dict[str, Any] | None = None
    if live:
        try:
            await _stop_live_runtime(live)
        except Exception:
            pass
        live.transport_ready = False
        live.state = 'ended'
        live.add_event('logical_session_disconnected')
        live_payload = _serialize_live_session(live)
        LIVE_SESSIONS.pop(session_id, None)

    state = SESSIONS.get(session_id)
    if state:
        state.connected = False
        state.status = 'disconnected'
        state.agent_status = 'disconnected'
        state.live_session = {}
        state.frontend_contract = _build_agent_contract(state)
        state.touch()
    return {
        'status': 'disconnected',
        'sessionId': session_id,
        'connected': False,
        'live': live_payload,
    }


def _fetch_contract(session_id: str) -> dict[str, Any]:
    payload = _get_json(f'{API_BASE_URL}/api/realtime/sessions/{session_id}/contract')
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail='Realtime contract response was not an object.')
    return payload


def _fetch_instructions(session_id: str) -> dict[str, Any]:
    payload = _get_json(f'{API_BASE_URL}/api/realtime/sessions/{session_id}/instructions')
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail='Realtime instructions response was not an object.')
    return payload


def _fetch_bootstrap(session_id: str) -> dict[str, Any]:
    payload = _post_json(f'{API_BASE_URL}/api/bootstrap/sessions/{session_id}', {})
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail='Realtime bootstrap response was not an object.')
    return payload


def _get_json(url: str) -> Any:
    try:
        response = httpx.get(url, timeout=30.0)
        response.raise_for_status()
        return response.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'GET failed for {url}: {exc}') from exc


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    try:
        response = httpx.post(url, json=payload, timeout=30.0)
        response.raise_for_status()
        return response.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'POST failed for {url}: {exc}') from exc


def _call_get_current_slide(session_id: str) -> dict[str, Any]:
    slide = _get_json(f'{API_BASE_URL}/api/sessions/{session_id}/current-slide')
    if not isinstance(slide, dict):
        raise HTTPException(status_code=502, detail='Current slide payload was invalid.')
    return slide


def _call_search_slides(session_id: str, query: str) -> dict[str, Any]:
    result = _get_json(f"{API_BASE_URL}/api/sessions/{session_id}/search-slides?query={quote(query)}")
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail='Search slides payload was invalid.')
    return result


def _call_get_slide_content(session_id: str, slide_index: int) -> dict[str, Any]:
    result = _get_json(f'{API_BASE_URL}/api/realtime/sessions/{session_id}/slide-content/{slide_index}')
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail='Slide content payload was invalid.')
    return result


def _call_next_slide(session_id: str) -> dict[str, Any]:
    return _post_json(f'{API_BASE_URL}/api/sessions/{session_id}/next-slide', {})


def _call_prev_slide(session_id: str) -> dict[str, Any]:
    return _post_json(f'{API_BASE_URL}/api/sessions/{session_id}/prev-slide', {})


def _call_goto_slide(session_id: str, slide_index: int) -> dict[str, Any]:
    return _post_json(f'{API_BASE_URL}/api/sessions/{session_id}/goto-slide', {'index': slide_index})


def _call_restart_current_slide(session_id: str) -> dict[str, Any]:
    return _post_json(f'{API_BASE_URL}/api/sessions/{session_id}/restart-current-slide', {})


def _call_pause(session_id: str) -> dict[str, Any]:
    return _post_json(f'{API_BASE_URL}/api/sessions/{session_id}/pause', {})


def _call_resume(session_id: str) -> dict[str, Any]:
    return _post_json(f'{API_BASE_URL}/api/sessions/{session_id}/resume', {})


def _dispatch_tool_call(session_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    if tool_name == 'get_current_slide':
        return _call_get_current_slide(session_id)
    if tool_name == 'search_slides':
        query = str(args.get('query') or '').strip()
        if not query:
            raise HTTPException(status_code=400, detail='search_slides requires query')
        return _call_search_slides(session_id, query)
    if tool_name == 'get_slide_content':
        slide_index = int(args.get('slide_index', 0))
        return _call_get_slide_content(session_id, slide_index)
    if tool_name == 'next_slide':
        return _call_next_slide(session_id)
    if tool_name == 'prev_slide':
        return _call_prev_slide(session_id)
    if tool_name == 'goto_slide':
        slide_index = int(args.get('slide_index', 0))
        return _call_goto_slide(session_id, slide_index)
    if tool_name == 'restart_current_slide':
        return _call_restart_current_slide(session_id)
    if tool_name == 'pause_presentation':
        return _call_pause(session_id)
    if tool_name == 'resume_presentation':
        return _call_resume(session_id)
    raise HTTPException(status_code=400, detail=f'Unsupported tool: {tool_name}')


def _format_tool_answer(*, session_id: str, tool_name: str, tool_result: dict[str, Any]) -> dict[str, Any]:
    if tool_name == 'get_current_slide':
        title = tool_result.get('title') or 'Untitled'
        index = tool_result.get('index')
        summary = tool_result.get('summary') or ''
        return {
            'answer': f"Current slide is {(index or 0) + 1}: {title}. {summary}".strip(),
            'citations': [{'slide_index': index, 'reason': 'current slide'}] if isinstance(index, int) else [],
        }
    if tool_name == 'search_slides':
        results = tool_result.get('results') if isinstance(tool_result.get('results'), list) else []
        if results:
            top = results[0]
            return {
                'answer': f"Best matching slide is {top.get('index', 0) + 1}: {top.get('title', 'Untitled')}. {top.get('summary', '')}".strip(),
                'citations': [
                    {'slide_index': item.get('index', 0), 'reason': 'search result'}
                    for item in results[:3]
                    if isinstance(item, dict)
                ],
            }
        return {'answer': 'I did not find a strong slide match for that search.', 'citations': []}
    if tool_name == 'get_slide_content':
        title = tool_result.get('title') or 'Untitled'
        index = tool_result.get('index')
        summary = tool_result.get('summary') or tool_result.get('raw_text') or ''
        return {
            'answer': f"Slide {(index or 0) + 1}: {title}. {summary}".strip(),
            'citations': [{'slide_index': index, 'reason': 'requested slide content'}] if isinstance(index, int) else [],
        }
    if tool_name in {'next_slide', 'prev_slide', 'goto_slide', 'restart_current_slide'}:
        slide = _call_get_current_slide(session_id)
        title = slide.get('title') if isinstance(slide, dict) else None
        index = slide.get('index') if isinstance(slide, dict) else None
        verb = {
            'next_slide': 'Moved to',
            'prev_slide': 'Moved back to',
            'goto_slide': 'Jumped to',
            'restart_current_slide': 'Restarted',
        }[tool_name]
        return {
            'answer': f"{verb} slide {(index or 0) + 1}{f': {title}' if title else ''}.",
            'citations': [{'slide_index': index, 'reason': 'presentation control tool'}] if isinstance(index, int) else [],
        }
    if tool_name == 'pause_presentation':
        return {'answer': 'Presentation paused.', 'citations': []}
    if tool_name == 'resume_presentation':
        return {'answer': 'Presentation resumed.', 'citations': []}
    return {'answer': 'Tool executed.', 'citations': []}


_SLIDE_NUMBER_WORDS = {
    'one': 1,
    'first': 1,
    'two': 2,
    'second': 2,
    'three': 3,
    'third': 3,
    'four': 4,
    'fourth': 4,
    'five': 5,
    'fifth': 5,
    'six': 6,
    'sixth': 6,
    'seven': 7,
    'seventh': 7,
    'eight': 8,
    'eighth': 8,
    'nine': 9,
    'ninth': 9,
    'ten': 10,
    'tenth': 10,
}


def _parse_slide_index_from_directive(lowered: str) -> int | None:
    """Parse common spoken slide navigation phrases into zero-based slide indexes."""
    normalized = lowered.replace('-', ' ')
    for prefix in ['go to slide', 'jump to slide', 'move to slide', 'navigate to slide']:
        if normalized.startswith(prefix):
            suffix = normalized.removeprefix(prefix).strip()
            number_text = ''.join(ch for ch in suffix if ch.isdigit())
            if number_text:
                return max(int(number_text) - 1, 0)
            for token in suffix.split():
                if token in _SLIDE_NUMBER_WORDS:
                    return _SLIDE_NUMBER_WORDS[token] - 1

    for phrase, slide_number in _SLIDE_NUMBER_WORDS.items():
        if f'to the {phrase} slide' in normalized or f'to {phrase} slide' in normalized:
            return slide_number - 1

    return None


def _last_slide_index_from_contract(contract: dict[str, Any]) -> int | None:
    deck = contract.get('deck') if isinstance(contract.get('deck'), dict) else {}
    manifest = deck.get('manifest_json') if isinstance(deck.get('manifest_json'), dict) else {}
    slide_count = manifest.get('slide_count') or deck.get('slide_count')
    if isinstance(slide_count, int) and slide_count > 0:
        return slide_count - 1
    slides = manifest.get('slides') if isinstance(manifest.get('slides'), list) else []
    if slides:
        return len(slides) - 1
    return None


def _is_next_slide_directive(lowered: str) -> bool:
    """Detect spoken requests that imply the visible slide should advance before discussion."""
    normalized = lowered.replace('-', ' ')
    explicit_next_phrases = [
        'next slide',
        'go next',
        'move on',
        'move forward',
        'continue to the next slide',
        'move to the next slide',
        'advance the slide',
        'advance to the next slide',
        'proceed to the next slide',
        "let's continue",
        "let's move on",
        "let us continue",
        "let us move on",
    ]
    if any(phrase in normalized for phrase in explicit_next_phrases):
        return True

    discussion_verbs = ['talk about', 'tell me about', "what's on", 'what is on', 'present', 'show me']
    return any(verb in normalized for verb in discussion_verbs) and 'next' in normalized and 'slide' in normalized


def _maybe_handle_directive_tool_call(*, state: PipecatSessionState, transcript: str) -> dict[str, Any] | None:
    lowered = transcript.lower().strip()
    session_id = state.session_id

    if any(
        phrase in lowered
        for phrase in [
            'what slide am i on',
            'which slide am i on',
            'current slide',
            'what slide is this',
            'where are we in the deck',
            'where are we in this deck',
            'where are we now',
        ]
    ):
        tool_name = 'get_current_slide'
        result = _dispatch_tool_call(session_id, tool_name)
        formatted = _format_tool_answer(session_id=session_id, tool_name=tool_name, tool_result=result)
        return {
            'tool_name': tool_name,
            'tool_result': result,
            **formatted,
        }

    if _is_next_slide_directive(lowered):
        tool_name = 'next_slide'
        result = _dispatch_tool_call(session_id, tool_name)
        slide = _call_get_current_slide(session_id)
        title = slide.get('title') if isinstance(slide, dict) else None
        index = slide.get('index') if isinstance(slide, dict) else None
        summary = slide.get('summary') if isinstance(slide, dict) else None
        answer = f"Moved to slide {(index or 0) + 1}{f': {title}' if title else ''}."
        if summary and any(phrase in lowered for phrase in ['talk about', 'tell me about', "what's on", 'what is on', 'present']):
            answer = f"{answer} {summary}"
        return {
            'tool_name': tool_name,
            'tool_result': result,
            'answer': answer.strip(),
            'citations': [{'slide_index': index, 'reason': 'slide advanced before discussing next slide'}] if isinstance(index, int) else [],
        }

    if any(phrase in lowered for phrase in ['previous slide', 'go back', 'back one slide']):
        tool_name = 'prev_slide'
        result = _dispatch_tool_call(session_id, tool_name)
        slide = _call_get_current_slide(session_id)
        title = slide.get('title') if isinstance(slide, dict) else None
        index = slide.get('index') if isinstance(slide, dict) else None
        return {
            'tool_name': tool_name,
            'tool_result': result,
            'answer': f"Moved back to slide {(index or 0) + 1}{f': {title}' if title else ''}.",
            'citations': [{'slide_index': index, 'reason': 'slide reversed by directive'}] if isinstance(index, int) else [],
        }

    if any(
        phrase in lowered
        for phrase in [
            'start over',
            'restart deck',
            'restart the deck',
            'back to beginning',
            'back to the beginning',
            'go to beginning',
            'go to the beginning',
        ]
    ):
        tool_name = 'goto_slide'
        result = _dispatch_tool_call(session_id, tool_name, {'slide_index': 0})
        slide = _call_get_current_slide(session_id)
        title = slide.get('title') if isinstance(slide, dict) else None
        index = slide.get('index') if isinstance(slide, dict) else 0
        answered_index = index if isinstance(index, int) else 0
        return {
            'tool_name': tool_name,
            'tool_result': result,
            'answer': f"Restarted at slide {answered_index + 1}{f': {title}' if title else ''}.",
            'citations': [{'slide_index': answered_index, 'reason': 'deck restarted by directive'}],
        }

    if any(phrase in lowered for phrase in ['pause presentation', 'pause the presentation', 'pause here']):
        tool_name = 'pause_presentation'
        result = _dispatch_tool_call(session_id, tool_name)
        return {'tool_name': tool_name, 'tool_result': result, 'answer': 'Presentation paused.', 'citations': []}

    if any(phrase in lowered for phrase in ['resume presentation', 'resume the presentation', 'continue presentation']):
        tool_name = 'resume_presentation'
        result = _dispatch_tool_call(session_id, tool_name)
        return {'tool_name': tool_name, 'tool_result': result, 'answer': 'Presentation resumed.', 'citations': []}

    if any(phrase in lowered for phrase in ['last slide', 'final slide', 'end of the deck']):
        target_index = _last_slide_index_from_contract(state.contract)
    else:
        target_index = _parse_slide_index_from_directive(lowered)

    if target_index is not None:
        tool_name = 'goto_slide'
        result = _dispatch_tool_call(session_id, tool_name, {'slide_index': target_index})
        slide = _call_get_current_slide(session_id)
        title = slide.get('title') if isinstance(slide, dict) else None
        index = slide.get('index') if isinstance(slide, dict) else None
        answered_index = index if isinstance(index, int) else target_index
        return {
            'tool_name': tool_name,
            'tool_result': result,
            'answer': f"Jumped to slide {answered_index + 1}{f': {title}' if title else ''}.",
            'citations': [{'slide_index': answered_index, 'reason': 'slide selected by directive'}],
        }

    if lowered.startswith('search slides for '):
        query = transcript[len('search slides for '):].strip()
        if query:
            tool_name = 'search_slides'
            result = _dispatch_tool_call(session_id, tool_name, {'query': query})
            formatted = _format_tool_answer(session_id=session_id, tool_name=tool_name, tool_result=result)
            return {
                'tool_name': tool_name,
                'tool_result': result,
                **formatted,
            }

    return None


def _upsert_session(
    *,
    session_id: str,
    public_token: str | None,
    contract: dict[str, Any],
    instructions: str | None,
    connected: bool,
    status: str,
) -> PipecatSessionState:
    avatar = contract.get('avatar') if isinstance(contract.get('avatar'), dict) else {}
    realtime = contract.get('realtime') if isinstance(contract.get('realtime'), dict) else {}
    tool_manifest = contract.get('tool_manifest') if isinstance(contract.get('tool_manifest'), list) else []
    pipecat_plan = contract.get('pipecat_plan') if isinstance(contract.get('pipecat_plan'), dict) else {}

    existing = SESSIONS.get(session_id)
    if existing:
        existing.public_token = public_token or existing.public_token
        existing.status = status
        existing.connected = connected
        existing.transport_mode = 'browser-webrtc' if realtime.get('browser_direct_supported') else 'server-orchestrated'
        existing.contract = contract
        existing.instructions = instructions
        existing.avatar = {
            **avatar,
        }
        existing.realtime = {
            'provider': 'pipecat',
            **realtime,
            'pipecat_service_url': PIPECAT_SERVICE_URL,
            'model': realtime.get('model') or OPENAI_REALTIME_MODEL,
        }
        existing.tool_manifest = [tool for tool in tool_manifest if isinstance(tool, dict)]
        existing.pipecat_plan = {
            **pipecat_plan,
            'orchestrator': 'pipecat',
            'state_authority': 'fastapi',
            'asr': _build_asr_contract(status='configured' if RTC_ASR_BASE_URL else 'not_configured'),
        }
        existing.frontend_contract = _build_agent_contract(existing)
        existing.touch()
        return existing

    state = PipecatSessionState(
        session_id=session_id,
        public_token=public_token,
        status=status,
        connected=connected,
        transport_mode='browser-webrtc' if realtime.get('browser_direct_supported') else 'server-orchestrated',
        contract=contract,
        instructions=instructions,
        avatar={
            **avatar,
        },
        realtime={
            'provider': 'pipecat',
            **realtime,
            'pipecat_service_url': PIPECAT_SERVICE_URL,
            'model': realtime.get('model') or OPENAI_REALTIME_MODEL,
        },
        tool_manifest=[tool for tool in tool_manifest if isinstance(tool, dict)],
        pipecat_plan={
            **pipecat_plan,
            'orchestrator': 'pipecat',
            'state_authority': 'fastapi',
            'asr': _build_asr_contract(status='configured' if RTC_ASR_BASE_URL else 'not_configured'),
        },
    )
    state.frontend_contract = _build_agent_contract(state)
    SESSIONS[session_id] = state
    return state


def _serialize_live_session(live: LivePresenterSession) -> dict[str, Any]:
    return {
        'session_id': live.session_id,
        'public_token': live.public_token,
        'state': live.state,
        'runtime_status': live.runtime_status,
        'transport_ready': live.transport_ready,
        'asr_ready': live.asr_ready,
        'asr': live.asr_contract or _build_asr_contract(status='unknown'),
        'openai_ready': live.openai_ready,
        'video_ready': live.video_ready,
        'pipeline_ready': live.pipeline_ready,
        'video_pipeline_enabled': live.video_pipeline_enabled,
        'heygen_ready': live.heygen_ready,
        'heygen_join': live.heygen_join,
        'last_error': live.last_error,
        'events': live.events,
        'created_at': live.created_at,
        'updated_at': live.updated_at,
    }


def _heygen_video_service_enabled() -> bool:
    return bool(HEYGEN_LIVE_AVATAR_API_KEY and HeyGenVideoService and LiveAvatarNewSessionRequest)


def _join_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith('/') else f'/{path}'
    return f'{base_url.rstrip()}{normalized_path}'


def _rtc_asr_stream_url() -> str | None:
    if not RTC_ASR_BASE_URL:
        return None
    url = _join_url(RTC_ASR_BASE_URL, RTC_ASR_STREAM_PATH)
    if url.startswith('http://'):
        return f'ws://{url[len("http://"):]}'
    if url.startswith('https://'):
        return f'wss://{url[len("https://"):]}'
    return url


def _rtc_asr_health_url() -> str | None:
    if not RTC_ASR_BASE_URL:
        return None
    return _join_url(RTC_ASR_BASE_URL, RTC_ASR_HEALTH_PATH)


def _build_asr_contract(*, status: str = 'unknown', reason: str | None = None) -> dict[str, Any]:
    configured = bool(RTC_ASR_BASE_URL)
    return {
        'provider': 'rtc-asr',
        'required_for_live_asr': True,
        'configured': configured,
        'status': status if configured else 'not_configured',
        'reason': reason or (None if configured else 'Set RTC_ASR_BASE_URL to enable live ASR.'),
        'base_url': RTC_ASR_BASE_URL or None,
        'health_url': _rtc_asr_health_url(),
        'stream_url': _rtc_asr_stream_url(),
        'stream_protocol': '/v1/stt/stream WebSocket',
        'audio': {
            'sample_rate_hz': RTC_ASR_SAMPLE_RATE,
            'channels': RTC_ASR_CHANNELS,
            'encoding': RTC_ASR_ENCODING,
            'endianness': 'little',
        },
        'demo_fallback': {
            'transcript_text_loop': 'non-production demo support only',
        },
    }


async def _resolve_asr_contract() -> dict[str, Any]:
    health_url = _rtc_asr_health_url()
    if not health_url:
        return _build_asr_contract(status='not_configured')
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(health_url)
        if 200 <= response.status_code < 400:
            return _build_asr_contract(status='ready')
        return _build_asr_contract(status='unavailable', reason=f'rtc-asr health returned HTTP {response.status_code}.')
    except Exception as exc:
        return _build_asr_contract(status='unavailable', reason=f'rtc-asr health check failed: {exc}')


def _format_heygen_start_error(error: str | None) -> str:
    if not error:
        return 'HeyGen avatar video service did not become ready yet.'
    if 'No credits available for start session' in error or 'code\":4033' in error or 'code":4033' in error:
        return 'HeyGen LiveAvatar could not start: no credits available for start session.'
    if 'API request failed with status 403' in error:
        return f'HeyGen LiveAvatar could not start: {error}'
    return error


async def _mark_heygen_video_ready(live: LivePresenterSession) -> None:
    if _heygen_video_service_enabled():
        live.heygen_ready = True
        live.heygen_join = {
            'provider': 'pipecat-heygen-video-service',
            'avatar_id': _heygen_avatar_id(),
            'sandbox': HEYGEN_SANDBOX,
        }
        live.add_event('heygen_video_service_ready', avatar_id=_heygen_avatar_id(), sandbox=HEYGEN_SANDBOX)



async def _ensure_live_runtime(live: LivePresenterSession, state: PipecatSessionState) -> None:
    if live.pipeline_ready and live.runtime_task and not live.runtime_task.done():
        return

    if not PIPECAT_RUNTIME_AVAILABLE:
        await live.webrtc.connect()
        live.runtime_status = 'transport_only'
        live.pipeline_ready = False
        live.last_error = 'Pipecat runtime classes are unavailable; SmallWebRTC signaling is running without a media pipeline.'
        live.add_event('runtime_degraded', reason=live.last_error)
        return

    processors = []
    try:
        params = TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            audio_in_sample_rate=RTC_ASR_SAMPLE_RATE,
            video_in_enabled=False,
            video_out_enabled=_heygen_video_service_enabled(),
            video_out_is_live=_heygen_video_service_enabled(),
            video_out_width=HEYGEN_VIDEO_WIDTH,
            video_out_height=HEYGEN_VIDEO_HEIGHT,
        )
        transport = SmallWebRTCTransport(live.webrtc, params=params)
        processors.append(transport.input())

        llm = None
        if live.openai_ready:
            llm = _build_openai_realtime_service(state)
            _register_realtime_tools(llm, state)
            processors.append(llm)
        else:
            live.add_event('openai_realtime_skipped', reason='OPENAI_API_KEY is not configured')

        if _heygen_video_service_enabled():
            live.aiohttp_session = live.aiohttp_session or aiohttp.ClientSession()
            heygen = HeyGenVideoService(
                api_key=HEYGEN_LIVE_AVATAR_API_KEY,
                service_type=ServiceType.LIVE_AVATAR,
                session=live.aiohttp_session,
                session_request=LiveAvatarNewSessionRequest(
                    avatar_id=_heygen_avatar_id(),
                    is_sandbox=HEYGEN_SANDBOX,
                ),
            )
            live.heygen_service = heygen
            live.video_pipeline_enabled = True
            processors.append(heygen)
        else:
            live.add_event('heygen_video_service_skipped', reason='HEYGEN_LIVE_AVATAR_API_KEY/HEYGEN_API_KEY is not configured')

        processors.append(transport.output())
        pipeline = Pipeline(processors)
        live.pipeline_task = PipelineTask(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=RTC_ASR_SAMPLE_RATE,
                audio_out_sample_rate=24000,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            conversation_id=state.session_id,
            idle_timeout_secs=None,
        )

        @live.pipeline_task.event_handler('on_pipeline_error')
        async def _on_pipeline_error(task: Any, frame: Any) -> None:
            raw_error = getattr(frame, 'error', None) or str(frame)
            error_text = str(raw_error)
            live.runtime_status = 'error'
            live.pipeline_ready = False
            live.state = 'error'
            live.last_error = error_text
            live.add_event(
                'runtime_provider_error',
                error=error_text,
                fatal=bool(getattr(frame, 'fatal', False)),
                processor=str(getattr(frame, 'processor', '') or ''),
            )
            session_state = SESSIONS.get(live.session_id)
            if session_state:
                session_state.status = 'error'
                session_state.agent_status = 'provider_error'
                session_state.live_session = _serialize_live_session(live)
                session_state.frontend_contract = _build_agent_contract(session_state)

        live.pipeline_runner = PipelineRunner(handle_sigint=False, handle_sigterm=False)
        live.runtime_task = asyncio.create_task(_run_live_pipeline(live))
        live.runtime_status = 'running'
        live.pipeline_ready = True
        live.last_error = None
        live.add_event(
            'runtime_started',
            openai_enabled=bool(llm),
            video_enabled=live.video_pipeline_enabled,
            heygen_enabled=_heygen_video_service_enabled(),
            asr_status=(live.asr_contract or _build_asr_contract(status='unknown'))['status'],
        )
        if _heygen_video_service_enabled():
            await _mark_heygen_video_ready(live)
    except Exception as exc:
        live.runtime_status = 'error'
        live.pipeline_ready = False
        live.last_error = str(exc)
        live.add_event('runtime_start_failed', error=str(exc))
        try:
            await live.webrtc.connect()
        except Exception:
            pass


async def _run_live_pipeline(live: LivePresenterSession) -> None:
    try:
        await live.pipeline_runner.run(live.pipeline_task)
        if live.runtime_status == 'running':
            live.runtime_status = 'ended'
            live.pipeline_ready = False
            live.add_event('runtime_ended')
    except asyncio.CancelledError:
        live.runtime_status = 'cancelled'
        live.pipeline_ready = False
        live.add_event('runtime_cancelled')
        raise
    except Exception as exc:
        live.runtime_status = 'error'
        live.pipeline_ready = False
        live.last_error = str(exc)
        live.add_event('runtime_failed', error=str(exc))
    finally:
        if live.aiohttp_session and not live.aiohttp_session.closed:
            await live.aiohttp_session.close()
        live.aiohttp_session = None


async def _stop_live_runtime(live: LivePresenterSession) -> None:
    if live.pipeline_task:
        try:
            await live.pipeline_task.cancel(reason='live session stopped')
        except Exception:
            pass
    if live.pipeline_runner:
        try:
            await live.pipeline_runner.cancel()
        except Exception:
            pass
    if live.runtime_task and not live.runtime_task.done():
        live.runtime_task.cancel()
        try:
            await asyncio.wait_for(live.runtime_task, timeout=5)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    if live.aiohttp_session and not live.aiohttp_session.closed:
        await live.aiohttp_session.close()
    live.runtime_task = None
    live.pipeline_task = None
    live.pipeline_runner = None
    live.heygen_service = None
    live.aiohttp_session = None
    live.pipeline_ready = False
    live.heygen_ready = False
    live.video_pipeline_enabled = False
    live.runtime_status = 'ended'
    await live.webrtc.disconnect()


def _build_openai_realtime_service(state: PipecatSessionState) -> Any:
    session_properties = SessionProperties(
        model=state.realtime.get('model') or OPENAI_REALTIME_MODEL,
        output_modalities=['audio'],
        instructions=state.instructions,
        tools=_build_tools_schema(state.tool_manifest),
        tool_choice='auto' if state.tool_manifest else None,
        audio=AudioConfiguration(
            input=AudioInput(
                transcription=InputAudioTranscription(language='en', prompt=None),
                turn_detection=TurnDetection(type='server_vad', threshold=0.5, silence_duration_ms=600),
            ),
            output=AudioOutput(voice=(state.realtime.get('voice') or 'alloy')),
        ),
    )
    return OpenAIRealtimeLLMService(
        api_key=os.environ['OPENAI_API_KEY'],
        settings=OpenAIRealtimeLLMService.Settings(
            model=state.realtime.get('model') or OPENAI_REALTIME_MODEL,
            session_properties=session_properties,
        ),
        function_call_timeout_secs=8.0,
    )



def _build_tools_schema(tool_manifest: list[dict[str, Any]]) -> Any | None:
    if not tool_manifest:
        return None
    schemas = []
    for tool in tool_manifest:
        parameters = tool.get('parameters') if isinstance(tool.get('parameters'), dict) else {}
        schemas.append(
            FunctionSchema(
                name=str(tool.get('name')),
                description=str(tool.get('description') or ''),
                properties=parameters.get('properties') if isinstance(parameters.get('properties'), dict) else {},
                required=parameters.get('required') if isinstance(parameters.get('required'), list) else [],
            )
        )
    return ToolsSchema(standard_tools=schemas)


def _register_realtime_tools(llm: Any, state: PipecatSessionState) -> None:
    handlers: dict[str, Callable[[Any], Awaitable[None]]] = {
        str(tool.get('name')): _make_realtime_tool_handler(state.session_id)
        for tool in state.tool_manifest
        if isinstance(tool.get('name'), str)
    }
    for name, handler in handlers.items():
        llm.register_function(name, handler)


def _make_realtime_tool_handler(session_id: str) -> Callable[[Any], Awaitable[None]]:
    async def handler(params: FunctionCallParams) -> None:
        result = await asyncio.to_thread(_dispatch_tool_call, session_id, params.function_name, params.arguments)
        await params.result_callback(result)

    return handler


def _coerce_ice_candidate(candidate: dict[str, Any]) -> Any:
    raw_candidate = candidate.get('candidate')
    if not raw_candidate:
        return None
    if candidate_from_sdp is None:
        return candidate
    candidate_sdp = str(raw_candidate).removeprefix('candidate:')
    parts = candidate_sdp.split()
    if (
        BROWSER_ICE_HOST_OVERRIDE
        and len(parts) > 4
        and parts[4].lower().endswith('.local')
    ):
        # Chromium masks host candidates with an mDNS name. A Linux
        # container cannot resolve that browser-local name, but it can reach
        # the browser's UDP socket through Docker's host gateway.
        parts[4] = BROWSER_ICE_HOST_OVERRIDE
        candidate_sdp = ' '.join(parts)
    parsed = candidate_from_sdp(candidate_sdp)
    parsed.sdpMid = candidate.get('sdpMid') if 'sdpMid' in candidate else candidate.get('sdp_mid')
    parsed.sdpMLineIndex = candidate.get('sdpMLineIndex') if 'sdpMLineIndex' in candidate else candidate.get('sdp_mline_index')
    return parsed


def _build_agent_contract(state: PipecatSessionState) -> dict[str, Any]:
    current_slide_index = state.contract.get('session', {}).get('current_slide_index') if isinstance(state.contract.get('session'), dict) else None
    return {
        'status': state.status,
        'sessionId': state.session_id,
        'publicToken': state.public_token,
        'connected': state.connected,
        'agent_status': state.agent_status,
        'transport_mode': state.transport_mode,
        'orchestration': {
            'provider': 'pipecat',
            'authority': 'pipecat',
            'state_authority': 'fastapi',
            'fake_ask_is_test_harness': True,
        },
        'instructions': state.instructions,
        'tool_manifest': state.tool_manifest,
        'avatar': None,
        'realtime': {
            **state.realtime,
            'session_id': state.session_id,
            'public_token': state.public_token,
        },
        'live_session': state.live_session,
        'live_transport': {
            'provider': 'smallwebrtc',
            'create_url': f'/sessions/{state.session_id}/live/create',
            'join_url': f'/sessions/{state.session_id}/live/join',
            'ice_url': f'/sessions/{state.session_id}/live/ice',
            'state_url': f'/sessions/{state.session_id}/live/state',
            'stop_url': f'/sessions/{state.session_id}/live/stop',
            'heygen_start_url': f'/sessions/{state.session_id}/heygen/start',
        },
        'tool_state': state.tool_state,
        'current_slide_index': current_slide_index,
        'nextStep': 'Create or join the Pipecat SmallWebRTC live session, then let the Pipecat-driven tool/prompt loop manage slide-aware behavior.',
    }


def _build_bootstrap_response(state: PipecatSessionState) -> dict[str, Any]:
    return {
        'status': 'ready',
        'orchestrator': 'pipecat',
        'sessionId': state.session_id,
        'publicToken': state.public_token,
        'voice': {
            'status': 'idle',
            'mode': 'pipecat-orchestrated',
            'asr': _build_asr_contract(status='configured' if RTC_ASR_BASE_URL else 'not_configured'),
            'start_endpoint': f'/sessions/{state.session_id}/connect',
            'ask_endpoint': f'/sessions/{state.session_id}/ask',
            'stop_endpoint': f'/sessions/{state.session_id}/disconnect',
        },
        'realtime': {
            **state.realtime,
            'enabled': bool(state.realtime.get('enabled')),
            'session_id': state.session_id,
            'public_token': state.public_token,
            'connect_url': f'/sessions/{state.session_id}/connect',
            'status': 'configured' if state.realtime.get('enabled') else 'needs_config',
            'bridge_configured': True,
            'browser_direct_supported': False,
            'tool_manifest': state.tool_manifest,
        },
        'transport': {
            'provider': 'pipecat',
            'mode': 'server-orchestrated',
            'model': state.realtime.get('model') or OPENAI_REALTIME_MODEL,
            'connect_url': f'/sessions/{state.session_id}/connect',
        },
        'avatar': None,
        'tool_manifest': state.tool_manifest,
        'pipecat_plan': state.pipecat_plan,
        'agent': _build_agent_contract(state),
        'nextStep': 'Start the Pipecat agent session, then attach browser live transport and use prompt + tools for slide-aware behavior.',
    }
