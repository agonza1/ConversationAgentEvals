import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ContinuousMicrophone, CallAudioCapture } from './continuous_microphone.mjs';

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
