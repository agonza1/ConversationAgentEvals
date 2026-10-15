"""Provider-neutral, receive-only monitoring of clocked local transport audio.

Reuse the public Pipecat mixer and authenticated listener bus. Neither provider
credentials nor participant tokens cross this boundary. A monitor failure must
never block or cancel the target call; the raw capture continues independently.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import time
from functools import lru_cache
from pathlib import Path

import httpx


@lru_cache(maxsize=1)
def _mixer_class():
    # Both runtime images include this shared source. Avoid a second mixer with
    # subtly different silence/overlap behavior in the API's LiveKit transport.
    path = Path(__file__).resolve().parents[4] / 'apps/pipecat/continuous_audio.py'
    spec = importlib.util.spec_from_file_location('cae_continuous_audio', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ContinuousDuplexAudio


class LiveAudioMonitor:
    def __init__(self, *, execution_run_id=None, conversation_id=None, runtime=None,
                 client=None, max_seconds=310, clock=time.monotonic, on_unavailable=None):
        self.run_id, self.conversation_id = execution_run_id, conversation_id
        self.runtime, self.http = runtime, client
        self.owns_client = client is None
        self.publisher_id = None
        self.failed = False
        self.recording_failed = False
        self.on_unavailable = on_unavailable
        self.failure_reported = False
        self.queue = asyncio.Queue(maxsize=10)  # At most one second of 100ms batches.
        self.pump = self.sender = None
        self.pending = bytearray()
        self.keys = set()
        self.recording = _mixer_class()(clock=clock, max_seconds=max_seconds)

    async def start(self):
        self.pump = asyncio.create_task(self._pump())
        try:
            if not (self.run_id and self.conversation_id and self.runtime and self.runtime.internal_token):
                if self.run_id and self.conversation_id:
                    self._unavailable()
                return
            if self.http is None:
                self.http = httpx.AsyncClient(timeout=2, follow_redirects=False, trust_env=False)
            response = await self.http.post(
                f'{self.runtime.pipecat_service_url}/outbound-voice/broadcast/open',
                headers={'x-cae-reference-token': self.runtime.internal_token},
                json={'execution_run_id': self.run_id, 'session_id': self.conversation_id})
            response.raise_for_status()
            self.publisher_id = response.json()['publisher_id']
            self.sender = asyncio.create_task(self._send())
        except Exception:
            self._unavailable()

    def add(self, direction, pcm, *, sample_rate, turn_pair=1, media_activity=True):
        if self.recording_failed:
            return
        try:
            self.recording.add(direction, pcm, sample_rate=sample_rate, channels=1)
            if media_activity and any(pcm):
                self.keys.add(f'{self.conversation_id}:{turn_pair}:{direction}')
        except Exception:
            self.recording_failed = True
            self.disable()

    def disable(self):
        self._unavailable()
        current = asyncio.current_task()
        for task in (self.pump, self.sender):
            if task and task is not current:
                task.cancel()

    def _unavailable(self):
        self.failed = True
        if not self.failure_reported:
            self.failure_reported = True
            if self.on_unavailable:
                try:
                    self.on_unavailable()
                except Exception:
                    pass

    def _publish(self, pcm):
        if not self.publisher_id or self.failed:
            return
        self.pending.extend(pcm)
        size = self.recording.sample_rate // 10 * 2
        while len(self.pending) >= size:
            packet = bytes(self.pending[:size])
            del self.pending[:size]
            try:
                self.queue.put_nowait((packet, sorted(self.keys)))
            except asyncio.QueueFull:
                # Do not build seconds of hidden lag, replay old packets, or
                # discard a hole while claiming monitoring remained continuous.
                self._unavailable()
                self.pending.clear()
                self.sender.cancel()
                break

    async def _pump(self):
        try:
            step = self.recording.sample_rate // 50
            while True:
                ready = self.recording.ready_sample()
                while self.recording.rendered + step <= ready:
                    self._publish(self.recording.render(self.recording.rendered + step))
                    await asyncio.sleep(0)
                await asyncio.sleep(.02)
        except asyncio.CancelledError:
            pass
        except Exception:
            self.recording_failed = True
            self.disable()

    async def _send(self):
        try:
            while True:
                pcm, keys = await self.queue.get()
                try:
                    response = await self.http.post(
                        f'{self.runtime.pipecat_service_url}/outbound-voice/broadcast/audio',
                        headers={'x-cae-reference-token': self.runtime.internal_token},
                        json={'execution_run_id': self.run_id, 'publisher_id': self.publisher_id,
                              'direction': 'target_to_tester', 'turn_pair': 1,
                              'listener_media_keys': keys, 'sample_rate': self.recording.sample_rate,
                              'channels': 1, 'pcm16_base64': base64.b64encode(pcm).decode('ascii')})
                    response.raise_for_status()
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception:
            self._unavailable()
        finally:
            await self._close_publisher()

    async def _close_publisher(self):
        if self.publisher_id:
            publisher, self.publisher_id = self.publisher_id, None
            try:
                await self.http.post(
                    f'{self.runtime.pipecat_service_url}/outbound-voice/broadcast/close',
                    headers={'x-cae-reference-token': self.runtime.internal_token},
                    json={'execution_run_id': self.run_id, 'publisher_id': publisher})
            except Exception:
                pass

    async def finish(self, *, partial=False):
        try:
            return await self._finish(partial=partial or self.recording_failed)
        except Exception:
            self.disable()
            return None, {'scope': 'continuous_duplex', 'partial': True,
                          'monitoring_status': 'unavailable', 'recording_status': 'unavailable'}
        finally:
            for task in (self.pump, self.sender):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (self.pump, self.sender) if task), return_exceptions=True)
            await self._close_publisher()
            if self.owns_client and self.http is not None:
                await self.http.aclose()

    async def _finish(self, *, partial=False):
        if self.pump:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
        recording = self.recording
        if recording.origin is not None:
            end = max([recording.ready_sample(delay_seconds=0), *recording.cursors.values()])
            step = recording.sample_rate // 50
            while recording.rendered < end:
                self._publish(recording.render(min(end, recording.rendered + step)))
                await asyncio.sleep(0)
            if self.pending and self.publisher_id and not self.failed:
                try:
                    self.queue.put_nowait((bytes(self.pending), sorted(self.keys)))
                except asyncio.QueueFull:
                    self._unavailable()
            if self.sender and not self.failed:
                try:
                    await asyncio.wait_for(self.queue.join(), 2)
                except TimeoutError:
                    self._unavailable()
        if self.sender:
            self.sender.cancel()
            await asyncio.gather(self.sender, return_exceptions=True)
        await self._close_publisher()
        metadata = recording.metadata(partial=partial)
        metadata.update(monitoring_status='unavailable' if self.failed else
                        'published' if self.sender else 'not_configured',
                        timing_scope='local caller handoff and target audio receipt; peer playback unknown')
        return (recording.finish_wav() if recording.origin is not None else None), metadata
