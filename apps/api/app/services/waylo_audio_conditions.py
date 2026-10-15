"""Deterministic media perturbations on actual sent PCM, not transcript edits."""
import math
import random
import hashlib
import json
import io
import wave
from array import array
from functools import lru_cache
from pathlib import Path

def pcm_wav(pcm: bytes, rate: int = 24000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return output.getvalue()


def read_wav(audio: bytes):
    with wave.open(io.BytesIO(audio), 'rb') as reader:
        if (len(audio) > 10_000_000 or reader.getcomptype() != 'NONE'
                or reader.getsampwidth() != 2 or reader.getnchannels() != 1
                or reader.getframerate() not in {16000, 24000, 48000}):
            raise ValueError('Background noise requires mono PCM16 WAV at 16, 24 or 48 kHz.')
        pcm = reader.readframes(reader.getnframes())
        if not pcm or len(pcm) != reader.getnframes() * 2:
            raise ValueError('Background noise requires complete nonempty PCM audio.')
        return reader.getframerate(), pcm


_ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'background-noise'
if not _ASSETS.exists():  # Standalone Pipecat worker image.
    _ASSETS = Path(__file__).resolve().parent / 'background-noise'


def noise_profile(noise_type: str = 'white') -> dict:
    if noise_type == 'white':
        return {'condition': 'seeded_white_noise', 'noise_type': 'white',
            'scope': 'synthetic_condition_only; not all real-world noise or accents'}
    if noise_type not in {'office', 'cafe', 'traffic'}:
        raise ValueError('Unsupported tester background noise type.')
    recording = json.loads((_ASSETS / 'manifest.json').read_text(encoding='utf-8'))[noise_type]
    if hashlib.sha256((_ASSETS / f'{noise_type}.wav').read_bytes()).hexdigest() != recording['sha256']:
        raise ValueError('Background recording hash mismatch; refusing to change the test condition.')
    return {'condition': f'recorded_{noise_type}_noise', 'noise_type': noise_type,
        'recording_sha256': recording['sha256'], 'recording_source': recording['source'],
        'scope': 'recorded_ambience; tester_utterances_only'}


def noise_preview_path(noise_type: str) -> Path:
    if noise_type not in {'office', 'cafe', 'traffic'}:
        raise ValueError('Only bundled background recordings can be previewed.')
    noise_profile(noise_type)
    return _ASSETS / f'{noise_type}.wav'


@lru_cache(maxsize=9)
def _recording(noise_type: str, rate: int, expected_sha256: str):
    import numpy as np

    audio = (_ASSETS / f'{noise_type}.wav').read_bytes()
    if hashlib.sha256(audio).hexdigest() != expected_sha256:
        raise ValueError('Background recording hash mismatch; refusing to change the test condition.')
    source_rate, pcm = read_wav(audio)
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float64)
    if source_rate != rate:
        # Bundled recordings are band-limited below 8 kHz before conversion.
        samples = np.interp(np.arange(round(len(samples) * rate / source_rate)) * source_rate / rate,
            np.arange(len(samples)), samples)
    samples -= samples.mean()
    return samples


def add_noise(audio: bytes, *, snr_db: float, seed: int, noise_type: str = 'white',
              recording_sha256: str | None = None) -> tuple[bytes, dict]:
    if not math.isfinite(snr_db) or not -5 <= snr_db <= 40:
        raise ValueError('Controlled noise SNR must be between -5 and 40 dB.')
    rate, pcm = read_wav(audio)
    samples = array('h', pcm)
    if not samples:
        raise ValueError('Cannot add controlled noise to empty audio.')
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    generator = random.Random(seed)
    profile = noise_profile(noise_type)
    if recording_sha256 and recording_sha256 != profile.get('recording_sha256'):
        raise ValueError('Background recording changed since this run was queued.')
    if noise_type == 'white':
        raw_noise = [generator.gauss(0, 1) for _ in samples]
    else:
        import numpy as np

        recording = _recording(noise_type, rate, profile['recording_sha256'])
        offset = generator.randrange(len(recording))
        raw_noise = recording[(np.arange(len(samples)) + offset) % len(recording)]
        profile['recording_offset_samples'] = offset
    noise_rms = math.sqrt(sum(n * n for n in raw_noise) / len(raw_noise))
    if not noise_rms:
        raise ValueError('Background recording has no audible samples.')
    gain = rms / 10 ** (snr_db / 20) / noise_rms
    output, clipped = array('h'), 0
    for sample, noise in zip(samples, raw_noise, strict=True):
        value = round(sample + noise * gain)
        clipped += int(value < -32768 or value > 32767)
        output.append(max(-32768, min(32767, value)))
    return pcm_wav(output.tobytes(), rate), {**profile, 'snr_db_requested': snr_db,
        'seed': seed, 'clipped_samples': clipped, 'signal_rms_pcm16': rms,
        'noise_rms_pcm16': noise_rms * gain}


