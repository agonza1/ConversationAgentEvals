import asyncio
import base64
import io
import time
import wave
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

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


def test_silent_pcm_keeps_timeline_without_false_content_markers():
    async def exercise():
        monitor = LiveAudioMonitor(conversation_id='conv')
        monitor.add('target_to_tester', bytes(4800), sample_rate=24000)
        assert monitor.keys == set()
        monitor.add('tester_to_target', b'\x01\x00' * 480, sample_rate=24000, turn_pair=2)
        assert monitor.keys == {'conv:2:tester_to_target'}
        wav, _ = await monitor.finish()
        assert wav
    asyncio.run(exercise())


def test_bad_mixer_does_not_escape_or_claim_success_and_notifies_once():
    async def exercise():
        failures = []
        monitor = LiveAudioMonitor(max_seconds=.001, on_unavailable=lambda: failures.append(True))
        await monitor.start()
        monitor.add('target_to_tester', bytes(4800), sample_rate=24000)
        monitor.add('tester_to_target', bytes(4800), sample_rate=24000)
        _, metadata = await monitor.finish()
        assert metadata['partial'] is True and metadata['monitoring_status'] == 'unavailable'
        assert failures == [True]
    asyncio.run(exercise())


def test_final_render_failure_closes_monitor_without_escaping():
    async def exercise():
        monitor = LiveAudioMonitor()
        monitor.add('target_to_tester', bytes(4800), sample_rate=24000)
        def broken(end):
            raise ValueError('broken rendering')
        monitor.recording.render = broken
        wav, metadata = await monitor.finish()
        assert wav is None and metadata['recording_status'] == 'unavailable'
    asyncio.run(exercise())


def test_slow_listener_queue_fails_visibly_instead_of_accumulating_lag():
    async def exercise():
        failures = []
        monitor = LiveAudioMonitor(on_unavailable=lambda: failures.append(True))
        monitor.publisher_id = 'test-publisher'
        monitor.sender = asyncio.create_task(asyncio.sleep(60))
        for _ in range(11):
            monitor._publish(bytes(4800))
        assert monitor.failed and monitor.queue.qsize() == 10 and failures == [True]
        await monitor.finish()
        assert monitor.sender.done()
    asyncio.run(exercise())


def test_target_receive_monitor_failure_does_not_stop_raw_capture():
    async def exercise():
        class Monitor:
            def add(self, *args, **kwargs):
                raise ValueError('monitor unavailable')
            def disable(self):
                pass
        class Stream:
            def __aiter__(self):
                return self.frames()
            async def frames(self):
                for _ in range(2):
                    yield SimpleNamespace(frame=SimpleNamespace(data=b'\x00\x10' * 480))
            async def aclose(self):
                pass
        peer = object.__new__(LiveKitVoicePeer)
        peer.rtc = SimpleNamespace(AudioStream=lambda *args, **kwargs: Stream())
        peer.origin, peer.remote_pcm, peer.remote_chunks = time.monotonic(), bytearray(), []
        peer.streams, peer.error, peer.speech_count, peer.monitor_turn_pair = [], None, 0, 1
        peer.monitor = Monitor()
        await peer._receive(None)
        assert peer.error is None and len(peer.remote_pcm) == 1920 and peer.speech_count == 2
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


def test_caller_monitor_failure_does_not_become_audio_delivery_failure(tmp_path):
    class Monitor:
        disabled = False
        def add(self, *args, **kwargs):
            raise RuntimeError('monitor broken')
        def disable(self):
            self.disabled = True
    class Source:
        sample_rate = 24000
        async def capture_frame(self, frame):
            pass
        async def wait_for_playout(self):
            pass
    peer = object.__new__(LiveKitVoicePeer)
    peer.origin, peer.origin_utc = time.monotonic(), '2026-10-12T00:00:00Z'
    peer.source, peer.rtc = Source(), SimpleNamespace(AudioFrame=lambda pcm, *args: pcm)
    peer.events, peer.monitor, peer.last_speech = [], Monitor(), 0
    result, _ = asyncio.run(peer.send(pcm_wav(b'\x01\x00' * 480), turn_id='caller-1', reference='hi', artifact_dir=tmp_path))
    assert result['delivery'] == 'local_queue_drained' and peer.monitor.disabled


def test_cancelled_live_call_finalizes_partial_mix_and_releases_client(tmp_path, monkeypatch):
    from app.services import waylo_livekit
    partial, closed = [], []
    class Monitor:
        def __init__(self, **kwargs):
            pass
        async def start(self):
            pass
        async def finish(self, *, partial=False):
            globals_partial.append(partial)
            return None, {}
    globals_partial = partial
    peer = object.__new__(LiveKitVoicePeer)
    async def cancelled(bootstrap):
        raise asyncio.CancelledError
    async def close():
        closed.append('peer')
    peer.connect, peer.close = cancelled, close
    class Client:
        redaction_values = ()
        async def bootstrap(self, correlation):
            return {'sessionId': 'session'}
        async def request(self, *args):
            return []
        async def close(self):
            closed.append('client')
    async def wording(*args):
        return 'hi'
    async def synthesize(*args):
        return pcm_wav(b'\x01\x00' * 480)
    monkeypatch.setattr(waylo_livekit, 'LiveAudioMonitor', Monitor)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(waylo_livekit.run_waylo_call(target={'id': 'target'}, correlation_id='exec/conv',
            scenario={'id': 'case'}, suite_id='suite', artifact_dir=tmp_path, max_exchanges=1,
            timeout_seconds=30, next_utterance=wording, synthesize=synthesize, peer=peer, client=Client()))
    assert partial == [True] and 'peer' in closed and 'client' in closed
