// A single paced sender: speech changes, the environment does not stop.
export class ContinuousMicrophone {
  constructor({ noise, send, observe, sampleRate = 48000, snrDb = null }) {
    if (!noise?.length) throw new Error('Background noise is empty.');
    this.noise = noise;
    this.send = send;
    this.observe = observe;
    this.sampleRate = sampleRate;
    this.position = 0;
    this.closed = false;
    this.pending = null;
    this.clipped = 0;
    this.snrDb = snrDb;
    this.noiseGain = 1;
    this.calibration = null;
    this.samplesSent = 0;
  }
  start() { this.task = this.run(); }
  async play(samples, turnPair) {
    if (this.closed || this.pending || this.error || !samples.length) throw new Error('Caller microphone unavailable.');
    if (this.calibration === null && Number.isFinite(this.snrDb)) {
      let signalPower = 0, noisePower = 0;
      for (let i = 0; i < samples.length; i += 1) {
        signalPower += samples[i] ** 2;
        noisePower += this.noise[(this.position + i) % this.noise.length] ** 2;
      }
      if (!signalPower || !noisePower) throw new Error('Caller noise calibration requires audible signal and ambience.');
      this.noiseGain = Math.sqrt(signalPower / noisePower) / 10 ** (this.snrDb / 20);
      this.calibration = { lead_in_samples: this.samplesSent, first_speech_noise_bed_offset_samples: this.position,
        noise_calibration: 'first_speech_cursor_window', noise_calibration_samples: samples.length, sample_rate_hz: this.sampleRate,
        signal_rms_pcm16: Math.sqrt(signalPower / samples.length), noise_rms_pcm16: Math.sqrt(noisePower / samples.length) * this.noiseGain };
    }
    return new Promise((resolve, reject) => {
      this.pending = { samples, turnPair, offset: 0, chunks: [], resolve, reject };
    });
  }
  async run() {
    const size = this.sampleRate / 100;
    const started = Date.now();
    try {
      while (!this.closed) {
        if (Date.now() - started > 310000) throw new Error('Caller microphone duration limit.');
        const pending = this.pending;
        const frame = new Int16Array(size);
        for (let i = 0; i < size; i += 1) {
          const signal = pending?.samples[pending.offset + i] || 0;
          const value = Math.round(signal + this.noise[this.position] * this.noiseGain);
          this.position = (this.position + 1) % this.noise.length;
          this.clipped += Number(value < -32768 || value > 32767);
          frame[i] = Math.max(-32768, Math.min(32767, value));
        }
        const before = Date.now();
        this.send(frame);
        this.samplesSent += frame.length;
        this.observe(frame, pending?.turnPair || 0);
        if (pending) {
          const count = Math.min(size, pending.samples.length - pending.offset);
          pending.chunks.push(frame.slice());
          pending.offset += count;
        }
        await new Promise((resolve) => setTimeout(resolve, Math.max(0, 10 - (Date.now() - before))));
        if (pending && pending.offset === pending.samples.length) {
          this.pending = null;
          const played = new Int16Array(pending.chunks.length * size);
          let offset = 0;
          for (const chunk of pending.chunks) { played.set(chunk, offset); offset += chunk.length; }
          pending.resolve(played);
        }
      }
    } catch (error) {
      this.error = error;
      this.pending?.reject(error);
    }
  }
  async close() {
    this.closed = true;
    this.pending?.reject(new Error('Caller microphone stopped.'));
    await this.task;
  }
}

export class CallAudioCapture {
  constructor(sampleRate = 48000) {
    this.sampleRate = sampleRate; this.chunks = []; this.started = null; this.cursors = new Map();
    this.rendered = 0; this.output = [];
  }
  add(direction, samples, sampleRate) {
    const now = Date.now();
    this.started ??= now;
    const count = Math.round(samples.length * this.sampleRate / sampleRate);
    const converted = new Int16Array(count);
    for (let i = 0; i < count; i += 1) converted[i] = samples[Math.min(samples.length - 1, Math.floor(i * sampleRate / this.sampleRate))];
    const observed = Math.round((now - this.started) * this.sampleRate / 1000);
    const previous = this.cursors.get(direction);
    const offset = Math.max(this.rendered, previous === undefined || observed - previous > this.sampleRate * .08 ? observed : previous);
    if (offset + count > this.sampleRate * 310) throw new Error('Conversation recording duration limit.');
    this.cursors.set(direction, offset + count);
    this.chunks.push({ offset, samples: converted });
  }
  render(final = false) {
    const latest = Math.max(0, ...this.cursors.values());
    const ready = this.started === null ? 0 : Math.floor((Date.now() - this.started - 60) * this.sampleRate / 1000);
    const end = Math.max(this.rendered, final ? latest : Math.min(latest, ready));
    const mix = new Int32Array(end - this.rendered);
    for (const chunk of this.chunks) {
      const lo = Math.max(chunk.offset, this.rendered), hi = Math.min(chunk.offset + chunk.samples.length, end);
      for (let i = lo; i < hi; i += 1) mix[i - this.rendered] += chunk.samples[i - chunk.offset];
    }
    const result = new Int16Array(mix.length);
    for (let i = 0; i < mix.length; i += 1) result[i] = Math.max(-32768, Math.min(32767, mix[i]));
    this.rendered = end;
    this.chunks = this.chunks.filter((chunk) => chunk.offset + chunk.samples.length > end);
    if (result.length) this.output.push(result);
    return result;
  }
  pcm() {
    this.render(true);
    const result = new Int16Array(this.rendered);
    let offset = 0;
    for (const block of this.output) { result.set(block, offset); offset += block.length; }
    this.output = [];
    return result;
  }
}
