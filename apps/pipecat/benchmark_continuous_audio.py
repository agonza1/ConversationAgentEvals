"""Offline resource benchmark; no models, calls, network or audio devices.

Run: python apps/pipecat/benchmark_continuous_audio.py --seconds 120
Reports isolated mixer costs, not full voice-stack resource requirements.
"""

import argparse
import base64
import hashlib
import json
import statistics
import time
import tracemalloc

import numpy as np

from continuous_audio import ContinuousDuplexAudio


def simulate(seconds: float, quantum_ms: int) -> ContinuousDuplexAudio:
    now = [0.0]
    audio = ContinuousDuplexAudio(clock=lambda: now[0], max_seconds=seconds + 5)
    target = np.full(320, 100, dtype='<i2').tobytes()
    caller = np.full(48000, 200, dtype='<i2').tobytes()
    step = audio.sample_rate * quantum_ms // 1000
    for tick in range(round(seconds * 50)):
        now[0] = tick * .02
        if tick % 250 == 0:
            audio.add('tester_to_target', caller, sample_rate=24000, channels=1)
        audio.add('target_to_tester', target, sample_rate=16000, channels=1)
        ready = audio.ready_sample()
        while audio.rendered + step <= ready:
            audio.render(audio.rendered + step)
    audio.render(round(seconds * audio.sample_rate))
    return audio


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--quantum-ms', type=int, choices=(10, 20), default=20)
    parser.add_argument('--duplicate-terminal-wav', action='store_true',
                        help='Simulate the previous redundant full-WAV terminal payload.')
    options = parser.parse_args(args)
    if not 1 <= options.seconds <= 600:
        parser.error('--seconds must be between 1 and 600')
    # Measure CPU without tracing, which itself adds allocation overhead.
    simulate(1, options.quantum_ms)
    times = []
    for _ in range(3):
        started = time.process_time()
        simulate(options.seconds, options.quantum_ms)
        times.append(time.process_time() - started)
    tracemalloc.start()
    audio = simulate(options.seconds, options.quantum_ms)
    current, capture_peak = tracemalloc.get_traced_memory()
    pcm_sha256 = hashlib.sha256(audio.pcm).hexdigest()
    wav = audio.wav() if options.duplicate_terminal_wav else audio.finish_wav()
    encoded = base64.b64encode(wav).decode('ascii')
    event = json.dumps({'type': 'live_audio', 'audio_wav_base64': encoded})
    media = {'recording_metadata': audio.metadata(partial=False)}
    if options.duplicate_terminal_wav:
        media['conversation_audio_wav_base64'] = encoded
    terminal = json.dumps({'type': 'complete', 'result': {'media': media}})
    _, finish_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    cpu = statistics.median(times)
    print(json.dumps({
        'seconds_simulated': options.seconds, 'quantum_ms': options.quantum_ms,
        'median_cpu_seconds': round(cpu, 4),
        'one_core_percent_equivalent': round(cpu / options.seconds * 100, 3),
        'capture_current_mib': round(current / 1024 ** 2, 2),
        'capture_peak_mib': round(capture_peak / 1024 ** 2, 2),
        'finish_peak_mib': round(finish_peak / 1024 ** 2, 2),
        'recording_wire_bytes': len(event) + len(terminal),
        'pcm_sha256': pcm_sha256,
    }))


if __name__ == '__main__':
    main()