class ContinuousNoise:
    """One seeded ambience cursor and fixed gain for the entire caller track.

    Calibrate once from the first caller WAV, not from silence or target speech.
    Chunk boundaries and later turn lengths do not restart or gate the noise.
    """
    def __init__(self, reference_audio: bytes, *, snr_db: float, seed: int,
                 noise_type: str = 'white', recording_sha256: str | None = None):
        if type(seed) is not int or not 0 <= seed <= 4294967295:
            raise ValueError('Continuous noise seed must be a uint32 integer.')
        if not math.isfinite(snr_db) or not -5 <= snr_db <= 40:
            raise ValueError('Controlled noise SNR must be between -5 and 40 dB.')
        self.rate, reference = read_wav(reference_audio)
        samples = array('h', reference)
        signal_rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        if not signal_rms:
            raise ValueError('Continuous noise requires audible caller audio for calibration.')
        profile = noise_profile(noise_type)
        if recording_sha256 and recording_sha256 != profile.get('recording_sha256'):
            raise ValueError('Background recording changed since this run was queued.')
        self.generator = random.Random(seed)
        self.recording = None if noise_type == 'white' else _recording(noise_type, self.rate, profile['recording_sha256'])
        self.position = self.generator.randrange(len(self.recording)) if self.recording is not None else 0
        self.samples = self.clipped = 0
        self.profile = {**profile, 'scope': 'continuous_caller_track', 'seed': seed,
            'snr_db_requested': snr_db, 'level_reference': 'first_caller_utterance_rms'}
        self.recalibrate(reference_audio)

    def recalibrate(self, reference_audio: bytes) -> None:
        """Freeze speech gain at the actual cursor after any pre-call ambience."""
        rate, reference = read_wav(reference_audio)
        if rate != self.rate:
            raise ValueError('Caller calibration sample rate changed.')
        samples = array('h', reference)
        signal_rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        if not signal_rms:
            raise ValueError('Continuous noise requires audible caller audio for calibration.')
        if self.recording is None:
            calibration = random.Random()
            calibration.setstate(self.generator.getstate())  # do not rewind playback
            power = sum(calibration.gauss(0, 1) ** 2 for _ in samples)
        else:
            import numpy as np
            # Whole loops plus at most two slices: exact seeded window RMS,
            # bounded memory even when the utterance is longer than the asset.
            cycles, tail = divmod(len(samples), len(self.recording))
            power = cycles * float(np.dot(self.recording, self.recording))
            first = min(tail, len(self.recording) - self.position)
            segment = self.recording[self.position:self.position + first]
            power += float(np.dot(segment, segment))
            segment = self.recording[:tail - first]
            power += float(np.dot(segment, segment))
        noise_rms = math.sqrt(power / len(samples))
        if not noise_rms:
            raise ValueError('The seeded calibration window contains no audible background noise.')
        if self.samples:
            self.profile.update(lead_in_samples=self.samples, lead_in_noise_rms_pcm16=self.profile['noise_rms_pcm16'])
        self.gain = signal_rms / (10 ** (self.profile['snr_db_requested'] / 20)) / noise_rms
        self.profile.update({
            'noise_calibration': 'seeded_reference_window', 'noise_calibration_samples': len(samples),
            'signal_rms_pcm16': signal_rms, 'noise_rms_pcm16': noise_rms * self.gain,
            'recording_offset_samples': self.position if self.recording is not None else None})

    def mix(self, pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError('PCM16 frames require complete samples.')
        output = array('h')
        for sample in array('h', pcm):
            if self.recording is None:
                noise = self.generator.gauss(0, 1)
            else:
                noise = self.recording[self.position]
                self.position = (self.position + 1) % len(self.recording)
            value = round(sample + noise * self.gain)
            self.clipped += int(value < -32768 or value > 32767)
            output.append(max(-32768, min(32767, value)))
        self.samples += len(output)
        return output.tobytes()

    def metadata(self) -> dict:
        return {**self.profile, 'generated_samples': self.samples, 'clipped_samples': self.clipped}
