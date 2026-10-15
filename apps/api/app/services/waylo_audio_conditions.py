"""Deterministic media perturbations on actual sent PCM, not transcript edits."""
import math
import random
from array import array

from app.services.waylo_livekit import pcm_wav, read_wav


def add_noise(audio: bytes, *, snr_db: float, seed: int) -> tuple[bytes, dict]:
    if not -5 <= snr_db <= 40:
        raise ValueError('Controlled noise SNR must be between -5 and 40 dB.')
    rate, pcm = read_wav(audio)
    samples = array('h', pcm)
    if not samples:
        raise ValueError('Cannot add controlled noise to empty audio.')
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    generator = random.Random(seed)
    raw_noise = [generator.gauss(0, 1) for _ in samples]
    noise_rms = math.sqrt(sum(n * n for n in raw_noise) / len(raw_noise))
    gain = rms / 10 ** (snr_db / 20) / noise_rms
    output, clipped = array('h'), 0
    for sample, noise in zip(samples, raw_noise, strict=True):
        value = round(sample + noise * gain)
        clipped += int(value < -32768 or value > 32767)
        output.append(max(-32768, min(32767, value)))
    return pcm_wav(output.tobytes(), rate), {'condition': 'seeded_white_noise', 'snr_db_requested': snr_db,
        'seed': seed, 'clipped_samples': clipped, 'signal_rms_pcm16': rms,
        'scope': 'synthetic_condition_only; not all real-world noise or accents'}
