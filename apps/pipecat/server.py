from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_env_candidates = [*Path(__file__).resolve().parents, Path.cwd()]
for _parent in _env_candidates:
    _env_path = _parent / '.env'
    if _env_path.exists():
        load_dotenv(_env_path)
        break

try:
    import aiohttp
    from aiortc import RTCSessionDescription
    from aiortc.sdp import candidate_from_sdp
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.frames.frames import ErrorFrame, LLMContextFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
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
    from pipecat.services.heygen.api_liveavatar import LiveAvatarNewSessionRequest
    from pipecat.services.heygen.client import ServiceType
    from pipecat.services.heygen.video import HeyGenVideoService
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.transport import RawAudioTrack, SmallWebRTCTransport
    PIPECAT_RUNTIME_AVAILABLE = True
except Exception:  # pragma: no cover - fallback for non-pipecat test envs
    aiohttp = None  # type: ignore[assignment]
    RTCSessionDescription = None  # type: ignore[assignment]
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
    LLMContext = None  # type: ignore[assignment]
    LLMContextFrame = None  # type: ignore[assignment]
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
HEYGEN_LIVE_AVATAR_API_KEY = os.getenv('HEYGEN_LIVE_AVATAR_API_KEY') or os.getenv('HEYGEN_API_KEY')
HEYGEN_AVATAR_ID = os.getenv('HEYGEN_AVATAR_ID', 'dd73ea75-1218-4ef3-92ce-606d5f7fbc0a')
HEYGEN_SANDBOX = os.getenv('HEYGEN_SANDBOX', 'true').lower() == 'true'
HEYGEN_SANDBOX_AVATAR_ID = os.getenv('HEYGEN_SANDBOX_AVATAR_ID', 'dd73ea75-1218-4ef3-92ce-606d5f7fbc0a')
HEYGEN_VIDEO_WIDTH = int(os.getenv('HEYGEN_VIDEO_WIDTH', '640'))
HEYGEN_VIDEO_HEIGHT = int(os.getenv('HEYGEN_VIDEO_HEIGHT', '360'))


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
    openai_ready: bool = False
    video_ready: bool = False
    pipeline_ready: bool = False
    video_pipeline_enabled: bool = False
    heygen_ready: bool = False
    heygen_join: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_error: str | None = None
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

    live = LivePresenterSession(
        session_id=session_id,
        public_token=state.public_token,
        webrtc=LivePresenterWebRTCConnection(video_out_enabled=video_ready),
        state='connecting',
        openai_ready=bool(os.getenv('OPENAI_API_KEY')),
        video_ready=video_ready,
    )
    live.add_event('live_session_created', openai_ready=live.openai_ready, video_ready=live.video_ready)
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
            openai_ready=bool(os.getenv('OPENAI_API_KEY')),
            video_ready=True,
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
            audio_in_sample_rate=24000,
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
                audio_in_sample_rate=24000,
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
    parsed = candidate_from_sdp(str(raw_candidate).removeprefix('candidate:'))
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
