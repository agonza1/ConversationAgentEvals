"""Reference generalist target used by CAE's built-in end-to-end runs.

The tester and target are deliberately separate participants.  The tester supplies
real synthesized audio; this target transcribes it with rtc-asr, calls a configured
LLM, synthesizes the reply with Kokoro, and returns only artifacts observed in this
execution.  No saved benchmark fixture is consulted here.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from app.services.acc_realtime_target import AccAudioFixture, AccAudioStep
from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
from app.services.llm_providers import get_provider
from app.services.two_agent_pipecat_duplex import (
    InMemoryDuplexFrameTransport,
    TwoPipecatDuplexHarness,
    build_builtin_sample_voice_graphs,
)


class ReferenceRuntimeError(RuntimeError):
    """Actionable fail-closed error from a required reference runtime."""


class CompletionProvider(Protocol):
    provider_id: str

    def status(self) -> dict[str, Any]: ...
    def complete(self, prompt: str, *, model_name: str | None = None) -> str: ...


def _default_target_voice() -> str:
    explicit = os.getenv('KOKORO_TARGET_VOICE', '').strip()
    if explicit:
        return explicit
    tester = os.getenv('KOKORO_TESTER_VOICE', 'af_heart').strip()
    legacy = os.getenv('KOKORO_VOICE', '').strip()
    return legacy if legacy and legacy != tester else 'am_adam'


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
    kokoro_base_url: str = field(
        default_factory=lambda: os.getenv('KOKORO_BASE_URL', '').rstrip('/')
    )
    kokoro_model: str = field(
        default_factory=lambda: os.getenv('KOKORO_MODEL', 'kokoro').strip()
    )
    kokoro_tester_voice: str = field(
        default_factory=lambda: os.getenv('KOKORO_TESTER_VOICE', 'af_heart').strip()
    )
    kokoro_target_voice: str = field(
        default_factory=_default_target_voice
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv('REFERENCE_LLM_MODEL', 'gpt-5.4-mini').strip()
    )
    tester_llm_model: str = field(
        default_factory=lambda: (
            os.getenv('REFERENCE_TESTER_LLM_MODEL')
            or os.getenv('REFERENCE_LLM_MODEL')
            or 'gpt-5.4-mini'
        ).strip()
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv('REFERENCE_AGENT_TIMEOUT_SECONDS', '60'))
    )


def discover_rtc_asr_runtime(payload: Any) -> dict[str, str]:
    """Use rtc-asr health as the source of truth for its loaded backend and model."""
    if not isinstance(payload, dict):
        raise ReferenceRuntimeError('rtc-asr health returned an invalid payload.')

    status = str(payload.get('status') or '').strip().lower()
    explicitly_unready = payload.get('ready') is False or payload.get('model_loaded') is False
    if explicitly_unready or status in {'error', 'failed', 'loading', 'starting', 'unavailable'}:
        reason = str(payload.get('preload_error') or payload.get('detail') or status or 'not ready')
        raise ReferenceRuntimeError(f'rtc-asr is reachable but not ready: {reason}.')

    return {
        'provider': 'rtc-asr',
        'backend': str(payload.get('backend') or 'service-selected').strip().lower(),
        'model': str(payload.get('model') or 'service-selected').strip(),
        'status': 'ready',
        'selection': 'service-discovery',
    }


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
        if self.config.kokoro_tester_voice == self.config.kokoro_target_voice:
            raise ReferenceRuntimeError(
                'Built-in voice evaluation requires distinct tester and target voices. '
                'Set KOKORO_TESTER_VOICE and KOKORO_TARGET_VOICE to different Kokoro voice IDs.'
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

        stt_runtime = discover_rtc_asr_runtime(asr_payload)
        return {
            'stt': stt_runtime,
            'tts': {
                'provider': 'kokoro',
                'model': self.config.kokoro_model,
                'tester_voice': self.config.kokoro_tester_voice,
                'target_voice': self.config.kokoro_target_voice,
                'voices_distinct': True,
                'status': str(kokoro_payload.get('status') or 'ready'),
            },
        }

    def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        response = self.client.post(
            f'{self.config.kokoro_base_url}/v1/audio/speech',
            json={
                'model': self.config.kokoro_model,
                'voice': voice or self.config.kokoro_tester_voice,
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


class ReferenceTesterLlmWordingRenderer:
    """Render tester caller turns with the configured real LLM provider."""

    def __init__(self, completion: CompletionProvider, *, model_name: str) -> None:
        self.completion = completion
        self.model_name = model_name

    async def render(self, act, observation, config) -> str:  # noqa: ANN001 - protocol accepts app models
        observed = (
            f'Target observation: {observation.agent_text}'
            if observation is not None and observation.agent_text
            else 'Target observation: none yet.'
        )
        prompt = (
            'You are the Pipecat scenario tester in a two-agent voice evaluation. '
            'Render exactly one concise caller utterance for the next spoken turn. '
            'Do not narrate, score, or include labels.\n\n'
            f'Scenario: {config.scenario_id}\n'
            f'Goal: {config.goal}\n'
            f'Allowed caller act: {act.act_id}\n'
            f'Act objective: {act.objective}\n'
            f'Example utterance: {act.example_utterance}\n'
            f'{observed}\n\n'
            'Caller utterance:'
        )
        text = await asyncio.to_thread(
            self.completion.complete,
            prompt,
            model_name=self.model_name,
        )
        return text.strip()


class ReferencePipecatTesterGraphRenderer:
    """Render tester wording and audio in the remote real Pipecat graph."""

    def __init__(self, transport: ReferencePipecatAgentTransport) -> None:
        self.transport = transport
        self._rendered: dict[str, bytes] = {}

    async def render(self, act, observation, config) -> str:  # noqa: ANN001 - app protocol models
        previous_audio = self.transport.latest_target_audio()
        payload = {
            'scenario_instruction': f'{config.scenario_id}: {config.goal}',
            'act_id': act.act_id,
            'act_objective': act.objective,
            'example_utterance': act.example_utterance,
            'target_audio_wav_base64': (
                base64.b64encode(previous_audio).decode('ascii') if previous_audio else None
            ),
            'model_name': self.transport.config.tester_llm_model,
        }
        response = await asyncio.to_thread(
            self.transport.media.client.post,
            f'{self.transport.config.pipecat_service_url}/reference-tester/turn',
            json=payload,
            timeout=self.transport.config.timeout_seconds,
            headers={'x-cae-reference-token': self.transport.config.internal_token},
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail')
            except Exception:  # noqa: BLE001
                detail = response.text
            raise ReferenceRuntimeError(f'Pipecat tester graph failed: {detail or response.status_code}')
        result = response.json()
        text = str(result.get('tester_text') or '').strip()
        try:
            audio = base64.b64decode(str(result.get('tester_audio_wav_base64') or ''), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ReferenceRuntimeError('Pipecat tester graph returned invalid audio.') from exc
        processors = (result.get('pipeline') or {}).get('processors')
        if not text or not audio or processors != ['rtc-asr', 'llm', 'kokoro']:
            raise ReferenceRuntimeError('Pipecat tester graph returned incomplete pipeline evidence.')
        self._rendered[text] = audio
        return text

    async def synthesize(self, text: str, *, seed: int, metadata: dict[str, Any]) -> AccAudioFixture:
        del seed
        try:
            audio = self._rendered.pop(text)
        except KeyError as exc:
            raise ReferenceRuntimeError('Pipecat tester audio was not rendered by its graph.') from exc
        return AccAudioFixture(
            fixture_id=f'reference-pipecat-tester-{metadata.get("turn_index") or 1}',
            uri='memory://reference-pipecat-tester/current-run.wav',
            expected_caller_act=str(metadata.get('act_id') or 'caller_act'),
            mime_type='audio/wav',
            metadata={**metadata, 'rendered_text': text, 'audio_bytes': audio, 'source': 'pipecat_tester_graph'},
        )


@dataclass
class _ReferenceSession:
    session_id: str
    execution_run_id: str | None = None
    transcription: list[TranscriptionTurn] = field(default_factory=list)
    inbound: list[dict[str, Any]] = field(default_factory=list)
    recording_wavs: list[bytes] = field(default_factory=list)
    latency_marks: list[dict[str, Any]] = field(default_factory=list)
    duplex_transport: InMemoryDuplexFrameTransport | None = None
    duplex_harness: TwoPipecatDuplexHarness | None = None
    duplex_frames: list[dict[str, Any]] = field(default_factory=list)
    remote_graphs: dict[str, Any] = field(default_factory=dict)
    remote_architecture: str | None = None
    termination_reason: str | None = None
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
        stt_runtime = self.runtime.get('stt') if isinstance(self.runtime.get('stt'), dict) else {}
        tester_graph, target_graph = build_builtin_sample_voice_graphs(
            tester_llm_provider=str(llm_status.get('provider') or completion.provider_id),
            tester_llm_model=config.tester_llm_model,
            target_llm_provider=str(llm_status.get('provider') or completion.provider_id),
            target_llm_model=config.llm_model,
            stt_model=str(stt_runtime.get('model') or 'service-selected'),
            tts_model=config.kokoro_model,
            tester_tts_voice=config.kokoro_tester_voice,
            target_tts_voice=config.kokoro_target_voice,
            llm_mode='real',
        )
        self._tester_graph = tester_graph
        self._target_graph = target_graph
        self.graphs = {
            'tester': tester_graph.as_dict(),
            'target': target_graph.as_dict(),
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
                        ('two-agent duplex route', 'duplex_route_ready'),
                    ) if not pipecat_payload.get(key)
                ]
                raise ReferenceRuntimeError(
                    f'Pipecat reference agent is not ready: {", ".join(missing) or "unknown dependency"}.'
                )
            if pipecat_payload.get('route') != '/reference-duplex/run':
                raise ReferenceRuntimeError(
                    'Pipecat service does not expose the required /reference-duplex/run primary path.'
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
        duplex_transport = InMemoryDuplexFrameTransport(run_id=session_id)
        self._sessions[session_id] = _ReferenceSession(
            session_id=session_id,
            execution_run_id=str((metadata or {}).get('execution_run_id') or session_id),
            duplex_transport=duplex_transport,
            duplex_harness=TwoPipecatDuplexHarness(
                tester_graph=self._tester_graph,
                target_graph=self._target_graph,
                transport=duplex_transport,
            ),
        )
        return {
            'session_id': session_id,
            'ready': True,
            'participant': 'reference_pipecat_agent',
            'runtime': self.runtime,
            'graphs': self.graphs,
        }

    async def start_recording(self, session_id: str) -> dict[str, Any]:
        self._require(session_id)
        return {'recording': True, 'session_id': session_id}

    async def run_duplex_session(
        self,
        session_id: str,
        *,
        scenario: dict[str, Any],
        max_turn_pairs: int,
        total_timeout_seconds: float,
    ) -> dict[str, Any]:
        """Run the primary two-agent session while audio remains inside Pipecat."""
        state = self._require(session_id)
        llm_runtime = self.runtime.get('llm') if isinstance(self.runtime.get('llm'), dict) else {}
        request_payload = {
            'session_id': session_id,
            'execution_run_id': state.execution_run_id or session_id,
            'scenario': scenario,
            'tester_model_name': self.config.tester_llm_model,
            'target_model_name': self.config.llm_model,
            'llm_provider': llm_runtime.get('provider') or self.completion.provider_id,
            'llm_mode': 'real',
            'stt_backend': str(self.runtime['stt']['backend']),
            'stt_model': str(self.runtime['stt']['model']),
            'max_turn_pairs': max_turn_pairs,
            'total_timeout_seconds': total_timeout_seconds,
            'tester_voice': self.config.kokoro_tester_voice,
            'target_voice': self.config.kokoro_target_voice,
        }
        exchanges: list[dict[str, Any]] = []
        completed: dict[str, Any] | None = None
        timeout = httpx.Timeout(
            connect=min(10.0, total_timeout_seconds),
            read=total_timeout_seconds + 10.0,
            write=30.0,
            pool=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                'POST',
                f'{self.config.pipecat_service_url}/reference-duplex/run',
                json=request_payload,
                headers={'x-cae-reference-token': self.config.internal_token},
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode('utf-8', errors='replace')
                    raise ReferenceRuntimeError(
                        f'Pipecat duplex session failed: {detail or response.status_code}'
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ReferenceRuntimeError('Pipecat duplex stream returned invalid NDJSON.') from exc
                    event_type = str(event.get('type') or '')
                    if event_type == 'error':
                        raise ReferenceRuntimeError(
                            str(event.get('detail') or event.get('code') or 'Pipecat duplex session failed.')
                        )
                    if event_type == 'complete':
                        completed = event
                        continue
                    if event_type == 'live_audio':
                        self._record_live_audio_event(event)
                        continue
                    if event_type in {'transcript', 'vad', 'metric'}:
                        # Streaming diagnostics are retained in the completion proof and
                        # directional frame metadata. Audio events remain the concise live UI.
                        continue
                    if event_type != 'exchange':
                        continue
                    exchanges.append(event)
                    self._record_duplex_exchange(state, event)

        if completed is None:
            raise ReferenceRuntimeError('Pipecat duplex stream ended without completion evidence.')
        state.remote_graphs = completed.get('graphs') if isinstance(completed.get('graphs'), dict) else {}
        if state.remote_graphs:
            self.graphs = state.remote_graphs
        state.duplex_frames = completed.get('frames') if isinstance(completed.get('frames'), list) else []
        state.remote_architecture = (
            str(completed.get('architecture'))
            if completed.get('architecture')
            else None
        )
        state.termination_reason = str(completed.get('termination_reason') or 'completed')
        return {
            'scenario_id': scenario.get('id'),
            'session_id': session_id,
            'status': str(completed.get('status') or 'completed'),
            'termination_reason': state.termination_reason,
            'error': None,
            'tester_provenance': {
                'controller': 'pipecat_scenario_agent',
                'fixture_scheduler': False,
                'scripted_transcript': False,
                'scenario_id': scenario.get('id'),
                'max_turn_pairs': max_turn_pairs,
            },
            'turns': exchanges,
            'proof': completed,
        }

    def _record_live_audio_event(self, event: dict[str, Any]) -> None:
        if self.event_observer is None:
            return
        speaker = str(event.get('speaker') or '').strip()
        direction = str(event.get('direction') or '').strip()
        text = str(event.get('text') or event.get('llm_output') or '').strip()
        expected_direction = {
            'Caller': 'tester_to_target',
            'Agent': 'target_to_tester',
        }.get(speaker)
        if not text or expected_direction != direction:
            raise ReferenceRuntimeError('Pipecat live audio event omitted valid speaker/direction evidence.')
        try:
            audio = base64.b64decode(str(event.get('audio_wav_base64') or ''), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ReferenceRuntimeError('Pipecat live audio event returned invalid WAV evidence.') from exc
        if not audio:
            raise ReferenceRuntimeError('Pipecat live audio event returned empty WAV evidence.')
        frame = event.get('frame') if isinstance(event.get('frame'), dict) else {}
        turn_pair = event.get('turn_pair')
        self.event_observer({
            'speaker': speaker,
            'text': text,
            'audio': audio,
            'direction': direction,
            'llm_output': str(event.get('llm_output') or text),
            'asr_receipt': (
                str(event.get('asr_receipt')).strip()
                if event.get('asr_receipt')
                else None
            ),
            'frame_metadata': frame,
            'live_audio_key': f'{turn_pair}:{direction}',
        })

    def _record_duplex_exchange(self, state: _ReferenceSession, event: dict[str, Any]) -> None:
        tester = event.get('tester') if isinstance(event.get('tester'), dict) else {}
        target = event.get('target') if isinstance(event.get('target'), dict) else {}
        tester_text = str(tester.get('llm_output') or '').strip()
        target_receipt = str(target.get('asr_receipt') or '').strip()
        target_text = str(target.get('llm_output') or '').strip()
        tester_receipt = str(target.get('tester_asr_receipt') or '').strip()
        if not all((tester_text, target_receipt, target_text, tester_receipt)):
            raise ReferenceRuntimeError('Pipecat duplex exchange omitted directional LLM/ASR evidence.')
        try:
            tester_wav = base64.b64decode(str(tester.get('audio_wav_base64') or ''), validate=True)
            target_wav = base64.b64decode(str(target.get('audio_wav_base64') or ''), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ReferenceRuntimeError('Pipecat duplex exchange returned invalid evidence audio.') from exc
        tester_frame = tester.get('frame') if isinstance(tester.get('frame'), dict) else {}
        target_frame = target.get('frame') if isinstance(target.get('frame'), dict) else {}
        if tester_frame.get('direction') != 'tester_to_target' or target_frame.get('direction') != 'target_to_tester':
            raise ReferenceRuntimeError('Pipecat duplex exchange returned invalid frame directions.')

        caller_index = len(state.transcription) + 1
        state.transcription.extend([
            TranscriptionTurn(
                turn_index=caller_index,
                speaker='Caller',
                text=target_receipt,
                source='rtc-asr.current_run',
                event_types=['tester_llm_output', 'tester_audio_received', 'rtc_asr_transcript'],
                direction='tester_to_target',
                evidence_role='target_asr_receipt',
                frame_metadata={
                    **tester_frame,
                    'source': 'tester_kokoro_audio',
                    'source_text': tester_text,
                    'asr_receipt': target_receipt,
                },
            ),
            TranscriptionTurn(
                turn_index=caller_index + 1,
                speaker='Agent',
                text=tester_receipt,
                source='rtc-asr.current_run',
                event_types=['target_llm_output', 'kokoro_audio_synthesized', 'rtc_asr_transcript'],
                direction='target_to_tester',
                evidence_role='tester_asr_receipt',
                frame_metadata={
                    **target_frame,
                    'source': 'target_kokoro_audio',
                    'source_text': target_text,
                    'asr_receipt': tester_receipt,
                },
            ),
        ])
        state.recording_wavs.extend([tester_wav, target_wav])
        state.inbound.append({'text': tester_receipt, 'audio': target_wav, 'bytes': len(target_wav)})
        latency_ms = event.get('latency_ms')
        if isinstance(latency_ms, (int, float)):
            latency_kind = str(event.get('latency_kind') or 'target_first_audio_byte')
            latency_label = (
                'Target first audible PCM'
                if latency_kind == 'speech_end_to_first_audible_pcm'
                else 'Target first audio byte'
            )
            state.latency_marks.append({
                'label': f'{latency_label} · exchange {event.get("turn_pair") or len(state.latency_marks) + 1}',
                'kind': latency_kind,
                'participant': 'target',
                'turn_pair': event.get('turn_pair'),
                'latency_ms': float(latency_ms),
                'exchange_elapsed_ms': event.get('exchange_elapsed_ms'),
                'response_complete_latency_ms': target_frame.get('response_complete_latency_ms'),
                'response_metric': target_frame.get('response_metric'),
                'stage_metrics': (
                    target_frame.get('stage_metrics')
                    if isinstance(target_frame.get('stage_metrics'), dict)
                    else {}
                ),
                'pipecat_metrics': (
                    target_frame.get('pipecat_metrics')
                    if isinstance(target_frame.get('pipecat_metrics'), list)
                    else []
                ),
            })
        if self.event_observer is not None:
            turn_pair = event.get('turn_pair')
            self.event_observer({
                'update_live_audio_key': f'{turn_pair}:tester_to_target',
                'text': target_receipt,
                'llm_output': tester_text,
                'asr_receipt': target_receipt,
                'frame_metadata': tester_frame,
            })
            self.event_observer({
                'update_live_audio_key': f'{turn_pair}:target_to_tester',
                'text': tester_receipt,
                'llm_output': target_text,
                'asr_receipt': tester_receipt,
                'frame_metadata': target_frame,
            })
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
                'voice': self.config.kokoro_target_voice,
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
        first_audio_byte_latency_ms = pipeline_payload.get('first_audio_byte_latency_ms')
        if not isinstance(first_audio_byte_latency_ms, (int, float)):
            raise ReferenceRuntimeError(
                'Pipecat reference agent did not report target first audio byte latency.'
            )
        try:
            agent_wav = base64.b64decode(str(pipeline_payload.get('agent_audio_wav_base64') or ''), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ReferenceRuntimeError('Pipecat reference agent returned invalid audio.') from exc
        if not caller_text or not agent_text or not agent_wav:
            raise ReferenceRuntimeError('Pipecat reference agent returned incomplete current-run evidence.')
        tester_receipt = await asyncio.to_thread(self.media.transcribe, agent_wav)
        pipeline_ms = round((time.perf_counter() - started) * 1000, 2)
        rendered_text = str(fixture.metadata.get('rendered_text') or '').strip() or caller_text
        if state.duplex_harness is None:
            raise ReferenceRuntimeError('Pipecat duplex harness was not initialized for the session.')
        tester_turn, target_turn = state.duplex_harness.record_exchange(
            tester_text=rendered_text,
            tester_audio=wav_bytes,
            target_receipt=caller_text,
            target_text=agent_text,
            target_audio=agent_wav,
            tester_receipt=tester_receipt,
        )

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
                    **tester_turn.frame,
                    'transport': self.transport_id,
                    'source': 'tester_kokoro_audio',
                    'source_text': rendered_text,
                    'asr_receipt': caller_text,
                },
            ),
            TranscriptionTurn(
                turn_index=caller_index + 1,
                speaker='Agent',
                text=tester_receipt,
                source='rtc-asr.current_run',
                event_types=['llm_response_completed', 'kokoro_audio_synthesized', 'rtc_asr_transcript'],
                direction='target_to_tester',
                evidence_role='tester_asr_receipt',
                frame_metadata={
                    **target_turn.frame,
                    'transport': self.transport_id,
                    'source': 'target_kokoro_audio',
                    'source_text': agent_text,
                    'asr_receipt': tester_receipt,
                    'response_metric': 'target_time_to_first_audio_byte',
                    'response_latency_ms': float(first_audio_byte_latency_ms),
                    'response_complete_latency_ms': pipeline_payload.get('response_complete_latency_ms'),
                },
            ),
        ])
        state.recording_wavs.extend([wav_bytes, agent_wav])
        state.inbound.append({'text': tester_receipt, 'audio': agent_wav, 'bytes': len(agent_wav)})
        state.latency_marks.append({
            'label': f'Target first audio byte · exchange {len(state.latency_marks) + 1}',
            'kind': 'target_first_audio_byte',
            'participant': 'target',
            'latency_ms': float(first_audio_byte_latency_ms),
            'response_complete_latency_ms': pipeline_payload.get('response_complete_latency_ms'),
            'exchange_elapsed_ms': pipeline_ms,
        })
        if self.event_observer is not None:
            self.event_observer({'speaker': 'Caller', 'text': caller_text, 'audio': wav_bytes})
            self.event_observer({'speaker': 'Agent', 'text': tester_receipt, 'audio': agent_wav})
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

    def latest_target_audio(self) -> bytes | None:
        for state in reversed(list(self._sessions.values())):
            if state.inbound:
                return state.inbound[-1]['audio']
        return None

    def session_proof(self, session_id: str) -> dict[str, Any]:
        state = self._require(session_id)
        graphs = state.remote_graphs or self.graphs
        frames = state.duplex_frames or [
            frame.metadata()
            for frame in ((state.duplex_transport.frames if state.duplex_transport else []))
        ]
        return {
            'session_id': session_id,
            'transport': self.transport_id,
            'tester_participant': 'pipecat_tester',
            'target_participant': 'pipecat_target',
            'reference_endpoint': 'reference_pipecat_agent',
            'frames_sent': len(state.recording_wavs[::2]),
            'frames_received': len(state.inbound),
            'closed': state.closed,
            'runtime': self.runtime,
            'evidence_source': 'current_run',
            'architecture': (
                state.remote_architecture
                or (
                    'two_independent_pipecat_graphs_in_process_duplex_frames'
                    if state.duplex_frames
                    else 'two_independent_pipecat_graphs_duplex_frames'
                )
            ),
            'graphs': graphs,
            'duplex': {
                'transport': (
                    'in_process_pipecat_frame_bus'
                    if state.duplex_frames
                    else 'local_duplex_frame_transport'
                ),
                'frame_count': len(frames),
                'frames': frames,
            },
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
