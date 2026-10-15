"""Offline checks of actual mixed PCM, preset integrity, and safe public previews."""
import hashlib
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import waylo_audio_conditions as noise
from app.services.waylo_livekit import pcm_wav, read_wav


def tone(rate=24000, seconds=1):
    samples = np.round(3000 * np.sin(np.arange(int(rate * seconds)) * 2 * np.pi * 440 / rate)).astype('<i2')
    return pcm_wav(samples.tobytes(), rate)


@pytest.mark.parametrize('kind', ['white', 'office', 'cafe', 'traffic'])
@pytest.mark.parametrize('rate', [16000, 24000, 48000])
def test_presets_mix_repeatably_at_the_requested_snr(kind, rate):
    audio = tone(rate)
    mixed, meta = noise.add_noise(audio, snr_db=10, seed=42, noise_type=kind)
    assert mixed == noise.add_noise(audio, snr_db=10, seed=42, noise_type=kind)[0]
    assert mixed != noise.add_noise(audio, snr_db=10, seed=43, noise_type=kind)[0]
    output_rate, output = read_wav(mixed)
    assert output_rate == rate and len(output) == len(read_wav(audio)[1])
    signal = np.frombuffer(read_wav(audio)[1], dtype='<i2').astype(float)
    added = np.frombuffer(output, dtype='<i2').astype(float) - signal
    measured_snr = 10 * math.log10(np.mean(signal ** 2) / np.mean(added ** 2))
    assert measured_snr == pytest.approx(10, abs=0.05)
    assert meta['noise_type'] == kind and meta['clipped_samples'] == 0
    if kind != 'white':
        assert meta['recording_sha256'] == noise.noise_profile(kind)['recording_sha256']
        assert meta['recording_offset_samples'] >= 0


def test_recorded_presets_are_distinct_and_loop_for_longer_utterances():
    audio = tone(16000, 21)
    outputs = {noise.add_noise(audio, snr_db=5, seed=9, noise_type=kind)[0]
        for kind in ['office', 'cafe', 'traffic']}
    assert len(outputs) == 3
    assert all(len(read_wav(item)[1]) == len(read_wav(audio)[1]) for item in outputs)


def test_lower_snr_produces_more_background_noise():
    audio = tone()
    signal = np.frombuffer(read_wav(audio)[1], dtype='<i2').astype(float)
    power = []
    for snr in [20, 5]:
        mixed = noise.add_noise(audio, snr_db=snr, seed=3, noise_type='cafe')[0]
        delta = np.frombuffer(read_wav(mixed)[1], dtype='<i2').astype(float) - signal
        power.append(np.mean(delta ** 2))
    assert 10 * math.log10(power[1] / power[0]) == pytest.approx(15, abs=0.1)


def test_changed_or_unknown_recordings_fail_closed():
    with pytest.raises(ValueError, match='changed since'):
        noise.add_noise(tone(), snr_db=20, seed=1, noise_type='office', recording_sha256='wrong')
    with pytest.raises(ValueError, match='Unsupported'):
        noise.add_noise(tone(), snr_db=20, seed=1, noise_type='../office')


def test_corrupt_bundled_recording_is_rejected(monkeypatch, tmp_path):
    (tmp_path / 'manifest.json').write_bytes((noise._ASSETS / 'manifest.json').read_bytes())
    (tmp_path / 'office.wav').write_bytes(b'corrupt')
    monkeypatch.setattr(noise, '_ASSETS', tmp_path)
    with pytest.raises(ValueError, match='hash mismatch'):
        noise.noise_profile('office')


@pytest.mark.parametrize('kind', ['office', 'cafe', 'traffic'])
def test_preview_serves_the_same_recording_used_by_the_mixer(kind):
    response = TestClient(app).get(f'/api/execution/noise-presets/{kind}/audio')
    assert response.status_code == 200 and response.headers['content-type'] == 'audio/wav'
    assert hashlib.sha256(response.content).hexdigest() == noise.noise_profile(kind)['recording_sha256']
    assert read_wav(response.content)[0] == 24000


def test_preview_does_not_accept_arbitrary_asset_names():
    assert TestClient(app).get('/api/execution/noise-presets/private/audio').status_code == 422
