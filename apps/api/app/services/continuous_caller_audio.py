"""Single clocked AudioSource writer for caller speech plus continuous ambience."""
from __future__ import annotations

import asyncio
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CallerSpeech:
    pcm: bytes
    done: asyncio.Future
    accepted: bytearray = field(default_factory=bytearray)
    offset: int = 0
    started_ms: float | None = None
    ended_ms: float | None = None


class ContinuousCallerAudio:
    def __init__(self, *, source, make_frame, mixer, on_audio, on_error, now_ms, path: Path):
        self.source, self.make_frame, self.mixer = source, make_frame, mixer
        self.on_audio, self.on_error, self.now_ms = on_audio, on_error, now_ms
        self.path = path
        self.pending: CallerSpeech | None = None
        self.task = None
        self.closed = False
        self.error = None
        self.accepted_samples = self.late_frames = 0
        self.started_ms = self.ended_ms = None
        self.speech_activity = False

    def start(self):
        if self.task is not None:
            raise ValueError('Caller stream already started.')
        self.task = asyncio.create_task(self._run())

    def enqueue(self, pcm: bytes) -> CallerSpeech:
        if self.closed or self.error or self.pending is not None or not pcm or len(pcm) % 2:
            raise ValueError('Caller stream is closed, unavailable or busy.')
        self.pending = CallerSpeech(pcm, asyncio.get_running_loop().create_future())
        return self.pending

    async def _run(self):
        rate = self.mixer.rate
        frame_bytes = rate // 50 * 2
        deadline = time.monotonic()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self.path), 'wb') as writer:
                writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(rate)
                while not self.closed:
                    await asyncio.sleep(max(0, deadline - time.monotonic()))
                    if self.accepted_samples + frame_bytes // 2 > rate * 310:
                        raise RuntimeError('Continuous caller capture limit reached.')
                    speech = self.pending
                    count = min(frame_bytes, len(speech.pcm) - speech.offset) if speech else 0
                    clean = speech.pcm[speech.offset:speech.offset + count] if speech else b''
                    mixed = self.mixer.mix(clean.ljust(frame_bytes, b'\x00'))
                    started = self.now_ms()
                    await self.source.capture_frame(self.make_frame(mixed))
                    # Only accepted PCM reaches local evidence and the listener.
                    writer.writeframesraw(mixed)
                    self.accepted_samples += frame_bytes // 2
                    self.started_ms = started if self.started_ms is None else self.started_ms
                    self.ended_ms = self.now_ms()
                    self.speech_activity = bool(count and any(clean))
                    self.on_audio(mixed, rate)
                    if speech:
                        speech.started_ms = started if speech.started_ms is None else speech.started_ms
                        speech.accepted.extend(mixed[:count]); speech.offset += count
                        if speech.offset == len(speech.pcm):
                            # Only this task writes the source. Drain the last speech
                            # frame, then immediately resume noise on the same track.
                            await self.source.wait_for_playout()
                            speech.ended_ms = self.now_ms()
                            self.pending = None
                            if not speech.done.done(): speech.done.set_result(None)
                    deadline += .02
                    if deadline < time.monotonic() - .04:
                        self.late_frames += 1
                        deadline = time.monotonic()  # no unbounded catch-up burst
        except asyncio.CancelledError:
            raise
        except Exception:
            self.error = 'Continuous caller audio publication failed; accepted audio retained.'
            self.on_error(self.error)
            if self.pending and not self.pending.done.done():
                self.pending.done.set_exception(RuntimeError(self.error))

    async def close(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.pending and not self.pending.done.done(): self.pending.done.cancel()

    def metadata(self):
        return {**self.mixer.metadata(), 'accepted_samples': self.accepted_samples,
                'sample_rate_hz': self.mixer.rate, 'started_at_ms': self.started_ms,
                'ended_at_ms': self.ended_ms, 'late_frames': self.late_frames,
                'partial': bool(self.error), 'delivery': 'local_audio_source_accepted', 'peer_receipt': 'unknown'}
