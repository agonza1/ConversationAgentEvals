"""Reference generalist target used by CAE's built-in end-to-end runs.

The tester and target are deliberately separate participants.  The tester supplies
real synthesized audio; this target transcribes it with rtc-asr, calls a configured
LLM, synthesizes the reply with Kokoro, and returns only artifacts observed in this
execution.  No saved benchmark fixture is consulted here.
"""

from __future__ import annotations

import io
import json
import os
import base64
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from app.services.acc_realtime_target import AccAudioFixture, AccAudioStep
from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
from app.services.llm_providers import get_provider


class ReferenceRuntimeError(RuntimeError):
    """Actionable fail-closed error from a required reference runtime."""


class CompletionProvider(Protocol):
    provider_id: str

    def status(self) -> dict[str, Any]: ...
    def complete(self, prompt: str, *, model_name: str | None = None) -> str: ...


@dataclass(frozen=True, slots=True)
class ReferenceRuntimeConfig:
    pipecat_service_url: str = field(
        default_factory=lambda: os.getenv('PIPECAT_SERVICE_URL', 'http://localhost:8110').rstrip('/')
    )
    internal_token: str = field(
        default_factory=lambda: os.getenv('REFERENCE_AGENT_INTERNAL_TOKEN', '').strip()
    )
    rtc_asr_base_url: str = field(
        default_factory=lambda: os.getenv('RTC_ASR_BASE_URL', '').rstrip('/')
    )
    rtc_asr_health_path: str = field(
        default_factory=lambda: '/' + (
            os.getenv('RTC_ASR_HEALTH_PATH', '').strip() or '/health'
        ).lstrip('/')
    )
    rtc_asr_backend: str = field(
        default_factory=lambda: os.getenv('REFERENCE_STT_BACKEND', 'whisper').strip().lower()
    )
    rtc_asr_model: str = field(
        default_factory=lambda: os.getenv('REFERENCE_STT_MODEL', 'base').strip()
    )
    kokoro_base_url: str = field(
        default_factory=lambda: os.getenv('KOKORO_BASE_URL', '').rstrip('/')
    )
    kokoro_model: str = field(
        default_factory=lambda: os.getenv('KOKORO_MODEL', 'kokoro').strip()
    )
    kokoro_voice: str = field(
        default_factory=lambda: os.getenv('KOKORO_VOICE', 'af_heart').strip()
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv('REFERENCE_LLM_MODEL', 'gpt-5.4-mini').strip()
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv('REFERENCE_AGENT_TIMEOUT_SECONDS', '60'))
    )


