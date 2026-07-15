"""Execution-time audio transports for Pipecat small WebRTC (and later Verto SIP).

Maps to the ACC pattern:

```text
CAE tester / audio plan
  -> ExecutionAudioTransport
       - pipecat_small_webrtc (this slice: local/mocked)
       - freeswitch_verto_sip (deferred extension point)
  -> target session send/receive
  -> recording + transcription handles
  -> vCon evidence export
```

Live FreeSWITCH/SIP is intentionally not required for CI. The Verto transport
documents the plug-in surface and refuses to run until implemented.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.services.acc_realtime_target import AccAudioFixture, AccAudioStep


AudioTransportId = Literal['none', 'pipecat_small_webrtc', 'freeswitch_verto_sip']


class ExecutionAudioTransportInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: AudioTransportId
    label: str
    available: bool
    status: Literal['available', 'deferred']
    requires_freeswitch: bool = False
    requires_live_pipecat: bool = False
    notes: str = ''


class ExecutionAudioCapabilities(BaseModel):
    model_config = ConfigDict(extra='forbid')

    default_transport: AudioTransportId = 'none'
    default_execution_mode: str = 'pipecat_webrtc'
    freeswitch_required: bool = False
    vcon_capture: bool = True
    transports: list[ExecutionAudioTransportInfo] = Field(default_factory=list)
    extension_points: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def describe_execution_audio_capabilities() -> ExecutionAudioCapabilities:
    return ExecutionAudioCapabilities(
        transports=[
            ExecutionAudioTransportInfo(
                id='none',
                label='No execution audio transport',
                available=True,
                status='available',
                notes='Used by text_callable and voice_fixture modes that do not stream media.',
            ),
            ExecutionAudioTransportInfo(
                id='pipecat_small_webrtc',
                label='Local Pipecat small WebRTC',
                available=True,
                status='available',
                requires_freeswitch=False,
                requires_live_pipecat=False,
                notes=(
                    'In-process send/receive + recording/transcription hooks used during '
                    'execution mode pipecat_webrtc. No browser peer or live Pipecat process '
                    'required for CI.'
                ),
            ),
            ExecutionAudioTransportInfo(
                id='freeswitch_verto_sip',
                label='FreeSWITCH Verto outbound SIP',
                available=False,
                status='deferred',
                requires_freeswitch=True,
                requires_live_pipecat=True,
                notes=(
                    'Deferred. Implement Verto login/call and bridge SIP media into the same '
                    'Pipecat small WebRTC send/receive + vCon capture surface.'
                ),
            ),
        ],
        extension_points={
            'freeswitch_verto_sip': {
                'status': 'deferred',
                'transport_class': 'FreeSwitchVertoSipTransport',
                'plug_in': [
                    'negotiate Verto WebSocket against FreeSWITCH',
                    'bridge SIP RTP into LocalPipecatSmallWebRtcTransport send/receive hooks',
                    'emit the same AudioRecordingHandle + TranscriptionTurn + vCon shape',
                ],
            },
            'vcon_capture': {
                'status': 'available',
                'module': 'app.services.execution_vcon',
                'analysis_type': 'execution_audio_capture',
                'reuses': 'benchmark_service._vcon_export',
            },
        },
        notes=[
            'Default CI must not require FreeSWITCH, live SIP, or a browser WebRTC client.',
            'ACC mirrors: Pipecat + SmallWebRTC for media, FreeSWITCH Verto for outbound SIP.',
            'Use execution mode pipecat_webrtc with audio_transport=pipecat_small_webrtc for vCon capture.',
            'freeswitch_verto_sip remains a deferred extension point (see FreeSwitchVertoSipTransport).',
        ],
    )


@dataclass(slots=True)
class AudioRecordingHandle:
    """Recording pointer emitted after an execution audio session closes."""

    uri: str
    mime_type: str = 'audio/wav'
    sha256: str | None = None
    duration_ms: int | None = None
    bytes_captured: int = 0
    transport: Literal['pipecat_small_webrtc', 'freeswitch_verto_sip'] = 'pipecat_small_webrtc'
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_call_media(self) -> dict[str, Any]:
        media: dict[str, Any] = {
            'recording_url': self.uri,
            'mime_type': self.mime_type,
            'transport': self.transport,
        }
        if self.sha256:
            media['recording_sha256'] = self.sha256
        if self.duration_ms is not None:
            media['duration_ms'] = self.duration_ms
        if self.bytes_captured:
            media['bytes_captured'] = self.bytes_captured
        if self.metadata:
            media['metadata'] = dict(self.metadata)
        return media


@dataclass(slots=True)
class TranscriptionTurn:
    turn_index: int
    speaker: str
    text: str
    act_id: str | None = None
    source: str = 'pipecat_execution'
    event_types: list[str] = field(default_factory=list)

    def as_dialog_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            'speaker': self.speaker,
            'text': self.text,
            'role': self.speaker.lower(),
        }
        if self.act_id:
            item['act_id'] = self.act_id
        if self.event_types:
            item['event_types'] = list(self.event_types)
        item['source'] = self.source
        return item


class ExecutionAudioTransport(Protocol):
    transport_id: AudioTransportId

    async def connect(self, session_id: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        ...

    async def send_audio(
        self,
        session_id: str,
        *,
        fixture: AccAudioFixture,
        step: AccAudioStep,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    async def receive_audio(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        ...

    async def start_recording(self, session_id: str) -> dict[str, Any]:
        ...

    async def stop_recording(self, session_id: str) -> AudioRecordingHandle:
        ...

    async def disconnect(self, session_id: str, *, reason: str = 'tester_complete') -> dict[str, Any]:
        ...


@dataclass
class _SessionState:
    session_id: str
    metadata: dict[str, Any]
    outbound: list[dict[str, Any]] = field(default_factory=list)
    inbound: list[dict[str, Any]] = field(default_factory=list)
    transcription: list[TranscriptionTurn] = field(default_factory=list)
    recording_active: bool = False
    recording_chunks: list[bytes] = field(default_factory=list)
    recording: AudioRecordingHandle | None = None
    closed: bool = False
    agent_turn: int = 0


class LocalPipecatSmallWebRtcTransport:
    """Local/mocked Pipecat small WebRTC audio send/receive for execution runs.

    This does not open a browser peer connection or contact FreeSWITCH. It
    exercises the same send/receive/recording hooks that a live Pipecat small
    WebRTC path would own, so CI can prove capture without live media.
    """

    transport_id: Literal['pipecat_small_webrtc'] = 'pipecat_small_webrtc'

    def __init__(
        self,
        *,
        artifact_dir: Path | None = None,
        agent_replies: list[str] | None = None,
    ):
        self.artifact_dir = artifact_dir
        self.agent_replies = agent_replies or [
            'I can help with that cancellation request.',
            'I preserved the cancellation intent and noted the renewal concern.',
            'I recorded the approved follow-up and closed the call.',
        ]
        self._sessions: dict[str, _SessionState] = {}

    async def connect(self, session_id: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        state = _SessionState(session_id=session_id, metadata=dict(metadata or {}))
        self._sessions[session_id] = state
        await self.start_recording(session_id)
        return {
            'session_id': session_id,
            'transport': self.transport_id,
            'webrtc': {
                'mode': 'local_small_webrtc',
                'sendrecv': True,
                'ice': 'loopback-mock',
            },
            'recording': {'active': True},
            'ready': True,
        }

    async def send_audio(
        self,
        session_id: str,
        *,
        fixture: AccAudioFixture,
        step: AccAudioStep,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._require(session_id)
        if state.closed:
            raise RuntimeError(f'audio session already closed: {session_id}')
        utterance = str(
            fixture.metadata.get('rendered_text')
            or fixture.metadata.get('text_reference')
            or step.metadata.get('utterance')
            or step.expected_caller_act
        )
        synthetic = f'{fixture.fixture_id}:{utterance}'.encode('utf-8')
        if state.recording_active:
            state.recording_chunks.append(synthetic)
        outbound = {
            'direction': 'outbound',
            'fixture_id': fixture.fixture_id,
            'uri': fixture.uri,
            'expected_caller_act': step.expected_caller_act,
            'utterance': utterance,
            'bytes': len(synthetic),
            'step_id': step.step_id,
            'barge_in': step.barge_in,
            'provenance': provenance,
        }
        state.outbound.append(outbound)
        turn_index = len(state.transcription) + 1
        state.transcription.append(
            TranscriptionTurn(
                turn_index=turn_index,
                speaker='Caller',
                text=utterance,
                act_id=step.expected_caller_act,
                event_types=['webrtc_audio_sent'],
            )
        )
        # Queue a local inbound agent frame so receive_audio is exercised.
        reply = self.agent_replies[min(state.agent_turn, len(self.agent_replies) - 1)]
        state.agent_turn += 1
        inbound_bytes = f'agent:{reply}'.encode('utf-8')
        if state.recording_active:
            state.recording_chunks.append(inbound_bytes)
        state.inbound.append(
            {
                'direction': 'inbound',
                'text': reply,
                'bytes': len(inbound_bytes),
                'frame_id': f'in-{len(state.inbound) + 1}',
            }
        )
        return {
            'accepted': True,
            'session_id': session_id,
            'transport': self.transport_id,
            'fixture_id': fixture.fixture_id,
            'utterance': utterance,
            'webrtc_send': {'frames': 1, 'bytes': len(synthetic)},
        }

    async def receive_audio(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        state = self._require(session_id)
        start = int(cursor) if cursor and str(cursor).isdigit() else 0
        frames = state.inbound[start:]
        texts = [str(frame.get('text') or '') for frame in frames if frame.get('text')]
        if texts:
            agent_text = texts[-1]
            state.transcription.append(
                TranscriptionTurn(
                    turn_index=len(state.transcription) + 1,
                    speaker='Agent',
                    text=agent_text,
                    event_types=['webrtc_audio_received', 'agent_response_completed'],
                )
            )
        next_cursor = str(len(state.inbound))
        return {
            'session_id': session_id,
            'transport': self.transport_id,
            'frames': frames,
            'agent_text': texts[-1] if texts else None,
            'next_cursor': next_cursor,
            'events': [
                {
                    'type': 'agent_response_completed',
                    'detail': {'speaker': 'agent', 'text': texts[-1]},
                }
                for _ in texts[-1:]
            ],
        }

    async def start_recording(self, session_id: str) -> dict[str, Any]:
        state = self._require(session_id)
        state.recording_active = True
        return {'session_id': session_id, 'recording': True, 'transport': self.transport_id}

    async def stop_recording(self, session_id: str) -> AudioRecordingHandle:
        state = self._require(session_id)
        state.recording_active = False
        payload = b''.join(state.recording_chunks) or b'empty-execution-audio'
        digest = hashlib.sha256(payload).hexdigest()
        if self.artifact_dir is not None:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            path = self.artifact_dir / f'{session_id}.wav'
            # Synthetic marker file — not a real WAV bitstream; URI/hash are the contract.
            path.write_bytes(payload)
            uri = str(path)
        else:
            uri = f'fixture://execution-audio/{session_id}/{digest[:12]}.wav'
        handle = AudioRecordingHandle(
            uri=uri,
            mime_type='audio/wav',
            sha256=digest,
            duration_ms=max(250, 250 * max(1, len(state.outbound))),
            bytes_captured=len(payload),
            transport=self.transport_id,
            metadata={
                'outbound_frames': len(state.outbound),
                'inbound_frames': len(state.inbound),
                'mode': 'local_small_webrtc',
            },
        )
        state.recording = handle
        return handle

    async def disconnect(self, session_id: str, *, reason: str = 'tester_complete') -> dict[str, Any]:
        state = self._require(session_id)
        if state.recording is None:
            await self.stop_recording(session_id)
        state.closed = True
        return {
            'closed': True,
            'session_id': session_id,
            'reason': reason,
            'transport': self.transport_id,
            'recording': asdict(state.recording) if state.recording else None,
        }

    def transcription_turns(self, session_id: str) -> list[TranscriptionTurn]:
        return list(self._require(session_id).transcription)

    def recording_handle(self, session_id: str) -> AudioRecordingHandle | None:
        return self._require(session_id).recording

    def session_proof(self, session_id: str) -> dict[str, Any]:
        """Duplex PCM/WebRTC proof counters attached to execution conversations."""
        state = self._require(session_id)
        outbound_bytes = sum(int(item.get('bytes') or 0) for item in state.outbound)
        inbound_bytes = sum(int(item.get('bytes') or 0) for item in state.inbound)
        return {
            'session_id': session_id,
            'transport': self.transport_id,
            'provider': 'pipecat.smallwebrtc.local',
            'frames_sent': len(state.outbound),
            'frames_received': len(state.inbound),
            'bytes_sent': outbound_bytes,
            'bytes_received': inbound_bytes,
            'negotiated': bool(state.outbound or state.inbound),
            'closed': state.closed,
            'webrtc': {
                'mode': 'local_small_webrtc',
                'offer_type': 'offer',
                'answer_type': 'answer',
            },
        }

    def _require(self, session_id: str) -> _SessionState:
        state = self._sessions.get(session_id)
        if state is None:
            raise RuntimeError(f'unknown audio session: {session_id}')
        return state


class FreeSwitchVertoSipTransport:
    """Deferred FreeSWITCH Verto outbound SIP extension point.

    Next slice should:
    1. negotiate Verto WebSocket login/call against FreeSWITCH;
    2. bridge SIP media into the same Pipecat small WebRTC send/receive hooks;
    3. emit AudioRecordingHandle + transcription turns identical to the local path.
    """

    transport_id: Literal['freeswitch_verto_sip'] = 'freeswitch_verto_sip'

    def __init__(self, *, verto_url: str | None = None, sip_destination: str | None = None):
        self.verto_url = verto_url or os.getenv('FREESWITCH_VERTO_URL')
        self.sip_destination = sip_destination or os.getenv('SIP_DESTINATION')

    async def connect(self, session_id: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        del session_id, metadata
        raise NotImplementedError(self._message())

    async def send_audio(
        self,
        session_id: str,
        *,
        fixture: AccAudioFixture,
        step: AccAudioStep,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        del session_id, fixture, step, provenance
        raise NotImplementedError(self._message())

    async def receive_audio(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        del session_id, cursor
        raise NotImplementedError(self._message())

    async def start_recording(self, session_id: str) -> dict[str, Any]:
        del session_id
        raise NotImplementedError(self._message())

    async def stop_recording(self, session_id: str) -> AudioRecordingHandle:
        del session_id
        raise NotImplementedError(self._message())

    async def disconnect(self, session_id: str, *, reason: str = 'tester_complete') -> dict[str, Any]:
        del session_id, reason
        raise NotImplementedError(self._message())

    def _message(self) -> str:
        return (
            'FreeSWITCH Verto SIP audio is deferred. Configure LocalPipecatSmallWebRtcTransport '
            'for execution mode pipecat_webrtc, or implement Verto signaling at '
            f'verto_url={self.verto_url!r} sip_destination={self.sip_destination!r}. '
            'See docs/execution-audio-webrtc.md.'
        )


class ExecutionAudioTargetAdapter:
    """Present an ExecutionAudioTransport as the AccRealtimeTargetAdapter surface.

    PipecatTesterAgentRunner talks to create_session / inject_audio / observe_events /
    close_session / collect_proof. This adapter routes those calls through the
    selected local WebRTC (or future Verto) transport and keeps transcription/recording.
    """

    def __init__(self, transport: ExecutionAudioTransport):
        self.transport = transport
        self._proof: dict[str, Any] = {}

    async def create_session(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = f'exec-audio-{uuid.uuid4().hex[:12]}'
        connected = await self.transport.connect(session_id, metadata=metadata)
        return {
            'sessionId': session_id,
            'ready': True,
            'transport': getattr(self.transport, 'transport_id', 'unknown'),
            **connected,
        }

    async def inject_audio(
        self,
        session_id: str,
        *,
        fixture: AccAudioFixture,
        step: AccAudioStep,
        scenario_id: str,
        seed: int,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self.transport.send_audio(
            session_id,
            fixture=fixture,
            step=step,
            provenance={**provenance, 'scenario_id': scenario_id, 'seed': seed},
        )
        return {**result, 'scenario_id': scenario_id, 'seed': seed}

    async def observe_events(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        received = await self.transport.receive_audio(session_id, cursor=cursor)
        events = received.get('events') if isinstance(received.get('events'), list) else []
        agent_text = received.get('agent_text')
        return {
            'events': events,
            'agent_text': agent_text,
            'next_cursor': received.get('next_cursor'),
            'final_state': {'status': 'active', 'transport': getattr(self.transport, 'transport_id', None)},
            'terminal': False,
        }

    async def close_session(self, session_id: str, *, reason: str = 'tester_complete') -> dict[str, Any]:
        closed = await self.transport.disconnect(session_id, reason=reason)
        recording = None
        if hasattr(self.transport, 'recording_handle'):
            recording = self.transport.recording_handle(session_id)
        self._proof = {
            'sessionId': session_id,
            'transport': getattr(self.transport, 'transport_id', None),
            'reason': reason,
            'recording': asdict(recording) if recording else closed.get('recording'),
            'transcription_turns': [
                asdict(turn)
                for turn in (
                    self.transport.transcription_turns(session_id)
                    if hasattr(self.transport, 'transcription_turns')
                    else []
                )
            ],
            'proof': True,
        }
        return closed

    async def collect_proof(self, session_id: str) -> dict[str, Any]:
        if not self._proof:
            recording = None
            if hasattr(self.transport, 'recording_handle'):
                recording = self.transport.recording_handle(session_id)
            self._proof = {
                'sessionId': session_id,
                'transport': getattr(self.transport, 'transport_id', None),
                'recording': asdict(recording) if recording else None,
                'proof': True,
            }
        return dict(self._proof)


class DeterministicExecutionTtsRenderer:
    """Synthesize AccAudioFixture markers without contacting a live TTS provider."""

    async def synthesize(self, text: str, *, seed: int, metadata: dict[str, Any]) -> AccAudioFixture:
        turn_index = metadata.get('turn_index') or seed
        act_id = str(metadata.get('act_id') or 'caller_act')
        return AccAudioFixture(
            fixture_id=f'exec-tts-{turn_index}',
            uri=f'fixture://execution-tts/{turn_index}.wav',
            expected_caller_act=act_id,
            duration_ms=700,
            mime_type='audio/wav',
            metadata={
                'rendered_text': text,
                'text_reference': text,
                'seed': seed,
                **{k: v for k, v in metadata.items() if k != 'rendered_text'},
            },
        )


def build_transport(
    transport_id: Literal['pipecat_small_webrtc', 'freeswitch_verto_sip'] = 'pipecat_small_webrtc',
    *,
    artifact_dir: Path | None = None,
    verto_url: str | None = None,
    sip_destination: str | None = None,
) -> ExecutionAudioTransport:
    if transport_id == 'pipecat_small_webrtc':
        return LocalPipecatSmallWebRtcTransport(artifact_dir=artifact_dir)
    if transport_id == 'freeswitch_verto_sip':
        return FreeSwitchVertoSipTransport(verto_url=verto_url, sip_destination=sip_destination)
    raise ValueError(f'unsupported execution audio transport: {transport_id}')
