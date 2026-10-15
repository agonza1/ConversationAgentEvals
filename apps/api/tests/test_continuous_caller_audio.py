"""Offline publication tests: the target, monitor and saved PCM share one stream."""
import asyncio
import math
import time
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.continuous_caller_audio import ContinuousCallerAudio
from app.services.waylo_audio_conditions import ContinuousNoise
from app.services.waylo_livekit import LiveKitVoicePeer, pcm_wav, read_wav


def reference(rate=24000, seconds=1):
    samples = np.round(3000 * np.sin(np.arange(round(rate * seconds)) * 2 * np.pi * 440 / rate)).astype('<i2')
    return pcm_wav(samples.tobytes(), rate)


@pytest.mark.parametrize('kind', ['white', 'office', 'cafe', 'traffic'])
@pytest.mark.parametrize('rate', [16000, 24000, 48000])
def test_cursor_is_chunk_invariant_and_keeps_noise_during_silence(kind, rate):
    def mixer(): return ContinuousNoise(reference(rate), snr_db=10, seed=42, noise_type=kind)
    pcm = read_wav(reference(rate))[1]
    whole = mixer().mix(pcm)
    split = mixer()
    chunked = b''.join(split.mix(pcm[i:i + 962]) for i in range(0, len(pcm), 962))
    assert whole == chunked
    assert any(split.mix(bytes(rate // 50 * 2)))
    assert split.metadata()['scope'] == 'continuous_caller_track'
    assert split.metadata()['level_reference'] == 'first_caller_utterance_rms'
    assert whole != ContinuousNoise(reference(rate), snr_db=10, seed=43, noise_type=kind).mix(pcm)


def test_snr_is_calibrated_once_not_gated_or_recalibrated_by_later_turns():
    mixer = ContinuousNoise(reference(), snr_db=10, seed=42)
    clean = read_wav(reference())[1]
    noisy = np.frombuffer(mixer.mix(clean), dtype='<i2').astype(float)
    signal = np.frombuffer(clean, dtype='<i2').astype(float)
    measured = 10 * math.log10(np.mean(signal ** 2) / np.mean((noisy - signal) ** 2))
    assert measured == pytest.approx(10, abs=.15)
    gain = mixer.gain
    silence = np.frombuffer(mixer.mix(bytes(len(clean))), dtype='<i2').astype(float)
    assert np.sqrt(np.mean(silence ** 2)) == pytest.approx(mixer.metadata()['noise_rms_pcm16'], rel=.03)
    mixer.mix(np.round(signal / 10).astype('<i2').tobytes())
    assert mixer.gain == gain


@pytest.mark.parametrize('kind', ['office', 'cafe', 'traffic'])
@pytest.mark.parametrize('seed', [7, 42])
@pytest.mark.parametrize('seconds', [.1, 1, 21])
def test_actual_recorded_first_turn_snr_uses_seeded_window_including_wraparound(kind, seed, seconds):
    audio = reference(seconds=seconds)
    mixer = ContinuousNoise(audio, snr_db=10, seed=seed, noise_type=kind)
    clean = np.frombuffer(read_wav(audio)[1], dtype='<i2').astype(float)
    sent = np.frombuffer(mixer.mix(read_wav(audio)[1]), dtype='<i2').astype(float)
    measured = 10 * math.log10(np.mean(clean ** 2) / np.mean((sent - clean) ** 2))
    assert measured == pytest.approx(10, abs=.03)
    assert mixer.metadata()['noise_calibration_samples'] == len(clean)
    gain = mixer.gain
    mixer.mix(bytes(960)); assert mixer.gain == gain


def test_silent_seeded_recording_window_is_rejected_before_bootstrap(monkeypatch):
    from app.services import waylo_audio_conditions as noise
    monkeypatch.setattr(noise, '_recording', lambda *args: np.zeros(480000))
    with pytest.raises(ValueError, match='calibration window'):
        ContinuousNoise(reference(), snr_db=10, seed=7, noise_type='office')


class Source:
    def __init__(self, fail_at=None):
        self.frames = []; self.fail_at = fail_at; self.target_speaking = False
        self.states = []; self.writing = False
    async def capture_frame(self, frame):
        assert not self.writing  # one writer owns all caller speech and noise
        self.writing = True
        try:
            await asyncio.sleep(0)
            if self.fail_at is not None and len(self.frames) == self.fail_at:
                raise RuntimeError('private SDK failure')
            self.frames.append(frame); self.states.append(self.target_speaking)
        finally: self.writing = False
    async def wait_for_playout(self):
        await asyncio.sleep(.02)


def stream_at(path, source=None, kind='white'):
    source = source or Source()
    observed, errors = [], []
    origin = time.monotonic()
    stream = ContinuousCallerAudio(source=source, make_frame=lambda pcm: pcm,
        mixer=ContinuousNoise(reference(), snr_db=10, seed=7, noise_type=kind),
        on_audio=lambda pcm, rate: observed.append(pcm), on_error=errors.append,
        now_ms=lambda: (time.monotonic() - origin) * 1000, path=path)
    return stream, source, observed, errors


async def wait_for_frames(source, count):
    async with asyncio.timeout(2):
        while len(source.frames) < count:
            await asyncio.sleep(.005)


@pytest.mark.parametrize('kind', ['white', 'office', 'cafe', 'traffic'])
def test_noise_published_through_greeting_turn_gaps_and_target_speech(tmp_path, kind):
    async def run():
        stream, source, observed, errors = stream_at(tmp_path / 'continuous.wav', kind=kind)
        stream.start()
        await wait_for_frames(source, 2)  # greeting / no caller utterance yet
        before = len(source.frames)
        first = stream.enqueue(b'\x00\x08' * 960)
        await asyncio.wait_for(first.done, 1)
        source.target_speaking = True
        await wait_for_frames(source, len(source.frames) + 3)  # target response
        source.target_speaking = False
        gap = len(source.frames)
        await wait_for_frames(source, gap + 2)  # between turns / tester planning
        second = stream.enqueue(b'\x00\x04' * 600)
        await asyncio.wait_for(second.done, 1)
        await stream.close()
        count = len(source.frames)
        await asyncio.sleep(.03)
        assert len(source.frames) == count and stream.task.done()
        assert before >= 2 and gap > before + 2
        assert all(any(pcm) for pcm in source.frames[:before])
        assert any(state and any(pcm) for state, pcm in zip(source.states, source.frames))
        assert all(any(pcm) for pcm in source.frames[gap:gap + 2])
        assert observed == source.frames and not errors
        assert len(first.accepted) == 1920 and len(second.accepted) == 1200
        with wave.open(str(stream.path)) as reader:
            assert reader.readframes(reader.getnframes()) == b''.join(source.frames)
        assert stream.metadata()['accepted_samples'] == count * 480
    asyncio.run(run())


def test_failed_capture_retains_only_accepted_frames_and_stops(tmp_path):
    async def run():
        stream, source, observed, errors = stream_at(tmp_path / 'partial.wav', Source(fail_at=1))
        speech = stream.enqueue(b'\x00\x08' * 1920)
        stream.start()
        with pytest.raises(RuntimeError, match='accepted audio retained'):
            await asyncio.wait_for(speech.done, 1)
        await stream.close()
        assert len(source.frames) == 1 and observed == source.frames
        assert len(speech.accepted) == 960
        assert 'private' not in errors[0]
        with wave.open(str(stream.path)) as reader: assert reader.getnframes() == 480
        assert stream.metadata()['partial'] is True
        with pytest.raises(ValueError): stream.enqueue(b'\x00\x01')
    asyncio.run(run())


def test_caller_leading_silence_does_not_count_background_as_spoken_content(tmp_path):
    async def run():
        stream, _, _, _ = stream_at(tmp_path / 'activity.wav')
        flags = []
        stream.on_audio = lambda pcm, rate: flags.append(stream.speech_activity)
        speech = stream.enqueue(bytes(960) + b'\x00\x08' * 480)
        stream.start()
        await asyncio.wait_for(speech.done, 1)
        await stream.close()
        assert flags[:2] == [False, True]
    asyncio.run(run())


def test_cancelled_send_stops_publisher_and_finalizes_partial_utterance(tmp_path):
    async def run():
        stream, source, _, _ = stream_at(tmp_path / 'whole.wav')
        peer = SimpleNamespace(continuous_caller=stream, noise_mixer=stream.mixer, events=[],
            last_speech=0, now_ms=stream.now_ms, origin_utc='2026-10-15T12:00:00Z')
        stream.start()
        sender = asyncio.create_task(LiveKitVoicePeer._send_continuous(peer, reference(),
            turn_id='caller-1', reference='fictional caller', artifact_dir=tmp_path))
        await wait_for_frames(source, 3)
        sender.cancel()
        with pytest.raises(asyncio.CancelledError): await sender
        assert stream.task.done()
        _, accepted = read_wav((tmp_path / 'caller-1-sent.wav').read_bytes())
        assert 0 < len(accepted) < len(read_wav(reference())[1])
        assert peer.events[-1]['accepted_samples'] == len(accepted) // 2
        assert peer.events[-1]['delivery'] == 'partial_or_unconfirmed'
        count = len(source.frames); await asyncio.sleep(.03)
        assert len(source.frames) == count
    asyncio.run(run())


@pytest.mark.parametrize('change', [{'snr_db': 41}, {'recording_sha256': 'wrong', 'noise_type': 'office'}])
def test_noise_validation_occurs_before_a_remote_call(change):
    with pytest.raises(ValueError): ContinuousNoise(reference(), **{'snr_db': 10, 'seed': 1, **change})


def test_real_peer_publishes_one_mic_track_and_records_noise_with_target_audio(tmp_path):
    from app.services.live_audio_monitor import LiveAudioMonitor

    class RtcSource(Source):
        def __init__(self, rate, channels):
            super().__init__(); self.sample_rate = rate; self.closed = False
        async def aclose(self): self.closed = True
    class Participant:
        identity = 'tester'; track_publications = {}
        def __init__(self): self.published = []
        async def publish_track(self, track, options): self.published.append(track)
    class Room:
        name = 'fake-room'
        def __init__(self): self.local_participant = Participant(); self.disconnected = False
        def register_text_stream_handler(self, *args): pass
        def on(self, *args): return lambda fn: fn
        async def connect(self, *args, **kwargs): pass
        async def disconnect(self): self.disconnected = True
    rtc = SimpleNamespace(Room=Room, RoomOptions=lambda **kw: kw, AudioSource=RtcSource,
        AudioFrame=lambda pcm, *args: pcm, TrackPublishOptions=lambda **kw: kw,
        TrackSource=SimpleNamespace(SOURCE_MICROPHONE='microphone'),
        LocalAudioTrack=SimpleNamespace(create_audio_track=lambda name, source: name))

    async def run():
        peer = LiveKitVoicePeer(rtc=rtc)
        peer.ready.set()
        peer.configure_noise(reference(), {'snr_db': 10, 'seed': 42, 'noise_type': 'cafe'}, tmp_path)
        monitor = LiveAudioMonitor(max_seconds=5)
        peer.monitor = monitor
        await monitor.start()  # No provider/listener endpoints are configured.
        await peer.connect({'livekitUrl': 'unused', 'participantToken': 'dummy', 'roomName': 'fake-room'})
        await wait_for_frames(peer.source, 2)
        assert monitor.keys == set()  # ambience must not mark caller speech as heard
        receipt, sent = await peer.send(pcm_wav(b'\x00\x08' * 600), turn_id='caller-1', reference='hello', artifact_dir=tmp_path)
        assert 'None:1:tester_to_target' in monitor.keys
        # Receiving target speech does not stop the caller microphone or add a second noise bed.
        target = b'\x00\x04' * 480
        peer._monitor_audio('target_to_tester', target, 24000)
        before = len(peer.source.frames)
        await wait_for_frames(peer.source, before + 3)
        await peer.close()
        wav, metadata = await monitor.finish()
        assert len(peer.room.local_participant.published) == 1
        assert len(peer.source.frames) > before and peer.source.closed and peer.room.disconnected
        assert receipt['audio_condition']['scope'] == 'continuous_caller_track'
        assert receipt['accepted_samples'] == 600 and receipt['peer_receipt'] == 'unknown'
        assert read_wav(sent)[1] != b'\x00\x08' * 600
        assert wav and len(wav) > len(sent) and not metadata['partial']
        event = next(e for e in peer.events if e['event_type'] == 'tester.background_noise.sent')
        assert event['audio_condition']['accepted_samples'] == len(peer.source.frames) * 480
        with wave.open(str(tmp_path / 'caller-continuous-sent.wav')) as reader:
            assert reader.readframes(reader.getnframes()) == b''.join(peer.source.frames)
    asyncio.run(run())


def test_unsupported_noise_peer_fails_before_bootstrap(tmp_path):
    from test_waylo_review_regressions import Client, Peer
    from test_waylo_target import TARGET
    from app.services.waylo_livekit import run_waylo_call
    from app.services.waylo_target import WayloError
    client = Client()
    async def wording(*args): return 'Hello'
    async def synthesis(*args): return reference(), {'kind': 'noise', 'snr_db': 10, 'seed': 42}
    with pytest.raises(WayloError, match='does not support continuous'):
        asyncio.run(run_waylo_call(target=TARGET, correlation_id='offline-only', scenario={'id': 'case'},
            suite_id='suite', artifact_dir=tmp_path, max_exchanges=1, timeout_seconds=5,
            next_utterance=wording, synthesize=synthesis, client=client, peer=Peer()))
    assert client.bootstraps == 0
