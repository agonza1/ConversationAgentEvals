from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


PacingMode = Literal['realtime', 'accelerated']
ObservationMode = Literal['semantic', 'acoustic']


class AccTargetEndpoints(BaseModel):
    model_config = ConfigDict(extra='forbid')

    create_session: str = '/api/voice/sessions'
    inject_audio: str = '/api/voice/sessions/{session_id}/play'
    events: str = '/api/voice/sessions/{session_id}/events'
    control: str = '/api/voice/sessions/{session_id}/control'
    close_session: str = '/api/voice/sessions/{session_id}/close'
    proof: str = '/api/voice/sessions/{session_id}/proof'
    media_input: str = '/api/voice/sessions/{session_id}/media/input'


class AccAudioFixture(BaseModel):
    model_config = ConfigDict(extra='forbid')

    fixture_id: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    expected_caller_act: str = Field(min_length=1)
    sha256: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    mime_type: str = 'audio/wav'
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccAudioStep(BaseModel):
    model_config = ConfigDict(extra='forbid')

    step_id: str = Field(min_length=1)
    fixture_id: str = Field(min_length=1)
    expected_caller_act: str = Field(min_length=1)
    pacing_mode: PacingMode = 'realtime'
    acceleration_factor: float = Field(default=1.0, ge=1.0, le=20.0)
    delay_after_previous_ms: int = Field(default=0, ge=0)
    wait_for_event: str | None = None
    wait_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    barge_in: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def accelerated_mode_requires_factor(self) -> 'AccAudioStep':
        if self.pacing_mode == 'realtime' and self.acceleration_factor != 1.0:
            raise ValueError('realtime pacing requires acceleration_factor=1.0')
        return self


class AccAudioPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')

    scenario_id: str = Field(min_length=1)
    seed: int
    fixtures: list[AccAudioFixture]
    steps: list[AccAudioStep]
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def fixtures_and_steps_must_be_consistent(self) -> 'AccAudioPlan':
        fixture_ids = [fixture.fixture_id for fixture in self.fixtures]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError('fixture ids must be unique')
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError('step ids must be unique')
        missing = [step.fixture_id for step in self.steps if step.fixture_id not in set(fixture_ids)]
        if missing:
            raise ValueError(f'unknown fixture ids in steps: {sorted(set(missing))}')
        return self


class TesterAct(BaseModel):
    model_config = ConfigDict(extra='forbid')

    act_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    example_utterance: str = Field(min_length=1)
    terminal_after: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TesterObservation(BaseModel):
    model_config = ConfigDict(extra='allow')

    turn_index: int = Field(default=0, ge=0)
    agent_text: str | None = None
    agent_audio_uri: str | None = None
    event_types: list[str] = Field(default_factory=list)
    final_state: dict[str, Any] = Field(default_factory=dict)
    terminal: bool = False


class TesterScenarioConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    scenario_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    allowed_caller_acts: list[str]
    acts: list[TesterAct]
    max_turns: int = Field(default=8, ge=1, le=100)
    total_timeout_seconds: float = Field(default=120.0, gt=0, le=3600)
    terminal_event_types: list[str] = Field(default_factory=list)
    terminal_final_states: list[str] = Field(default_factory=list)
    observation_mode: ObservationMode = 'semantic'
    seed: int
    model_version: str | None = None
    prompt_version: str | None = None

    @model_validator(mode='after')
    def acts_must_be_allowed_and_bounded(self) -> 'TesterScenarioConfig':
        allowed = set(self.allowed_caller_acts)
        disallowed = [act.act_id for act in self.acts if act.act_id not in allowed]
        if disallowed:
            raise ValueError(f'tester acts are not allowed: {disallowed}')
        if len(self.acts) > self.max_turns:
            raise ValueError('configured tester acts exceed max_turns')
        return self


class AsyncJsonTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class AccMediaInputStream(Protocol):
    async def send_start(self, metadata: dict[str, Any]) -> None:
        ...

    async def send_audio(self, frame: bytes) -> None:
        ...

    async def send_finalize(self) -> None:
        ...

    async def close(self) -> None:
        ...


