from __future__ import annotations

import asyncio
import math
import os
import struct
import time
import uuid
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


AudioTransportId = Literal['none', 'local_pipecat_webrtc', 'sip_verto']
AudioDirection = Literal['caller_to_target', 'target_to_caller']

DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1
DEFAULT_FRAME_MS = 20
PCM_S16LE = 'pcm_s16le'


class ExecutionAudioCapabilities(BaseModel):
    model_config = ConfigDict(extra='forbid')

    transports: list[dict[str, Any]]
    default_transport: AudioTransportId = 'none'
    live_pipecat_configured: bool = False
    freeswitch_required: bool = False
    notes: list[str] = Field(default_factory=list)


class ExecutionAudioSessionProof(BaseModel):
    model_config = ConfigDict(extra='allow')

    session_id: str
    transport: AudioTransportId
    provider: str
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    format: str = PCM_S16LE
    frame_ms: int = DEFAULT_FRAME_MS
    frames_sent: int = 0
    frames_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    negotiated: bool = False
    closed: bool = False
    offer_type: str | None = None
    answer_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionAudioFrame(BaseModel):
    model_config = ConfigDict(extra='forbid')

    direction: AudioDirection
    pcm: bytes
    sequence: int
    timestamp_ms: float


class ExecutionAudioSession(Protocol):
    """Duplex audio session used while an execution run is active."""

    @property
    def session_id(self) -> str:
        ...

    @property
    def transport_id(self) -> AudioTransportId:
        ...

    async def negotiate(self, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        ...

    async def send_pcm(
        self,
        frames: AsyncIterable[bytes],
        *,
        direction: AudioDirection = 'caller_to_target',
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    async def receive_pcm(self, *, max_frames: int | None = None) -> list[ExecutionAudioFrame]:
        ...

    async def inject_remote_pcm(self, frames: AsyncIterable[bytes]) -> dict[str, Any]:
        """Simulate or feed target→caller audio (test harness / live receive path)."""

    async def close(self, *, reason: str = 'execution_complete') -> ExecutionAudioSessionProof:
        ...

    def proof(self) -> ExecutionAudioSessionProof:
        ...


def pcm_silence_frames(
    *,
    duration_ms: int,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    frame_ms: int = DEFAULT_FRAME_MS,
) -> list[bytes]:
    """Build paced PCM16 silence frames for offline WebRTC hook tests."""

    duration_ms = max(0, int(duration_ms))
    if duration_ms == 0:
        samples_per_frame = int(sample_rate * frame_ms / 1000) * channels
        return [b'\x00' * (samples_per_frame * 2)]

    frame_count = max(1, (duration_ms + frame_ms - 1) // frame_ms)
    samples_per_frame = int(sample_rate * frame_ms / 1000) * channels
    frame = b'\x00' * (samples_per_frame * 2)
    return [frame for _ in range(frame_count)]


def pcm_tone_frames(
    *,
    duration_ms: int,
    frequency_hz: float = 440.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    frame_ms: int = DEFAULT_FRAME_MS,
    amplitude: float = 0.2,
) -> list[bytes]:
    """Build a short tone for audible smoke tests when a real sink is attached."""

    frames = pcm_silence_frames(
        duration_ms=duration_ms,
        sample_rate=sample_rate,
        channels=channels,
        frame_ms=frame_ms,
    )
    samples_per_frame = int(sample_rate * frame_ms / 1000)
    out: list[bytes] = []
    sample_index = 0
    for _ in frames:
        values: list[int] = []
        for _sample in range(samples_per_frame):
            phase = 2.0 * math.pi * frequency_hz * (sample_index / sample_rate)
            sample = int(max(-1.0, min(1.0, math.sin(phase) * amplitude)) * 32767)
            for _channel in range(channels):
                values.append(sample)
            sample_index += 1
        out.append(struct.pack(f'<{len(values)}h', *values))
    return out


class LocalPipecatSmallWebRtcSession:
    """In-process duplex audio bridge shaped like Pipecat SmallWebRTC.

    ConversationAgentEvals can exercise caller→target and target→caller PCM hooks during
    execution without FreeSWITCH, a browser, or a live Pipecat process. Signaling is a
    local loopback SDP stub so default CI stays offline.

    ACC mapping (later):
      CAE fixture/tester PCM
        -> Pipecat SmallWebRTC (this session shape)
        -> FreeSWITCH Verto outbound SIP leg (SipVertoOutboundExtension)
        -> target phone/SIP endpoint
    """

    transport_id: AudioTransportId = 'local_pipecat_webrtc'
    provider = 'pipecat.smallwebrtc.local'

    def __init__(
        self,
        *,
        session_id: str | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        frame_ms: int = DEFAULT_FRAME_MS,
        metadata: dict[str, Any] | None = None,
    ):
        self.session_id = session_id or f'exec-audio-{uuid.uuid4().hex[:12]}'
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_ms = frame_ms
        self.metadata = dict(metadata or {})
        self._receive_q: asyncio.Queue[ExecutionAudioFrame] = asyncio.Queue()
        self._frames_sent = 0
        self._frames_received = 0
        self._bytes_sent = 0
        self._bytes_received = 0
        self._negotiated = False
        self._closed = False
        self._offer_type: str | None = None
        self._answer_type: str | None = None
        self._seq = 0

    async def negotiate(self, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError('audio session is closed')
        if metadata:
            self.metadata.update(metadata)
        self._offer_type = 'offer'
        self._answer_type = 'answer'
        self._negotiated = True
        return {
            'session_id': self.session_id,
            'transport': self.transport_id,
            'provider': self.provider,
            'offer': {
                'type': self._offer_type,
                'sdp': (
                    'v=0\\r\\no=- 0 0 IN IP4 127.0.0.1\\r\\ns=cae-local-smallwebrtc\\r\\n'
                    f'a=cae-session:{self.session_id}\\r\\n'
                ),
            },
            'answer': {
                'type': self._answer_type,
                'sdp': (
                    'v=0\\r\\no=- 0 0 IN IP4 127.0.0.1\\r\\ns=cae-local-smallwebrtc-answer\\r\\n'
                    f'a=cae-session:{self.session_id}\\r\\n'
                ),
            },
            'ice': {'candidates': [], 'gathering_complete': True},
            'media': {
                'sampleRate': self.sample_rate,
                'channels': self.channels,
                'format': PCM_S16LE,
                'frameMs': self.frame_ms,
            },
            'metadata': self.metadata,
        }

    async def send_pcm(
        self,
        frames: AsyncIterable[bytes],
        *,
        direction: AudioDirection = 'caller_to_target',
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError('audio session is closed')
        if not self._negotiated:
            await self.negotiate(metadata=metadata)
        frame_count = 0
        audio_bytes = 0
        async for frame in frames:
            if not frame:
                continue
            self._seq += 1
            self._frames_sent += 1
            self._bytes_sent += len(frame)
            frame_count += 1
            audio_bytes += len(frame)
        return {
            'session_id': self.session_id,
            'frame_count': frame_count,
            'audio_bytes': audio_bytes,
            'direction': direction,
        }

    async def receive_pcm(self, *, max_frames: int | None = None) -> list[ExecutionAudioFrame]:
        if self._closed:
            raise RuntimeError('audio session is closed')
        received: list[ExecutionAudioFrame] = []
        while True:
            if max_frames is not None and len(received) >= max_frames:
                break
            try:
                item = self._receive_q.get_nowait()
            except asyncio.QueueEmpty:
                break
            received.append(item)
            self._frames_received += 1
            self._bytes_received += len(item.pcm)
        return received

    async def inject_remote_pcm(self, frames: AsyncIterable[bytes]) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError('audio session is closed')
        if not self._negotiated:
            await self.negotiate()
        frame_count = 0
        audio_bytes = 0
        now = time.time() * 1000
        async for frame in frames:
            if not frame:
                continue
            self._seq += 1
            await self._receive_q.put(
                ExecutionAudioFrame(
                    direction='target_to_caller',
                    pcm=frame,
                    sequence=self._seq,
                    timestamp_ms=now + frame_count * self.frame_ms,
                )
            )
            frame_count += 1
            audio_bytes += len(frame)
        return {
            'session_id': self.session_id,
            'frame_count': frame_count,
            'audio_bytes': audio_bytes,
            'direction': 'target_to_caller',
        }

    async def close(self, *, reason: str = 'execution_complete') -> ExecutionAudioSessionProof:
        self._closed = True
        self.metadata['close_reason'] = reason
        return self.proof()

    def proof(self) -> ExecutionAudioSessionProof:
        return ExecutionAudioSessionProof(
            session_id=self.session_id,
            transport=self.transport_id,
            provider=self.provider,
            sample_rate=self.sample_rate,
            channels=self.channels,
            format=PCM_S16LE,
            frame_ms=self.frame_ms,
            frames_sent=self._frames_sent,
            frames_received=self._frames_received,
            bytes_sent=self._bytes_sent,
            bytes_received=self._bytes_received,
            negotiated=self._negotiated,
            closed=self._closed,
            offer_type=self._offer_type,
            answer_type=self._answer_type,
            metadata=dict(self.metadata),
        )


class SipVertoOutboundExtension:
    """Deferred FreeSWITCH Verto outbound SIP extension point.

    ACC pattern this will mirror later:
      Pipecat SmallWebRTC (local duplex audio)
        <-> FreeSWITCH Verto (outbound SIP leg)
        <-> PSTN / SIP target

    This class is intentionally not wired into default CI. Callers discover it through
    capabilities and must not assume live SIP.
    """

    transport_id: AudioTransportId = 'sip_verto'
    provider = 'freeswitch.verto.outbound'
    status = 'deferred'

    def __init__(self, *, verto_url: str | None = None, sip_destination: str | None = None):
        self.verto_url = verto_url or os.getenv('FREESWITCH_VERTO_URL')
        self.sip_destination = sip_destination or os.getenv('SIP_DESTINATION')

    def capabilities(self) -> dict[str, Any]:
        return {
            'id': self.transport_id,
            'provider': self.provider,
            'status': self.status,
            'available': False,
            'requires': ['freeswitch', 'verto', 'pipecat_small_webrtc'],
            'configured': bool(self.verto_url and self.sip_destination),
            'next_step': (
                'After local SmallWebRTC in/out is stable, attach a Verto outbound dial '
                'that bridges the same PCM session to a SIP destination. Do not enable in default CI.'
            ),
        }

    async def create_session(self, *_args: Any, **_kwargs: Any) -> ExecutionAudioSession:
        raise NotImplementedError(
            'sip_verto outbound SIP is deferred. Use audio_transport=local_pipecat_webrtc '
            'for execution audio hooks; configure FreeSWITCH Verto in a later PR.'
        )


def describe_execution_audio_capabilities() -> ExecutionAudioCapabilities:
    live_pipecat = bool(os.getenv('PIPECAT_SERVICE_URL') or os.getenv('NEXT_PUBLIC_PIPECAT_SERVICE_URL'))
    sip = SipVertoOutboundExtension()
    return ExecutionAudioCapabilities(
        default_transport='none',
        live_pipecat_configured=live_pipecat,
        freeswitch_required=False,
        transports=[
            {
                'id': 'none',
                'provider': 'none',
                'status': 'available',
                'available': True,
                'description': 'No execution-time audio stream; text/fixture-only runs.',
            },
            {
                'id': 'local_pipecat_webrtc',
                'provider': LocalPipecatSmallWebRtcSession.provider,
                'status': 'available',
                'available': True,
                'description': (
                    'Local Pipecat SmallWebRTC-shaped duplex PCM hooks during execution. '
                    'Default path is in-process loopback; optional live Pipecat signaling is future work.'
                ),
                'requires_live_pipecat': False,
                'requires_freeswitch': False,
            },
            sip.capabilities(),
        ],
        notes=[
            'Default CI must not require FreeSWITCH, live SIP, or a browser WebRTC client.',
            'ACC mirrors: Pipecat + SmallWebRTC for media, FreeSWITCH Verto for outbound SIP.',
            'This slice ships local SmallWebRTC audio in/out hooks; sip_verto remains an extension point.',
        ],
    )


def create_execution_audio_session(
    transport: AudioTransportId,
    *,
    metadata: dict[str, Any] | None = None,
) -> ExecutionAudioSession:
    if transport == 'none':
        raise ValueError('audio transport "none" does not create a media session')
    if transport == 'sip_verto':
        extension = SipVertoOutboundExtension()
        raise NotImplementedError(extension.capabilities()['next_step'])
    if transport == 'local_pipecat_webrtc':
        return LocalPipecatSmallWebRtcSession(metadata=metadata)
    raise ValueError(f'Unknown audio transport: {transport}')


class WebRtcBackedVoiceTarget:
    """Voice fixture target that paces caller PCM through a SmallWebRTC-shaped session.

    Semantic inject responses stay fixture-compatible so AccAudioFixtureScheduler and
    cancellation-rescue scoring keep working. Media proof is attached separately.
    """

    def __init__(
        self,
        audio_session: ExecutionAudioSession,
        *,
        observe_events: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    ):
        self.audio_session = audio_session
        self._observe_events = observe_events
        self.media_steps: list[dict[str, Any]] = []

    async def observe_events(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        if self._observe_events is not None:
            return await self._observe_events(session_id, cursor=cursor)
        return {
            'events': [
                {'type': 'agent_response_completed', 'detail': {'text': 'Ready for next act.'}},
                {'type': 'agent_tts_started', 'detail': {}},
                {'type': 'operator_steer_applied', 'detail': {'action': 'approve_offer'}},
            ],
            'next_cursor': 'cursor-1',
        }

    async def inject_audio(
        self,
        session_id: str,
        *,
        fixture: Any,
        step: Any,
        scenario_id: str,
        seed: int,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        duration_ms = int(getattr(fixture, 'duration_ms', None) or 40)
        outbound = pcm_silence_frames(duration_ms=min(duration_ms, 200))

        async def outbound_iter() -> AsyncIterable[bytes]:
            for frame in outbound:
                yield frame

        send_summary = await self.audio_session.send_pcm(
            outbound_iter(),
            direction='caller_to_target',
            metadata={
                'fixture_id': fixture.fixture_id,
                'step_id': step.step_id,
                'scenario_id': scenario_id,
                'seed': seed,
            },
        )

        inbound = pcm_silence_frames(duration_ms=20)

        async def inbound_iter() -> AsyncIterable[bytes]:
            for frame in inbound:
                yield frame

        receive_summary = await self.audio_session.inject_remote_pcm(inbound_iter())
        received = await self.audio_session.receive_pcm()
        step_proof = {
            'session_id': session_id,
            'fixture_id': fixture.fixture_id,
            'step_id': step.step_id,
            'send': send_summary,
            'remote_inject': receive_summary,
            'received_frames': len(received),
            'provenance': provenance,
        }
        self.media_steps.append(step_proof)
        utterance = (
            (fixture.metadata or {}).get('text_reference')
            or (fixture.metadata or {}).get('rendered_text')
            or step.expected_caller_act
        )
        return {
            'accepted': True,
            'session_id': session_id,
            'fixture_id': fixture.fixture_id,
            'utterance': utterance,
            'scenario_id': scenario_id,
            'seed': seed,
            'provenance': provenance,
            'audio': step_proof,
        }
