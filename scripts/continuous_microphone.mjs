// A single paced sender: speech changes, the environment does not stop.
export class ContinuousMicrophone {
  constructor({ noise, send, observe, sampleRate = 48000 }) {
    if (!noise?.length) throw new Error('Background noise is empty.');
    this.noise = noise;
    this.send = send;
    this.observe = observe;
    this.sampleRate = sampleRate;
    this.position = 0;
    this.closed = false;
    this.pending = null;
    this.clipped = 0;
  }
  start() { this.task = this.run(); }
  async play(samples, turnPair) {
    if (this.closed || this.pending || this.error || !samples.length) throw new Error('Caller microphone unavailable.');
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
          const value = signal + this.noise[this.position];
          this.position = (this.position + 1) % this.noise.length;
          this.clipped += Number(value < -32768 || value > 32767);
          frame[i] = Math.max(-32768, Math.min(32767, value));
        }
        const before = Date.now();
        this.send(frame);
        this.observe(frame, pending?.turnPair || 0);
        if (pending) {
          const count = Math.min(size, pending.samples.length - pending.offset);
          pending.chunks.push(frame.slice(0, count));
          pending.offset += count;
        }
        await new Promise((resolve) => setTimeout(resolve, Math.max(0, 10 - (Date.now() - before))));
        if (pending && pending.offset === pending.samples.length) {
          this.pending = null;
          const played = new Int16Array(pending.samples.length);
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
  constructor(sampleRate = 48000) { this.sampleRate = sampleRate; this.chunks = []; this.started = null; this.cursors = new Map(); }
  add(direction, samples, sampleRate) {
    const now = Date.now();
    this.started ??= now;
    const count = Math.round(samples.length * this.sampleRate / sampleRate);
    const converted = new Int16Array(count);
    for (let i = 0; i < count; i += 1) converted[i] = samples[Math.min(samples.length - 1, Math.floor(i * sampleRate / this.sampleRate))];
    const observed = Math.round((now - this.started) * this.sampleRate / 1000);
    const previous = this.cursors.get(direction);
    const offset = previous === undefined || observed - previous > this.sampleRate * .08 ? observed : previous;
    if (offset + count > this.sampleRate * 310) throw new Error('Conversation recording duration limit.');
    this.cursors.set(direction, offset + count);
    this.chunks.push({ offset, samples: converted });
  }
  pcm() {
    const end = Math.max(0, ...this.cursors.values());
    const mix = new Int32Array(end);
    for (const chunk of this.chunks) for (let i = 0; i < chunk.samples.length; i += 1) mix[chunk.offset + i] += chunk.samples[i];
    const result = new Int16Array(end);
    for (let i = 0; i < end; i += 1) result[i] = Math.max(-32768, Math.min(32767, mix[i]));
    this.chunks = [];
    return result;
  }
}