class TesterWordingRenderer(Protocol):
    async def render(
        self,
        act: TesterAct,
        observation: TesterObservation | None,
        config: TesterScenarioConfig,
    ) -> str:
        ...


class TesterTtsRenderer(Protocol):
    async def synthesize(self, text: str, *, seed: int, metadata: dict[str, Any]) -> AccAudioFixture:
        ...


class HttpxJsonTransport:
    def __init__(self, *, timeout_seconds: float = 30.0):
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.request(method, url, json=json, params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f'{method} {url} returned a non-object JSON response')
        return payload


class AccRealtimeTargetAdapter:
    """Optional target adapter for a future persistent ACC media-session API.

    ConversationAgentEvals can import and test this adapter without ACC. Actual calls
    occur only when a caller explicitly configures an ACC base URL and invokes a method.
    """

    def __init__(
        self,
        *,
        base_url: str,
        endpoints: AccTargetEndpoints | None = None,
        transport: AsyncJsonTransport | None = None,
        media_stream_factory: Callable[[str], Awaitable[AccMediaInputStream]] | None = None,
    ):
        self.base_url = base_url.rstrip('/')
        self.endpoints = endpoints or AccTargetEndpoints()
        self.transport = transport or HttpxJsonTransport()
        self.media_stream_factory = media_stream_factory

    async def create_session(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.transport.request(
            'POST',
            self._url(self.endpoints.create_session),
            json={
                'fullDuplex': True,
                'source': 'conversation-agent-evals',
                'metadata': metadata or {},
            },
        )

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
        return await self.transport.request(
            'POST',
            self._url(self.endpoints.inject_audio, session_id=session_id),
            json={
                'audioArtifactId': fixture.fixture_id,
                'audioUri': fixture.uri,
                'sha256': fixture.sha256,
                'mimeType': fixture.mime_type,
                'durationMs': fixture.duration_ms,
                'pace': step.pacing_mode,
                'accelerationFactor': step.acceleration_factor,
                'bargeIn': step.barge_in,
                'expectedCallerAct': step.expected_caller_act,
                'metadata': {
                    **fixture.metadata,
                    **step.metadata,
                    'scenarioId': scenario_id,
                    'scenarioStep': step.step_id,
                    'seed': seed,
                    'provenance': provenance,
                },
            },
        )

    async def stream_audio(
        self,
        session_id: str,
        frames: AsyncIterable[bytes],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.media_stream_factory is None:
            raise RuntimeError('ACC media input stream is not configured for this adapter')
        stream_url = self._url(self.endpoints.media_input, session_id=session_id)
        stream = await self.media_stream_factory(stream_url)
        frame_count = 0
        audio_bytes = 0
        try:
            await stream.send_start(
                {
                    'sampleRate': 16000,
                    'channels': 1,
                    'format': 'pcm_s16le',
                    'frameMs': 20,
                    **(metadata or {}),
                }
            )
            async for frame in frames:
                await stream.send_audio(frame)
                frame_count += 1
                audio_bytes += len(frame)
            await stream.send_finalize()
        finally:
            await stream.close()
        return {'session_id': session_id, 'frame_count': frame_count, 'audio_bytes': audio_bytes}

    async def observe_events(self, session_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        params = {'cursor': cursor} if cursor else None
        return await self.transport.request(
            'GET',
            self._url(self.endpoints.events, session_id=session_id),
            params=params,
        )

    async def interrupt(self, session_id: str, *, reason: str = 'tester_barge_in') -> dict[str, Any]:
        return await self.transport.request(
            'POST',
            self._url(self.endpoints.control, session_id=session_id),
            json={'action': 'interrupt_agent', 'reason': reason},
        )

    async def close_session(self, session_id: str, *, reason: str = 'tester_complete') -> dict[str, Any]:
        return await self.transport.request(
            'POST',
            self._url(self.endpoints.close_session, session_id=session_id),
            json={'reason': reason},
        )

    async def collect_proof(self, session_id: str) -> dict[str, Any]:
        return await self.transport.request(
            'GET',
            self._url(self.endpoints.proof, session_id=session_id),
        )

    def _url(self, path: str, **values: str) -> str:
        resolved = path.format(**values)
        return f'{self.base_url}/{resolved.lstrip("/")}'


class AccAudioFixtureScheduler:
    """Own fixture selection/order while leaving decoding and media injection to ACC."""

    def __init__(
        self,
        adapter: AccRealtimeTargetAdapter,
        *,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        event_poll_interval_seconds: float = 0.1,
    ):
        self.adapter = adapter
        self.sleeper = sleeper
        self.event_poll_interval_seconds = event_poll_interval_seconds

    async def run(self, session_id: str, plan: AccAudioPlan) -> list[dict[str, Any]]:
        fixtures = {fixture.fixture_id: fixture for fixture in plan.fixtures}
        results: list[dict[str, Any]] = []
        for step in plan.steps:
            if step.delay_after_previous_ms:
                await self.sleeper(step.delay_after_previous_ms / 1000)
            if step.wait_for_event:
                await self._wait_for_event(session_id, step.wait_for_event, step.wait_timeout_seconds)
            response = await self.adapter.inject_audio(
                session_id,
                fixture=fixtures[step.fixture_id],
                step=step,
                scenario_id=plan.scenario_id,
                seed=plan.seed,
                provenance=plan.provenance,
            )
            results.append(
                {
                    'step_id': step.step_id,
                    'fixture_id': step.fixture_id,
                    'expected_caller_act': step.expected_caller_act,
                    'pacing_mode': step.pacing_mode,
                    'barge_in': step.barge_in,
                    'response': response,
                }
            )
        return results

    async def _wait_for_event(self, session_id: str, event_type: str, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        cursor: str | None = None
        while time.monotonic() < deadline:
            payload = await self.adapter.observe_events(session_id, cursor=cursor)
            events = payload.get('events') if isinstance(payload.get('events'), list) else []
            if any(isinstance(event, dict) and event.get('type') == event_type for event in events):
                return
            next_cursor = payload.get('next_cursor') or payload.get('nextCursor')
            cursor = str(next_cursor) if next_cursor is not None else cursor
            await self.sleeper(self.event_poll_interval_seconds)
        raise TimeoutError(f'ACC event was not observed before timeout: {event_type}')


class DeterministicTesterController:
    """Select caller acts deterministically; optional LLM/TTS only render the act."""

    def __init__(
        self,
        config: TesterScenarioConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.clock = clock
        self.started_at = clock()
        self.turn_index = 0
        self.terminated_reason: str | None = None

    def next_act(self, observation: TesterObservation | None = None) -> TesterAct | None:
        if self.terminated_reason is not None:
            return None
        if observation and self._is_terminal(observation):
            self.terminated_reason = 'target_terminal_state'
            return None
        if self.clock() - self.started_at >= self.config.total_timeout_seconds:
            self.terminated_reason = 'total_timeout'
            return None
        if self.turn_index >= self.config.max_turns or self.turn_index >= len(self.config.acts):
            self.terminated_reason = 'max_turns_or_plan_complete'
            return None

        act = self.config.acts[self.turn_index]
        self.turn_index += 1
        if act.terminal_after:
            self.terminated_reason = 'terminal_tester_act_scheduled'
        return act

    async def render_utterance(
        self,
        act: TesterAct,
        observation: TesterObservation | None = None,
        *,
        renderer: TesterWordingRenderer | None = None,
    ) -> str:
        if renderer is None:
            return act.example_utterance
        rendered = await renderer.render(act, observation, self.config)
        if not rendered.strip():
            raise RuntimeError('tester wording renderer returned empty text')
        return rendered.strip()

    def provenance(self) -> dict[str, Any]:
        return {
            'scenario_id': self.config.scenario_id,
            'goal': self.config.goal,
            'seed': self.config.seed,
            'model_version': self.config.model_version,
            'prompt_version': self.config.prompt_version,
            'observation_mode': self.config.observation_mode,
            'max_turns': self.config.max_turns,
            'total_timeout_seconds': self.config.total_timeout_seconds,
        }

    def _is_terminal(self, observation: TesterObservation) -> bool:
        if observation.terminal:
            return True
        terminal_events = set(self.config.terminal_event_types)
        if terminal_events.intersection(observation.event_types):
            return True
        final_status = observation.final_state.get('status') or observation.final_state.get('outcome')
        return isinstance(final_status, str) and final_status in set(self.config.terminal_final_states)
