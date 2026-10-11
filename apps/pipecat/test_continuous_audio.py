import io
import wave

import numpy as np
import pytest

from continuous_audio import ContinuousDuplexAudio


def test_clocked_mix_preserves_greeting_gaps_overlap_and_between_turn_audio():
    now = [10.0]
    audio = ContinuousDuplexAudio(clock=lambda: now[0], sample_rate=1000)
    tone = lambda value, length: np.full(length, value, dtype='<i2').tobytes()
    # Greeting before exchange capture is opened.
    audio.add('target_to_tester', tone(100, 100), sample_rate=1000, channels=1)
    now[0] += .3
    audio.add('tester_to_target', tone(1000, 300), sample_rate=1000, channels=1)
    now[0] += .1
    audio.add('target_to_tester', tone(2000, 100), sample_rate=1000, channels=1)
    now[0] += .4
    audio.add('target_to_tester', tone(400, 100), sample_rate=1000, channels=1)
    live = [audio.render(400), audio.render(900)]
    expected = np.array([100] * 100 + [0] * 200 + [1000] * 100
                        + [3000] * 100 + [1000] * 100 + [0] * 200
                        + [400] * 100, dtype='<i2')
    assert b''.join(live) == expected.tobytes()
    with wave.open(io.BytesIO(audio.wav())) as recorded:
        assert recorded.readframes(recorded.getnframes()) == b''.join(live)
    assert audio.metadata(partial=False)['duration_ms'] == 900


def test_contiguous_packets_ignore_small_scheduler_jitter_and_clip_overlap():
    now = [0.0]
    audio = ContinuousDuplexAudio(clock=lambda: now[0], sample_rate=1000)
    loud = np.full(20, 30000, dtype='<i2').tobytes()
    audio.add('target_to_tester', loud, sample_rate=1000, channels=1)
    now[0] = .022
    audio.add('target_to_tester', loud, sample_rate=1000, channels=1)
    audio.add('tester_to_target', loud, sample_rate=1000, channels=1)
    pcm = np.frombuffer(audio.render(42), dtype='<i2')
    assert np.all(pcm[:22] == 30000)
    assert np.all(pcm[22:40] == 32767)
    assert np.all(pcm[40:] == 30000)


def test_stereo_and_resampling_and_partial_call_trim():
    now = [0.0]
    audio = ContinuousDuplexAudio(clock=lambda: now[0], sample_rate=1000)
    stereo = np.array([[100, 300]] * 100, dtype='<i2').tobytes()
    audio.add('tester_to_target', stereo, sample_rate=500, channels=2)
    now[0] = .05
    # Stop during a prebuffered utterance: never render the unsent future tail.
    pcm = audio.render(audio.ready_sample(delay_seconds=0))
    assert pcm == np.full(50, 200, dtype='<i2').tobytes()
    assert audio.metadata(partial=True)['partial'] is True
    assert audio.metadata(partial=True)['duration_ms'] == 50


def test_recording_limits_and_invalid_formats():
    audio = ContinuousDuplexAudio(sample_rate=1000, max_seconds=.01)
    with pytest.raises(ValueError, match='duration limit'):
        audio.add('target_to_tester', bytes(40), sample_rate=1000, channels=1)
    with pytest.raises(ValueError, match='format'):
        audio.add('target_to_tester', b'1', sample_rate=1000, channels=1)
