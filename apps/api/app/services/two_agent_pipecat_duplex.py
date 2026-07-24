"""Offline seam for the #104 two-Pipecat duplex voice evaluation.

This module is deliberately provider-neutral: it proves the architecture shape
that CI can exercise while live OpenAI, rtc-asr, Kokoro, and browser listener
adapters remain explicit runtime dependencies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


ParticipantId = Literal['pipecat_tester', 'pipecat_target']
FrameDirection = Literal['tester_to_target', 'target_to_tester']
ProcessorName = Literal['rtc-asr', 'llm', 'kokoro']


class TextGenerator(Protocol):
    provider_id: str
    model_name: str
    llm_mode: Literal['real', 'mock']

    def complete(self, prompt: str, *, history: list[dict[str, Any]]) -> str: ...


class AudioSynthesizer(Protocol):
    provider_id: str
    model_name: str

    def synthesize(self, text: str) -> bytes: ...


class AudioTranscriber(Protocol):
    provider_id: str
    model_name: str

    def transcribe(self, audio: bytes, *, source_text: str) -> str: ...


@dataclass(frozen=True, slots=True)
class PipecatProcessorSpec:
    name: ProcessorName
    provider: str
    model: str

    def as_dict(self) -> dict[str, str]:
        return {'name': self.name, 'provider': self.provider, 'model': self.model}


@dataclass(frozen=True, slots=True)
class PipecatAgentGraph:
    participant_id: ParticipantId
    label: str
    processors: tuple[PipecatProcessorSpec, PipecatProcessorSpec, PipecatProcessorSpec]
    llm_mode: Literal['real', 'mock']

    def __post_init__(self) -> None:
        chain = tuple(processor.name for processor in self.processors)
        if chain != ('rtc-asr', 'llm', 'kokoro'):
            raise ValueError(f'Pipecat graph must be rtc-asr -> llm -> kokoro, got {chain!r}')

    def as_dict(self) -> dict[str, Any]:
        return {
            'participant_id': self.participant_id,
            'label': self.label,
            'processors': [processor.as_dict() for processor in self.processors],
            'llm_mode': self.llm_mode,
        }


@dataclass(frozen=True, slots=True)
class DuplexAudioFrame:
    sequence: int
    direction: FrameDirection
    sender: ParticipantId
    receiver: ParticipantId
    audio_bytes: bytes
    source_text: str
    sent_at: float = field(default_factory=time.time)

    def metadata(self) -> dict[str, Any]:
        return {
            'sequence': self.sequence,
            'direction': self.direction,
            'sender': self.sender,
            'receiver': self.receiver,
            'bytes': len(self.audio_bytes),
            'sent_at': self.sent_at,
        }


@dataclass(frozen=True, slots=True)
class DirectionalTurnEvidence:
    turn_index: int
    direction: FrameDirection
    speaker: str
    llm_output: str
    asr_receipt: str
    frame: dict[str, Any]

    def as_dialog_items(self) -> list[dict[str, Any]]:
        sender, receiver = self.direction.split('_to_')
        return [
            {
                'speaker': sender,
                'text': self.llm_output,
                'source': f'{self.speaker}.llm_output',
                'turn_index': self.turn_index,
                'direction': self.direction,
            },
            {
                'speaker': receiver,
                'text': self.asr_receipt,
                'source': f'{receiver}.asr_receipt',
                'turn_index': self.turn_index,
                'direction': self.direction,
            },
        ]


class InMemoryDuplexFrameTransport:
    """Local duplex frame bus used by offline tests and future service adapters."""

    def __init__(self, *, run_id: str | None = None) -> None:
        self.run_id = run_id or f'two-pipecat-{uuid.uuid4().hex}'
        self.frames: list[DuplexAudioFrame] = []

    def send(
        self,
        *,
        direction: FrameDirection,
        sender: ParticipantId,
        receiver: ParticipantId,
        audio_bytes: bytes,
        source_text: str,
    ) -> DuplexAudioFrame:
        expected = _participants_for_direction(direction)
        if (sender, receiver) != expected:
            raise ValueError(f'{direction} requires {expected[0]} -> {expected[1]}, got {sender} -> {receiver}')
        if not audio_bytes:
            raise ValueError('duplex audio frames must carry bytes')
        frame = DuplexAudioFrame(
            sequence=len(self.frames) + 1,
            direction=direction,
            sender=sender,
            receiver=receiver,
            audio_bytes=audio_bytes,
            source_text=source_text,
        )
        self.frames.append(frame)
        return frame

    def frames_for(self, participant_id: ParticipantId) -> list[DuplexAudioFrame]:
        return [frame for frame in self.frames if frame.receiver == participant_id]


@dataclass(slots=True)
class TwoPipecatDuplexRunResult:
    run_id: str
    graphs: dict[str, Any]
    turns: list[DirectionalTurnEvidence]
    frames: list[dict[str, Any]]
    status: Literal['completed', 'needs_review']
    termination_reason: str

    def transcript(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for turn in self.turns:
            items.extend(turn.as_dialog_items())
        return items

    def provenance(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'architecture': 'two_independent_pipecat_graphs_duplex_frames',
            'graphs': self.graphs,
            'duplex': {
                'transport': 'in_memory_duplex_frame_transport',
                'frame_count': len(self.frames),
                'directions': sorted({frame['direction'] for frame in self.frames}),
            },
        }


class TwoPipecatDuplexHarness:
    """Exercise two independent Pipecat graph contracts over duplex audio frames."""

    def __init__(
        self,
        *,
        tester_graph: PipecatAgentGraph,
        target_graph: PipecatAgentGraph,
        tester_llm: TextGenerator | None = None,
        target_llm: TextGenerator | None = None,
        tester_tts: AudioSynthesizer | None = None,
        target_tts: AudioSynthesizer | None = None,
        tester_asr: AudioTranscriber | None = None,
        target_asr: AudioTranscriber | None = None,
        transport: InMemoryDuplexFrameTransport | None = None,
        max_turn_pairs: int = 2,
    ) -> None:
        if tester_graph.participant_id != 'pipecat_tester':
            raise ValueError('tester_graph must describe pipecat_tester')
        if target_graph.participant_id != 'pipecat_target':
            raise ValueError('target_graph must describe pipecat_target')
        self.tester_graph = tester_graph
        self.target_graph = target_graph
        self.tester_llm = tester_llm
        self.target_llm = target_llm
        self.tester_tts = tester_tts
        self.target_tts = target_tts
        self.tester_asr = tester_asr
        self.target_asr = target_asr
        self.transport = transport or InMemoryDuplexFrameTransport()
        self.max_turn_pairs = max(1, max_turn_pairs)

    def record_exchange(
        self,
        *,
        tester_text: str,
        tester_audio: bytes,
        target_receipt: str,
        target_text: str,
        target_audio: bytes,
        tester_receipt: str,
    ) -> tuple[DirectionalTurnEvidence, DirectionalTurnEvidence]:
        """Record one live exchange after both graph pipelines have run."""
        tester_frame = self.transport.send(
            direction='tester_to_target',
            sender='pipecat_tester',
            receiver='pipecat_target',
            audio_bytes=tester_audio,
            source_text=tester_text,
        )
        target_frame = self.transport.send(
            direction='target_to_tester',
            sender='pipecat_target',
            receiver='pipecat_tester',
            audio_bytes=target_audio,
            source_text=target_text,
        )
        turn_index = len(self.transport.frames) - 1
        return (
            DirectionalTurnEvidence(
                turn_index=turn_index,
                direction='tester_to_target',
                speaker='tester',
                llm_output=tester_text,
                asr_receipt=target_receipt,
                frame=tester_frame.metadata(),
            ),
            DirectionalTurnEvidence(
                turn_index=turn_index + 1,
                direction='target_to_tester',
                speaker='target',
                llm_output=target_text,
                asr_receipt=tester_receipt,
                frame=target_frame.metadata(),
            ),
        )

    def run(self, *, scenario_instruction: str) -> TwoPipecatDuplexRunResult:
        dependencies = (
            self.tester_llm,
            self.target_llm,
            self.tester_tts,
            self.target_tts,
            self.tester_asr,
            self.target_asr,
        )
        if any(dependency is None for dependency in dependencies):
            raise RuntimeError('Offline duplex run requires LLM, TTS, and ASR adapters for both graphs.')
        assert self.tester_llm is not None
        assert self.target_llm is not None
        assert self.tester_tts is not None
        assert self.target_tts is not None
        assert self.tester_asr is not None
        assert self.target_asr is not None
        history: list[dict[str, Any]] = [{'role': 'system', 'content': scenario_instruction}]
        turns: list[DirectionalTurnEvidence] = []

        for _ in range(1, self.max_turn_pairs + 1):
            tester_text = self.tester_llm.complete(scenario_instruction, history=history)
            tester_audio = self.tester_tts.synthesize(tester_text)
            target_receipt = self.target_asr.transcribe(tester_audio, source_text=tester_text)
            history.append({'role': 'tester', 'content': tester_text, 'asr_receipt': target_receipt})

            target_text = self.target_llm.complete(target_receipt, history=history)
            target_audio = self.target_tts.synthesize(target_text)
            tester_receipt = self.tester_asr.transcribe(target_audio, source_text=target_text)
            turns.extend(self.record_exchange(
                tester_text=tester_text,
                tester_audio=tester_audio,
                target_receipt=target_receipt,
                target_text=target_text,
                target_audio=target_audio,
                tester_receipt=tester_receipt,
            ))
            history.append({'role': 'target', 'content': target_text, 'asr_receipt': tester_receipt})
            if _terminal_text(tester_text) or _terminal_text(target_text):
                break

        return TwoPipecatDuplexRunResult(
            run_id=self.transport.run_id,
            graphs={
                'tester': self.tester_graph.as_dict(),
                'target': self.target_graph.as_dict(),
            },
            turns=turns,
            frames=[frame.metadata() for frame in self.transport.frames],
            status='completed',
            termination_reason='terminal_text' if any(_terminal_text(turn.llm_output) for turn in turns) else 'max_turn_pairs',
        )


def build_builtin_sample_voice_graphs(
    *,
    tester_llm_provider: str,
    tester_llm_model: str,
    target_llm_provider: str,
    target_llm_model: str,
    stt_model: str,
    tts_model: str,
    llm_mode: Literal['real', 'mock'],
) -> tuple[PipecatAgentGraph, PipecatAgentGraph]:
    tester = PipecatAgentGraph(
        participant_id='pipecat_tester',
        label='Pipecat scenario tester',
        processors=(
            PipecatProcessorSpec('rtc-asr', 'rtc-asr', stt_model),
            PipecatProcessorSpec('llm', tester_llm_provider, tester_llm_model),
            PipecatProcessorSpec('kokoro', 'kokoro', tts_model),
        ),
        llm_mode=llm_mode,
    )
    target = PipecatAgentGraph(
        participant_id='pipecat_target',
        label='Pipecat evaluated built-in voice agent',
        processors=(
            PipecatProcessorSpec('rtc-asr', 'rtc-asr', stt_model),
            PipecatProcessorSpec('llm', target_llm_provider, target_llm_model),
            PipecatProcessorSpec('kokoro', 'kokoro', tts_model),
        ),
        llm_mode=llm_mode,
    )
    return tester, target


def _participants_for_direction(direction: FrameDirection) -> tuple[ParticipantId, ParticipantId]:
    if direction == 'tester_to_target':
        return 'pipecat_tester', 'pipecat_target'
    return 'pipecat_target', 'pipecat_tester'


def _terminal_text(text: str) -> bool:
    lowered = text.lower()
    return '[done]' in lowered or '[end]' in lowered
