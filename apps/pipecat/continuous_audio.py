"""Clocked duplex mix shared by live monitoring and recorded playback.

Capture is independent of ASR/turn windows. Both directions retain their place
on the local media clock, including silence, greetings and overlapping speech.
This records local transport audio, not a browser's network jitter or volume.
"""

from __future__ import annotations

import io
import time
import wave
from collections import deque
from collections.abc import Callable

import numpy as np


class ContinuousDuplexAudio:
    def __init__(self, *, clock: Callable[[], float] = time.perf_counter,
                 sample_rate: int = 24_000, max_seconds: float = 960):
        self.clock = clock
        self.sample_rate = sample_rate
        self.max_samples = int(max_seconds * sample_rate)
        self.origin: float | None = None
        self.rendered = 0
        self.queues: dict[str, deque[tuple[int, np.ndarray]]] = {}
        self.cursors: dict[str, int] = {}
        self.resample_grids: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        self.pcm = bytearray()

    def add(self, direction: str, audio: bytes, *, sample_rate: int, channels: int) -> None:
        if not audio:
            return
        if direction not in {'tester_to_target', 'target_to_tester'}:
            raise ValueError('Unsupported duplex direction.')
        if sample_rate <= 0 or channels <= 0 or len(audio) % (channels * 2):
            raise ValueError('Invalid PCM16 frame format.')
        now = self.clock()
        if self.origin is None:
            self.origin = now
        # Most transport frames are already mono PCM16. Keep their immutable
        # input buffer rather than allocating float64 and int32 copies per frame.
        samples = np.frombuffer(audio, dtype='<i2')
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        if sample_rate != self.sample_rate:
            key = (sample_rate, len(samples))
            grids = self.resample_grids.get(key)
            if grids is None:
                count = max(1, round(len(samples) * self.sample_rate / sample_rate))
                grids = (np.arange(count) * sample_rate / self.sample_rate, np.arange(len(samples)))
                # Cache common short packet shapes, not whole utterance grids.
                if len(samples) <= sample_rate // 10:
                    if len(self.resample_grids) >= 8:
                        self.resample_grids.pop(next(iter(self.resample_grids)))
                    self.resample_grids[key] = grids
            samples = np.interp(*grids, samples).astype('<i2')
        elif channels > 1:
            samples = samples.astype('<i2')
        observed = round((now - self.origin) * self.sample_rate)
        previous = self.cursors.get(direction)
        # Keep contiguous packets contiguous despite modest scheduling jitter;
        # real gaps remain gaps. Different speakers never share a FIFO cursor.
        start = observed if previous is None or observed - previous > self.sample_rate * .08 else previous
        start = max(self.rendered, start)
        end = start + len(samples)
        if end > self.max_samples:
            raise ValueError('Continuous audio exceeds the recording duration limit.')
        self.queues.setdefault(direction, deque()).append((start, samples))
        self.cursors[direction] = end

    def ready_sample(self, *, delay_seconds: float = .06) -> int:
        if self.origin is None:
            return 0
        return min(self.max_samples, max(0, int(
            (self.clock() - self.origin - delay_seconds) * self.sample_rate
        )))

    def render(self, end: int) -> bytes:
        """Commit PCM once; callers publish these exact bytes to live tracks."""
        end = min(self.max_samples, max(self.rendered, end))
        mix = np.zeros(end - self.rendered, dtype=np.int32)
        for queue in self.queues.values():
            while queue and queue[0][0] + len(queue[0][1]) <= self.rendered:
                queue.popleft()
            for offset, samples in queue:
                if offset >= end:
                    break
                lo, hi = max(offset, self.rendered), min(offset + len(samples), end)
                if hi > lo:
                    mix[lo - self.rendered:hi - self.rendered] += samples[lo - offset:hi - offset]
        np.clip(mix, -32768, 32767, out=mix)
        payload = mix.astype('<i2').tobytes()
        self.pcm.extend(payload)
        self.rendered = end
        return payload

    def wav(self) -> bytes:
        output = io.BytesIO()
        with wave.open(output, 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(self.sample_rate)
            target.writeframes(self.pcm)
        return output.getvalue()

    def finish_wav(self) -> bytes:
        """Snapshot a closed call and release its buffers before serialization."""
        payload = self.wav()
        self.pcm.clear()
        self.queues.clear()
        self.cursors.clear()
        self.resample_grids.clear()
        return payload

    def metadata(self, *, partial: bool) -> dict:
        return {
            'scope': 'continuous_duplex', 'recording_clock': 'local_monotonic_media',
            'live_mix_shared': True, 'partial': partial,
            'sample_rate': self.sample_rate, 'channels': 1,
            'duration_ms': round(self.rendered / self.sample_rate * 1000, 2),
            'capture_boundary': 'first_transport_audio_to_transport_close',
        }