class OpenAICompatibleApiKeyProvider:
    provider_id = 'openai_compatible'

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self.api_key = os.getenv('OPENAI_API_KEY', '').strip()
        self.base_url = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
        self._client = client

    def status(self) -> dict[str, Any]:
        return {
            'status': 'connected' if self.api_key else 'disconnected',
            'provider': 'openai_compatible_api_key',
            'message': None if self.api_key else 'Set OPENAI_API_KEY or connect OpenAI/Codex OAuth.',
        }

    def complete(self, prompt: str, *, model_name: str | None = None) -> str:
        if not self.api_key:
            raise ReferenceRuntimeError('Set OPENAI_API_KEY or connect OpenAI/Codex OAuth.')
        client = self._client or httpx.Client(timeout=60)
        response = client.post(
            f'{self.base_url}/responses',
            headers={'authorization': f'Bearer {self.api_key}'},
            json={'model': model_name, 'input': prompt},
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get('output_text')
        if not text:
            chunks = []
            for item in payload.get('output') or []:
                for content in item.get('content') or []:
                    if content.get('type') in {'output_text', 'text'} and content.get('text'):
                        chunks.append(str(content['text']))
            text = '\n'.join(chunks)
        if not isinstance(text, str) or not text.strip():
            raise ReferenceRuntimeError('OpenAI-compatible provider returned no response text.')
        return text.strip()


def resolve_reference_completion_provider() -> CompletionProvider:
    api_key_provider = OpenAICompatibleApiKeyProvider()
    if api_key_provider.status()['status'] == 'connected':
        return api_key_provider
    oauth = get_provider('openai')
    if oauth.status().get('status') == 'connected':
        return oauth
    raise ReferenceRuntimeError(
        'Built-in generalist target requires an LLM. Set OPENAI_API_KEY or connect OpenAI/Codex OAuth.'
    )


class ReferenceMediaServices:
    def __init__(self, config: ReferenceRuntimeConfig, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout_seconds)

    def readiness(self) -> dict[str, Any]:
        if not self.config.rtc_asr_base_url:
            raise ReferenceRuntimeError(
                'Built-in voice target requires rtc-asr. Set RTC_ASR_BASE_URL (for example http://localhost:8080).'
            )
        if not self.config.kokoro_base_url:
            raise ReferenceRuntimeError(
                'Built-in voice target requires Kokoro. Set KOKORO_BASE_URL (for example http://localhost:8880).'
            )
        try:
            asr = self.client.get(
                f'{self.config.rtc_asr_base_url}{self.config.rtc_asr_health_path}'
            )
            asr.raise_for_status()
            asr_payload = asr.json()
        except Exception as exc:  # noqa: BLE001 - normalize service errors
            raise ReferenceRuntimeError(f'rtc-asr is unavailable at {self.config.rtc_asr_base_url}: {exc}') from exc
        try:
            kokoro = self.client.get(f'{self.config.kokoro_base_url}/health')
            kokoro.raise_for_status()
            kokoro_payload = kokoro.json()
        except Exception as exc:  # noqa: BLE001
            raise ReferenceRuntimeError(f'Kokoro is unavailable at {self.config.kokoro_base_url}: {exc}') from exc

        actual_backend = str(asr_payload.get('backend') or '').lower()
        actual_model = str(asr_payload.get('model') or '')
        requested = self.config.rtc_asr_backend
        if requested == 'whisper' and 'whisper' not in actual_backend:
            raise ReferenceRuntimeError(
                f'rtc-asr backend mismatch: requested Whisper, service reports {actual_backend or "unknown"}.'
            )
        if requested in {'parakeet', 'mlx_parakeet'} and not (
            'parakeet' in actual_backend or 'mlx' in actual_backend
        ):
            raise ReferenceRuntimeError(
                f'rtc-asr backend mismatch: requested MLX Parakeet, service reports {actual_backend or "unknown"}.'
            )
        return {
            'stt': {
                'provider': 'rtc-asr',
                'backend': actual_backend or requested,
                'model': actual_model or self.config.rtc_asr_model,
                'status': 'ready',
            },
            'tts': {
                'provider': 'kokoro',
                'model': self.config.kokoro_model,
                'voice': self.config.kokoro_voice,
                'status': str(kokoro_payload.get('status') or 'ready'),
            },
        }

    def synthesize(self, text: str) -> bytes:
        response = self.client.post(
            f'{self.config.kokoro_base_url}/v1/audio/speech',
            json={
                'model': self.config.kokoro_model,
                'voice': self.config.kokoro_voice,
                'input': text,
                'response_format': 'wav',
            },
        )
        response.raise_for_status()
        if not response.content:
            raise ReferenceRuntimeError('Kokoro returned empty audio.')
        return response.content

    def transcribe(self, wav_bytes: bytes) -> str:
        response = self.client.post(
            f'{self.config.rtc_asr_base_url}/api/transcribe/file',
            files={'file': ('turn.wav', wav_bytes, 'audio/wav')},
        )
        response.raise_for_status()
        payload = response.json()
        transcription = payload.get('transcription') if isinstance(payload.get('transcription'), dict) else {}
        text = payload.get('text') or transcription.get('text') or payload.get('transcript')
        if not isinstance(text, str) or not text.strip():
            raise ReferenceRuntimeError('rtc-asr returned no transcript for tester audio.')
        return text.strip()


class KokoroTesterTtsRenderer:
    """Pipecat tester TTS renderer that carries current-run WAV bytes in memory."""

    def __init__(self, media: ReferenceMediaServices) -> None:
        self.media = media

    async def synthesize(self, text: str, *, seed: int, metadata: dict[str, Any]) -> AccAudioFixture:
        audio = self.media.synthesize(text)
        act_id = str(metadata.get('act_id') or 'caller_act')
        return AccAudioFixture(
            fixture_id=f'reference-tester-{metadata.get("turn_index") or seed}',
            uri='memory://reference-tester/current-run.wav',
            expected_caller_act=act_id,
            mime_type='audio/wav',
            metadata={**metadata, 'rendered_text': text, 'audio_bytes': audio, 'source': 'current_run_kokoro'},
        )


@dataclass
class _ReferenceSession:
    session_id: str
    transcription: list[TranscriptionTurn] = field(default_factory=list)
    inbound: list[dict[str, Any]] = field(default_factory=list)
    recording_wavs: list[bytes] = field(default_factory=list)
    latency_marks: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False
    recording: AudioRecordingHandle | None = None


class ReferencePipecatAgentTransport:
    """Duplex local transport whose target participant is a real agent pipeline."""

    transport_id = 'pipecat_small_webrtc'

    def __init__(
        self,
        *,
        artifact_dir: Path,
        media: ReferenceMediaServices,
        completion: CompletionProvider,
        config: ReferenceRuntimeConfig,
        agent_name: str = 'CAE generalist voice agent',
        event_observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.artifact_dir = artifact_dir
        self.media = media
        self.completion = completion
        self.config = config
        self.agent_name = agent_name
        self.event_observer = event_observer
        self.runtime = media.readiness()
        if not config.internal_token:
            raise ReferenceRuntimeError(
                'Built-in voice target requires REFERENCE_AGENT_INTERNAL_TOKEN shared by the API and Pipecat service.'
            )
        llm_status = completion.status()
        if llm_status.get('status') != 'connected':
            raise ReferenceRuntimeError(llm_status.get('message') or 'Reference LLM is not connected.')
        self.runtime['llm'] = {
            'provider': llm_status.get('provider') or completion.provider_id,
            'model': config.llm_model,
            'status': 'ready',
        }
        try:
            pipecat_health = media.client.get(
                f'{config.pipecat_service_url}/reference-agent/readiness',
                headers={'x-cae-reference-token': config.internal_token},
            )
            pipecat_health.raise_for_status()
            pipecat_payload = pipecat_health.json()
            if not pipecat_payload.get('ready'):
                missing = [
                    name for name, key in (
                        ('Pipecat runtime', 'pipeline_runtime'),
                        ('RTC_ASR_BASE_URL', 'rtc_asr_configured'),
                        ('KOKORO_BASE_URL', 'kokoro_configured'),
                    ) if not pipecat_payload.get(key)
                ]
                raise ReferenceRuntimeError(
                    f'Pipecat reference agent is not ready: {", ".join(missing) or "unknown dependency"}.'
                )
        except Exception as exc:  # noqa: BLE001
            raise ReferenceRuntimeError(
                f'Pipecat reference agent is unavailable at {config.pipecat_service_url}: {exc}'
            ) from exc
        self.runtime['pipeline'] = {
            'provider': 'pipecat',
            'service_url': config.pipecat_service_url,
            'status': 'ready',
        }
        self._sessions: dict[str, _ReferenceSession] = {}

    async def connect(self, session_id: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        del metadata
        self._sessions[session_id] = _ReferenceSession(session_id=session_id)
        return {'session_id': session_id, 'ready': True, 'participant': 'reference_pipecat_agent', 'runtime': self.runtime}

    async def start_recording(self, session_id: str) -> dict[str, Any]:
        self._require(session_id)
        return {'recording': True, 'session_id': session_id}

    async def send_audio(
        self,
        session_id: str,
        *,
        fixture: AccAudioFixture,
        step: AccAudioStep,
        provenance: dict[str, Any],
    ) -> dict[str, Any]:
        del provenance
        state = self._require(session_id)
        wav_bytes = fixture.metadata.get('audio_bytes')
        if not isinstance(wav_bytes, bytes):
            raise ReferenceRuntimeError('Tester did not provide current-run WAV audio to the agent participant.')

        started = time.perf_counter()
        response = self.media.client.post(
            f'{self.config.pipecat_service_url}/reference-agent/turn',
            json={
                'audio_wav_base64': base64.b64encode(wav_bytes).decode('ascii'),
                'history': [
                    {'speaker': turn.speaker, 'text': turn.text}
                    for turn in state.transcription
                ],
                'model_name': self.config.llm_model,
            },
            timeout=self.config.timeout_seconds,
            headers={'x-cae-reference-token': self.config.internal_token},
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail')
            except Exception:  # noqa: BLE001
                detail = response.text
            raise ReferenceRuntimeError(f'Pipecat reference agent failed: {detail or response.status_code}')
        pipeline_payload = response.json()
        caller_text = str(pipeline_payload.get('caller_transcript') or '').strip()
        agent_text = str(pipeline_payload.get('agent_text') or '').strip()
        try:
            agent_wav = base64.b64decode(str(pipeline_payload.get('agent_audio_wav_base64') or ''), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ReferenceRuntimeError('Pipecat reference agent returned invalid audio.') from exc
        if not caller_text or not agent_text or not agent_wav:
            raise ReferenceRuntimeError('Pipecat reference agent returned incomplete current-run evidence.')
        pipeline_ms = round((time.perf_counter() - started) * 1000, 2)

        caller_index = len(state.transcription) + 1
        state.transcription.extend([
            TranscriptionTurn(
                turn_index=caller_index,
                speaker='Caller',
                text=caller_text,
                act_id=step.expected_caller_act,
                source='rtc-asr.current_run',
                event_types=['tester_audio_received', 'rtc_asr_transcript'],
                direction='tester_to_target',
                evidence_role='target_asr_receipt',
                frame_metadata={
                    'audio_bytes': len(wav_bytes),
                    'transport': self.transport_id,
                    'source': 'tester_kokoro_audio',
                },
            ),
            TranscriptionTurn(
                turn_index=caller_index + 1,
                speaker='Agent',
                text=agent_text,
                source='reference_pipecat_agent.current_run',
                event_types=['llm_response_completed', 'kokoro_audio_synthesized'],
                direction='target_to_tester',
                evidence_role='target_llm_output',
                frame_metadata={
                    'audio_bytes': len(agent_wav),
                    'transport': self.transport_id,
                    'source': 'target_kokoro_audio',
                },
            ),
        ])
        state.recording_wavs.extend([wav_bytes, agent_wav])
        state.inbound.append({'text': agent_text, 'audio': agent_wav, 'bytes': len(agent_wav)})
        state.latency_marks.append({'label': 'Pipecat rtc-asr → LLM → Kokoro turn', 'latency_ms': pipeline_ms})
        if self.event_observer is not None:
            self.event_observer({'speaker': 'Caller', 'text': caller_text, 'audio': wav_bytes})
            self.event_observer({'speaker': 'Agent', 'text': agent_text, 'audio': agent_wav})
        return {'accepted': True, 'session_id': session_id, 'fixture_id': fixture.fixture_id, 'current_run': True}

    async def receive_audio(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        state = self._require(session_id)
        start = int(cursor or 0)
        frames = state.inbound[start:]
        text = frames[-1]['text'] if frames else None
        return {
            'frames': [{'bytes': item['bytes']} for item in frames],
            'agent_text': text,
            'next_cursor': str(len(state.inbound)),
            'events': ([{'type': 'agent_response_completed', 'detail': {'speaker': 'agent', 'text': text}}] if text else []),
        }

    async def disconnect(self, session_id: str, *, reason: str = 'tester_complete') -> dict[str, Any]:
        state = self._require(session_id)
        if state.recording_wavs:
            state.recording = await self.stop_recording(session_id)
        state.closed = True
        return {
            'closed': True,
            'session_id': session_id,
            'reason': reason,
            'recording': state.recording.as_call_media() if state.recording else None,
        }

    async def stop_recording(self, session_id: str) -> AudioRecordingHandle:
        state = self._require(session_id)
        if state.recording is not None:
            return state.recording
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f'{session_id}.wav'
        payload = _concatenate_wavs(state.recording_wavs)
        path.write_bytes(payload)
        import hashlib
        state.recording = AudioRecordingHandle(
            uri=str(path),
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes_captured=len(payload),
            duration_ms=None,
            metadata={'source': 'current_run', 'segments': len(state.recording_wavs), 'synthetic_local_media': True},
        )
        return state.recording

    def recording_handle(self, session_id: str) -> AudioRecordingHandle | None:
        return self._require(session_id).recording

    def transcription_turns(self, session_id: str) -> list[TranscriptionTurn]:
        return list(self._require(session_id).transcription)

    def latency_marks(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._require(session_id).latency_marks)

    def session_proof(self, session_id: str) -> dict[str, Any]:
        state = self._require(session_id)
        return {
            'session_id': session_id,
            'transport': self.transport_id,
            'tester_participant': 'pipecat_tester',
            'target_participant': 'reference_pipecat_agent',
            'frames_sent': len(state.recording_wavs[::2]),
            'frames_received': len(state.inbound),
            'closed': state.closed,
            'runtime': self.runtime,
            'evidence_source': 'current_run',
        }

    def _require(self, session_id: str) -> _ReferenceSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ReferenceRuntimeError(f'Unknown reference agent session: {session_id}') from exc


def _concatenate_wavs(chunks: list[bytes]) -> bytes:
    """Concatenate same-format WAV segments into one valid current-run recording."""
    if not chunks:
        raise ReferenceRuntimeError('No current-run audio was captured.')
    output = io.BytesIO()
    params = None
    frames: list[bytes] = []
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), 'rb') as source:
            current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
            if params is None:
                params = current
            if current != params:
                raise ReferenceRuntimeError('Kokoro tester/agent WAV formats did not match for recording capture.')
            frames.append(source.readframes(source.getnframes()))
    assert params is not None
    with wave.open(output, 'wb') as target:
        target.setnchannels(params[0])
        target.setsampwidth(params[1])
        target.setframerate(params[2])
        target.writeframes(b''.join(frames))
    return output.getvalue()
