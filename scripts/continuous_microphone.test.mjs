import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ContinuousMicrophone, CallAudioCapture } from './continuous_microphone.mjs';
import fs from 'node:fs';
import vm from 'node:vm';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('one microphone sends noise through pauses and mixes each caller turn', async () => {
  const sent = [], observed = [];
  const mic = new ContinuousMicrophone({ noise: new Int16Array([100, -100]),
    send: (frame) => sent.push(frame), observe: (frame, turn) => observed.push({ frame, turn }) });
  mic.start();
  try {
    const first = await mic.play(new Int16Array(960).fill(1000), 1);
    assert.equal(first[0], 1100);
    const before = sent.length;
    const deadline = Date.now() + 2000;
    while (sent.length < before + 3 && Date.now() < deadline) await sleep(5);
    assert.ok(sent.length >= before + 3);
    assert.ok(observed.slice(before).some(({ turn }) => turn === 0));
    const second = await mic.play(new Int16Array(480).fill(-1000), 2);
    assert.equal(second[0], -900);
    assert.deepEqual(observed.map(({ frame }) => frame), sent);
    assert.ok(sent.every((frame) => frame.some((sample) => sample !== 0)));
  } finally { await mic.close(); }
  const count = sent.length;
  await sleep(30);
  assert.equal(sent.length, count);
});

test('continuous capture overlaps directions, does not concatenate them', () => {
  const capture = new CallAudioCapture(1000);
  capture.add('tester_to_target', new Int16Array(100).fill(50), 1000);
  capture.add('target_to_tester', new Int16Array(100).fill(150), 1000);
  const pcm = capture.pcm();
  assert.ok(pcm.length < 150);
  assert.ok(pcm.some((sample) => sample === 200));
});

test('live mixed blocks are exact slices of the recorded conversation', () => {
  const original = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    const capture = new CallAudioCapture(1000);
    capture.add('tester_to_target', new Int16Array(100).fill(50), 1000);
    capture.add('target_to_tester', new Int16Array(100).fill(150), 1000);
    now += 80;
    const live = capture.render();
    assert.equal(live.length, 20);
    assert.ok(live.every((sample) => sample === 200));
    const recording = capture.pcm();
    assert.equal(recording.length, 100);
    assert.deepEqual(recording.slice(0, live.length), live);
    assert.ok(recording.every((sample) => sample === 200));
  } finally { Date.now = original; }
});

test('failure never publishes rejected microphone PCM', async () => {
  const observed = [];
  const mic = new ContinuousMicrophone({ noise: new Int16Array([1]),
    send: () => { throw new Error('offline send failure'); }, observe: (frame) => observed.push(frame) });
  const pending = mic.play(new Int16Array(480).fill(1), 1);
  mic.start();
  await assert.rejects(pending, /offline send failure/);
  assert.deepEqual(observed, []);
  await mic.close();
});

test('SignalWire publishes one mixed track without marking idle noise as caller speech', async () => {
  const source = fs.readFileSync(new URL('./signalwire_holyguacamole_smoke.mjs', import.meta.url), 'utf8');
  const start = source.indexOf('class CaeLiveAudioPublisher');
  const end = source.indexOf('\nfunction baseResult', start);
  const context = { Buffer, Set, OUTBOUND_SAMPLE_RATE: 48000,
    pcm16ToBuffer: (samples) => Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength),
    redact: String };
  vm.createContext(context);
  vm.runInContext(`${source.slice(start, end)}\nglobalThis.Publisher = CaeLiveAudioPublisher;`, context);
  const publisher = new context.Publisher({ livePublishExecutionRunId: 'run', livePublishSessionId: 'session', livePublishPublisherId: 'publisher' });
  publisher.callCapture = new CallAudioCapture();
  publisher.callCapture.add('tester_to_target', new Int16Array(480).fill(50), 48000);
  publisher.callCapture.add('target_to_tester', new Int16Array(480).fill(150), 48000);
  const posts = [];
  publisher.request = async (path, body) => posts.push({ path, body });
  publisher.queue = [
    { direction: 'tester_to_target', turnPair: 0 },
    { direction: 'target_to_tester', turnPair: 1 },
  ];
  await publisher.scheduleFlush(true);
  assert.equal(posts.length, 1);
  assert.deepEqual(Array.from(posts[0].body.listener_media_keys), ['session:1:target_to_tester']);
  const sent = Buffer.from(posts[0].body.pcm16_base64, 'base64');
  const recorded = publisher.callCapture.pcm();
  assert.deepEqual(sent, Buffer.from(recorded.buffer));
  assert.ok(recorded.some((sample) => sample === 200));
});

test('first caller calibrates at the current noise cursor after greeting playback', async () => {
  const noise = new Int16Array(2400);
  [10, 10, 200, 20, 1000].forEach((level, index) => noise.fill(level, index * 480, (index + 1) * 480));
  let frames = 0;
  const mic = new ContinuousMicrophone({ noise, snrDb: 10, send: () => { frames += 1; }, observe: () => {} });
  mic.start();
  try {
    const deadline = Date.now() + 2000;
    while (frames < 3 && Date.now() < deadline) await sleep(2);
    assert.ok(frames >= 3);
    const clean = new Int16Array(960).fill(1000);
    const sent = await mic.play(clean, 1);
    const power = sent.reduce((sum, sample) => sum + (sample - 1000) ** 2, 0) / sent.length;
    assert.ok(Math.abs(10 * Math.log10(1000000 / power) - 10) < .05);
    assert.ok(mic.calibration.lead_in_samples >= 1440);
    const gain = mic.noiseGain;
    await mic.play(new Int16Array(480).fill(100), 2);
    assert.equal(mic.noiseGain, gain);
  } finally { await mic.close(); }
});
