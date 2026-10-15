"""One lightweight caller microphone clock, shared by local and Daily tests."""
from __future__ import annotations

import asyncio
import time
import sys
from pathlib import Path

try:
    from waylo_audio_conditions import ContinuousNoise, pcm_wav, read_wav
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
    from app.services.waylo_audio_conditions import ContinuousNoise, pcm_wav, read_wav


class ClockedNoiseTrack:
    def __init__(self, reference_wav, condition, *, send, observe, max_seconds=310):
        self.mixer = ContinuousNoise(reference_wav, **condition)
        self.reference_wav = reference_wav
        self.max_seconds = max_seconds
        self.speech_calibrated = False
        self.send, self.observe = send, observe
        self.pending = None
        self.task = None
        self.error = None
        self.closed = False

    def start(self):
        self.task = asyncio.create_task(self._run())

    async def play(self, pcm, turn_pair=0):
        if self.error:
            raise RuntimeError('Caller background noise stream failed.') from self.error
        if self.closed or self.pending or not pcm or len(pcm) % 2:
            raise ValueError('Caller microphone is unavailable or busy.')
        if not self.speech_calibrated:
            self.mixer.recalibrate(self.reference_wav)
            self.speech_calibrated = True
        done = asyncio.get_running_loop().create_future()
        self.pending = [pcm, 0, bytearray(), done, turn_pair]
        return await done

    async def _run(self):
        size = self.mixer.rate // 50 * 2
        deadline = time.monotonic()
        try:
            while not self.closed:
                await asyncio.sleep(max(0, deadline - time.monotonic()))
                if self.mixer.samples >= self.mixer.rate * self.max_seconds:
                    raise RuntimeError('Caller microphone duration limit reached.')
                pending = self.pending
                clean = pending[0][pending[1]:pending[1] + size] if pending else b''
                mixed = self.mixer.mix(clean.ljust(size, b'\0'))
                await self.send(mixed, self.mixer.rate)
                await self.observe(mixed, self.mixer.rate, pending[4] if pending else 0)
                if pending:
                    pending[1] += len(clean)
                    pending[2].extend(mixed)  # retain the complete frame accepted by the target
                    if pending[1] == len(pending[0]):
                        # The last frame must play before the speech-end boundary.
                        await asyncio.sleep(.02)
                        self.pending = None
                        if not pending[3].done():
                            pending[3].set_result(pcm_wav(bytes(pending[2]), self.mixer.rate))
                deadline += .02
                if deadline < time.monotonic() - .04:
                    deadline = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = exc
            if self.pending and not self.pending[3].done():
                self.pending[3].set_exception(RuntimeError('Caller background noise stream failed.'))

    async def close(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.pending and not self.pending[3].done():
            self.pending[3].cancel()

