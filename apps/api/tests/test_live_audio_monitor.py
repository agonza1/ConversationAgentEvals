import asyncio
import base64
import io
import time
import wave
from types import SimpleNamespace

import httpx
import numpy as np

from app.services.live_audio_monitor import LiveAudioMonitor
from app.services.waylo_livekit import LiveKitVoicePeer, pcm_wav


def test_live_published_mix_matches_recording_with_gaps_and_overlap():
    requests, now = [], [0.0]
    def handler(request):
        import json
        payload = json.loads(request.content)
        requests.append((request.url.path, payload, dict(request.headers)))
        return httpx.Response(200, json={'publisher_id': 'opaque-publisher'})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monitor = LiveAudioMonitor(execution_run_id='exec-test', conversation_id='conv',
                runtime=SimpleNamespace(internal_token='internal-only', pipecat_service_url='http://runtime'),
                client=client, clock=lambda: now[0])
            await monitor.start()
            tone = lambda value, count: np.full(count, value, dtype='<i2').tobytes()
            monitor.add('target_to_tester', tone(100, 2400), sample_rate=24000)
            now[0] = .3
            monitor.add('tester_to_target', tone(1000, 7200), sample_rate=24000)
            now[0] = .4
            monitor.add('target_to_tester', tone(2000, 2400), sample_rate=24000)
            now[0] = .6
            wav, metadata = await monitor.finish()
            with wave.open(io.BytesIO(wav)) as reader:
                recorded = reader.readframes(reader.getnframes())
            streamed = b''.join(base64.b64decode(payload['pcm16_base64']) for path, payload, _ in requests
                                if path.endswith('/audio'))
            assert streamed == recorded
            samples = np.frombuffer(recorded, dtype='<i2')
            assert np.all(samples[:2400] == 100)
            assert np.all(samples[2400:7200] == 0)
            assert np.all(samples[9600:12000] == 3000)
            assert metadata['scope'] == 'continuous_duplex'
            assert metadata['monitoring_status'] == 'published'
            assert requests[0][0].endswith('/open') and requests[-1][0].endswith('/close')
            for path, payload, headers in requests:
                assert headers['x-cae-reference-token'] == 'internal-only'
                assert 'internal-only' not in str(payload)
                if path.endswith('/audio'):
                    assert 'conv:1:tester_to_target' in payload['listener_media_keys']
    asyncio.run(exercise())


def test_monitor_network_failure_keeps_local_partial_recording_and_closes():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'publisher_id': 'publisher'}) if request.url.path.endswith('/open') else httpx.Response(503)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monitor = LiveAudioMonitor(execution_run_id='exec-test', conversation_id='conv',
                runtime=SimpleNamespace(internal_token='safe', pipecat_service_url='http://runtime'), client=client)
            await monitor.start()
            monitor.add('tester_to_target', b'\x01\x00' * 2400, sample_rate=24000)
            wav, metadata = await monitor.finish(partial=True)
            assert wav and metadata['partial'] is True
            assert metadata['monitoring_status'] == 'unavailable'
            assert any(path.endswith('/close') for path in paths)
    asyncio.run(exercise())


def test_unconfigured_monitor_still_records_and_queue_is_bounded():
    async def exercise():
        monitor = LiveAudioMonitor()
        await monitor.start()
        monitor.add('tester_to_target', b'\x01\x00' * 480, sample_rate=24000)
        wav, metadata = await monitor.finish()
        assert wav and metadata['monitoring_status'] == 'not_configured'
        assert monitor.queue.maxsize == 10
    asyncio.run(exercise())


def test_local_caller_frames_are_paced_not_enqueued_as_whole_turn(tmp_path):
    times, accepted = [], []
    class Source:
        sample_rate = 24000
        async def capture_frame(self, frame):
            times.append(time.monotonic())
            accepted.append(frame)
        async def wait_for_playout(self):
            pass
    peer = object.__new__(LiveKitVoicePeer)
    peer.origin = time.monotonic()
    peer.origin_utc = '2026-10-12T00:00:00Z'
    peer.source = Source()
    peer.rtc = SimpleNamespace(AudioFrame=lambda pcm, *args: pcm)
    peer.events, peer.monitor, peer.last_speech = [], None, 0
    pcm = b'\x01\x00' * 2400  # 100ms, five accepted 20ms frames.
    observation, wav = asyncio.run(peer.send(pcm_wav(pcm), turn_id='caller-2', reference='hello', artifact_dir=tmp_path))
    assert b''.join(accepted) == pcm and len(times) == 5
    assert times[-1] - times[0] >= .065
    assert peer.transcript_turn_id == 'caller-2'
    assert observation['peer_receipt'] == 'unknown'
    assert (tmp_path / 'caller-2-sent.wav').read_bytes() == wav
