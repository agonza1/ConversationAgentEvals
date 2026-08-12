#!/usr/bin/env node
import crypto, { webcrypto } from 'node:crypto';
import { execFile } from 'node:child_process';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import process from 'node:process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { SignalWire } = require('@signalwire/js');
const wrtc = require('@roamhq/wrtc');
const WebSocket = require('ws');

const execFileAsync = promisify(execFile);
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_TARGET_URL = 'https://holyguacamole.signalwire.me/';
const RESULT_SCHEMA_VERSION = 'signalwire-holyguacamole-smoke-result-v2';
const OUTBOUND_SAMPLE_RATE = 48000;
const FRAME_DURATION_MS = 10;
const FRAME_SAMPLE_COUNT = OUTBOUND_SAMPLE_RATE * FRAME_DURATION_MS / 1000;
const AUDIBLE_PEAK = 32;
const PRE_RESPONSE_SILENCE_MS = 700;
const POST_CALLER_GRACE_MS = 300;
// Remote agents can insert a natural pause between clauses. Keep a conservative
// end boundary so ASR receives the complete utterance instead of the first clause.
const RESPONSE_END_SILENCE_MS = 1800;
const RESPONSE_MIN_CAPTURE_MS = 3500;
const RESPONSE_MAX_CAPTURE_MS = 9000;

if (!globalThis.crypto) globalThis.crypto = webcrypto;

function parseArgs(argv) {
  const args = {
    targetUrl: process.env.SIGNALWIRE_HOLYGUACAMOLE_TARGET_URL || DEFAULT_TARGET_URL,
    callerText: process.env.SIGNALWIRE_HOLYGUACAMOLE_CALLER_TEXT || 'I would like one chicken taco and a small drink.',
    callerAudio: process.env.SIGNALWIRE_HOLYGUACAMOLE_CALLER_AUDIO || '',
    artifactRoot: process.env.SIGNALWIRE_HOLYGUACAMOLE_ARTIFACT_ROOT || 'artifacts/signalwire-holyguacamole-smoke',
    timeoutMs: Number(process.env.SIGNALWIRE_HOLYGUACAMOLE_TIMEOUT_MS || 60000),
    jsonOnly: false,
    livePublishBaseUrl: (process.env.PIPECAT_SERVICE_URL || '').replace(/\/$/, ''),
    livePublishToken: process.env.REFERENCE_AGENT_INTERNAL_TOKEN || '',
    livePublishExecutionRunId: process.env.SIGNALWIRE_HOLYGUACAMOLE_EXECUTION_RUN_ID || '',
    livePublishSessionId: process.env.SIGNALWIRE_HOLYGUACAMOLE_CONVERSATION_ID || '',
    livePublishPublisherId: process.env.SIGNALWIRE_HOLYGUACAMOLE_LIVE_PUBLISHER_ID || '',
    maxExchanges: Number(process.env.SIGNALWIRE_HOLYGUACAMOLE_MAX_EXCHANGES || 1),
    scenario: JSON.parse(process.env.SIGNALWIRE_HOLYGUACAMOLE_SCENARIO_JSON || '{}'),
    testerModelName: process.env.SIGNALWIRE_HOLYGUACAMOLE_TESTER_MODEL_NAME || null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--target-url') args.targetUrl = requireValue(argv, ++index, value);
    else if (value === '--caller-text') args.callerText = requireValue(argv, ++index, value);
    else if (value === '--caller-audio') args.callerAudio = requireValue(argv, ++index, value);
    else if (value === '--artifact-root') args.artifactRoot = requireValue(argv, ++index, value);
    else if (value === '--timeout-ms') args.timeoutMs = Number(requireValue(argv, ++index, value));
    else if (value === '--max-exchanges') args.maxExchanges = Number(requireValue(argv, ++index, value));
    else if (value === '--json-only') args.jsonOnly = true;
    else if (value === '--help' || value === '-h') {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown option: ${value}`);
    }
  }
  args.targetUrl = normalizeAllowlistedTargetUrl(args.targetUrl);
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs < 10000) {
    throw new Error('--timeout-ms must be a number >= 10000.');
  }
  if (!Number.isInteger(args.maxExchanges) || args.maxExchanges < 1 || args.maxExchanges > 2) {
    throw new Error('--max-exchanges must be 1 or 2.');
  }
  if (process.env.SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC !== '1') {
    throw new Error('Set SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC=1 to run the public SignalWire smoke.');
  }
  return args;
}

function requireValue(argv, index, flag) {
  const value = argv[index];
  if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value.`);
  return value;
}

function normalizeAllowlistedTargetUrl(value) {
  const url = new URL(value);
  const expected = new URL(DEFAULT_TARGET_URL);
  if (url.href !== expected.href) {
    throw new Error(`Holy Guacamole smoke is allowlisted to ${DEFAULT_TARGET_URL}.`);
  }
  return expected.href;
}

function printHelp() {
  console.log(`Run a direct server-side WebRTC smoke against Holy Guacamole.

Usage:
  SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC=1 node scripts/signalwire_holyguacamole_smoke.mjs [options]

Options:
  --target-url <url>       Fixed target URL. Default: ${DEFAULT_TARGET_URL}
  --caller-text <text>     Caller utterance to synthesize/play.
  --caller-audio <path>    PCM WAV audio file to send as the WebRTC microphone.
  --artifact-root <path>   Artifact directory.
  --timeout-ms <ms>        Overall wait budget. Default: 60000
  --max-exchanges <1|2>    Exchanges in the same call. Default: 1
  --json-only              Print only the machine-readable summary.
`);
}

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const nowIso = () => new Date().toISOString();
const relativeToRepo = (value) => path.relative(REPO_ROOT, value);

function redact(value) {
  return String(value)
    .replace(/token["'=:\s]+[^"',\s}]+/gi, 'token=<redacted>')
    .replace(/bearer\s+[A-Za-z0-9._-]+/gi, 'Bearer <redacted>');
}

class MemoryStorage {
  constructor() {
    this.scopes = new Map();
  }
  scope(name) {
    if (!this.scopes.has(name)) this.scopes.set(name, new Map());
    return this.scopes.get(name);
  }
  async getItem(key, scope = 'session') { return this.scope(scope).get(key) ?? null; }
  async setItem(key, value, scope = 'session') { this.scope(scope).set(key, value); }
  async removeItem(key, scope = 'session') { this.scope(scope).delete(key); }
  async clear(scope = 'session') { this.scope(scope).clear(); }
}

class CaeLiveAudioPublisher {
  constructor(args) {
    this.baseUrl = args.livePublishBaseUrl;
    this.token = args.livePublishToken;
    this.executionRunId = args.livePublishExecutionRunId;
    this.sessionId = args.livePublishSessionId || `${this.executionRunId}:signalwire-webrtc`;
    this.publisherId = args.livePublishPublisherId || null;
    this.queue = [];
    this.flushTimer = null;
    this.pending = Promise.resolve();
    this.error = null;
  }
  get configured() { return Boolean(this.baseUrl && this.token && this.executionRunId); }
  async request(pathname, body) {
    const response = await fetch(`${this.baseUrl}${pathname}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-cae-reference-token': this.token },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`CAE live audio publisher returned HTTP ${response.status}.`);
    return response.json();
  }
  async open() {
    if (!this.configured) return false;
    if (this.publisherId) return true;
    try {
      const opened = await this.request('/outbound-voice/broadcast/open', {
        execution_run_id: this.executionRunId,
        session_id: this.sessionId,
      });
      this.publisherId = opened.publisher_id;
      return Boolean(this.publisherId);
    } catch (error) {
      this.error = redact(error instanceof Error ? error.message : String(error));
      return false;
    }
  }
  enqueue(frame) {
    if (!this.publisherId || !frame?.pcm16Base64) return;
    this.queue.push(frame);
    if (this.flushTimer === null) {
      this.flushTimer = setTimeout(() => {
        this.flushTimer = null;
        this.scheduleFlush();
      }, 90);
    }
  }
  scheduleFlush() {
    if (!this.queue.length || !this.publisherId) return this.pending;
    const queued = this.queue.splice(0);
    this.pending = this.pending.then(async () => {
      const batches = [];
      for (const frame of queued) {
        const previous = batches.at(-1);
        if (previous && previous.direction === frame.direction
          && previous.sampleRate === frame.sampleRate && previous.turnPair === frame.turnPair) {
          previous.buffers.push(Buffer.from(frame.pcm16Base64, 'base64'));
        } else {
          batches.push({
            direction: frame.direction,
            sampleRate: frame.sampleRate,
            turnPair: Number(frame.turnPair || 1),
            buffers: [Buffer.from(frame.pcm16Base64, 'base64')],
          });
        }
      }
      for (const batch of batches) {
        const audio = Buffer.concat(batch.buffers);
        if (!audio.length) continue;
        await this.request('/outbound-voice/broadcast/audio', {
          execution_run_id: this.executionRunId,
          publisher_id: this.publisherId,
          direction: batch.direction,
          turn_pair: batch.turnPair,
          sample_rate: batch.sampleRate,
          channels: 1,
          pcm16_base64: audio.toString('base64'),
        });
      }
    }).catch((error) => {
      this.error = redact(error instanceof Error ? error.message : String(error));
    });
    return this.pending;
  }
  async close() {
    if (this.flushTimer !== null) clearTimeout(this.flushTimer);
    this.flushTimer = null;
    await this.scheduleFlush();
    await this.pending;
    if (!this.publisherId) return;
    const publisherId = this.publisherId;
    this.publisherId = null;
    try {
      await this.request('/outbound-voice/broadcast/close', {
        execution_run_id: this.executionRunId,
        publisher_id: publisherId,
      });
    } catch (error) {
      this.error = redact(error instanceof Error ? error.message : String(error));
    }
  }
}

function baseResult(args, startedAt) {
  return {
    schema_version: RESULT_SCHEMA_VERSION,
    status: 'running',
    reason_code: null,
    reason: null,
    target: {
      id: 'holyguacamole-signalwire-agent',
      url: DEFAULT_TARGET_URL,
      kind: 'signalwire_holy_guacamole',
      execution: 'real_external_public_target',
    },
    tester: {
      id: 'cae_signalwire_webrtc_tester',
      executor_id: 'signalwire_public_webrtc',
      transport_runtime: '@signalwire/js + @roamhq/wrtc',
      media_source: 'current_run_tts_or_supplied_audio',
    },
    provenance: {
      cae_path: 'scripts/signalwire_holyguacamole_smoke.mjs',
      fixture_backed: false,
      mock_execution: false,
      live_external_connection: true,
      saved_replay: false,
      public_execution_gate: 'SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC',
      tokens_redacted: true,
      guest_token_persisted: false,
      headless_browser: false,
    },
    timestamps: { started_at: startedAt, completed_at: null },
    connection: {
      token_endpoint_seen: false,
      token_status: null,
      sdk_connected: false,
      call_connected: false,
      remote_stream_seen: false,
      remote_audio_sample_seen: false,
      caller_audio_played: false,
      caller_audio_completed: false,
      remote_audio_after_caller_seen: false,
      terminal_status: null,
    },
    latency_metrics: {
      token_fetch_ms: null,
      token_to_sdk_connected_ms: null,
      sdk_connected_to_call_connected_ms: null,
      call_connected_to_remote_track_ms: null,
      caller_audio_completed_to_remote_audio_ms: null,
      total_run_ms: null,
    },
    transcript: {
      text: '',
      caller_text: '',
      caller_text_verified: false,
      caller_text_source: 'unverified',
      agent_text: '',
      source: 'remote_audio_capture_untranscribed',
      artifact_path: null,
      complete_as_observed: false,
      agent_text_available: false,
      untranscribed_target_audio: true,
    },
    media: { target_audio_duration_ms: null, target_audio_bytes: 0 },
    network_events: [],
    console_events: [],
    call_events: [],
    exchanges: [],
    artifacts: {},
  };
}

function parsePcmWav(buffer) {
  if (buffer.length < 44 || buffer.subarray(0, 4).toString('ascii') !== 'RIFF'
    || buffer.subarray(8, 12).toString('ascii') !== 'WAVE') {
    throw new Error('Caller audio must be a RIFF/WAVE PCM file.');
  }
  let offset = 12;
  let format = null;
  let pcm = null;
  while (offset + 8 <= buffer.length) {
    const id = buffer.subarray(offset, offset + 4).toString('ascii');
    const size = buffer.readUInt32LE(offset + 4);
    const start = offset + 8;
    const end = Math.min(buffer.length, start + size);
    if (id === 'fmt ' && size >= 16) {
      format = {
        audioFormat: buffer.readUInt16LE(start),
        channels: buffer.readUInt16LE(start + 2),
        sampleRate: buffer.readUInt32LE(start + 4),
        bitsPerSample: buffer.readUInt16LE(start + 14),
      };
    } else if (id === 'data') {
      pcm = buffer.subarray(start, end);
    }
    offset = start + size + (size % 2);
  }
  if (!format || !pcm || format.audioFormat !== 1 || format.bitsPerSample !== 16) {
    throw new Error('Caller audio must use 16-bit integer PCM WAV encoding.');
  }
  const frameCount = Math.floor(pcm.length / 2 / format.channels);
  const mono = new Int16Array(frameCount);
  for (let frame = 0; frame < frameCount; frame += 1) {
    let sum = 0;
    for (let channel = 0; channel < format.channels; channel += 1) {
      sum += pcm.readInt16LE((frame * format.channels + channel) * 2);
    }
    mono[frame] = Math.round(sum / format.channels);
  }
  return { samples: resamplePcm16(mono, format.sampleRate, OUTBOUND_SAMPLE_RATE), sampleRate: OUTBOUND_SAMPLE_RATE };
}

function resamplePcm16(samples, sourceRate, targetRate) {
  if (sourceRate === targetRate) return samples;
  const length = Math.max(1, Math.round(samples.length * targetRate / sourceRate));
  const output = new Int16Array(length);
  const scale = sourceRate / targetRate;
  for (let index = 0; index < length; index += 1) {
    const position = index * scale;
    const left = Math.min(samples.length - 1, Math.floor(position));
    const right = Math.min(samples.length - 1, left + 1);
    const fraction = position - left;
    output[index] = Math.round(samples[left] * (1 - fraction) + samples[right] * fraction);
  }
  return output;
}

function downmixPcm16(samples, channels) {
  if (channels <= 1) return Int16Array.from(samples);
  const mono = new Int16Array(Math.floor(samples.length / channels));
  for (let frame = 0; frame < mono.length; frame += 1) {
    let sum = 0;
    for (let channel = 0; channel < channels; channel += 1) sum += samples[frame * channels + channel];
    mono[frame] = Math.round(sum / channels);
  }
  return mono;
}

function pcm16ToBuffer(samples) {
  return Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength);
}

function createPcmWav(chunks, sampleRate) {
  const pcm = Buffer.concat(chunks);
  const wav = Buffer.alloc(44 + pcm.length);
  wav.write('RIFF', 0, 'ascii');
  wav.writeUInt32LE(36 + pcm.length, 4);
  wav.write('WAVE', 8, 'ascii');
  wav.write('fmt ', 12, 'ascii');
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24);
  wav.writeUInt32LE(sampleRate * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write('data', 36, 'ascii');
  wav.writeUInt32LE(pcm.length, 40);
  pcm.copy(wav, 44);
  return wav;
}

function waitForObservable(observable, predicate, timeoutMs, label) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let subscription;
    const done = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      setTimeout(() => subscription?.unsubscribe(), 0);
      callback(value);
    };
    const timer = setTimeout(() => done(reject, new Error(`Timed out waiting for ${label}.`)), timeoutMs);
    subscription = observable.subscribe({
      next: (value) => { if (predicate(value)) done(resolve, value); },
      error: (error) => done(reject, error),
      complete: () => done(reject, new Error(`${label} completed before a matching value.`)),
    });
  });
}

async function waitUntil(predicate, timeoutMs, label, intervalMs = 25) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = predicate();
    if (value) return value;
    await sleep(intervalMs);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

class RemoteAudioMonitor {
  constructor(livePublisher) {
    this.livePublisher = livePublisher;
    this.sink = null;
    this.sampleRate = null;
    this.firstFrameAt = null;
    this.firstAudibleAt = null;
    this.lastAudibleAt = null;
    this.silentSince = Date.now();
    this.turnPair = 1;
    this.capture = null;
  }
  attach(track) {
    if (this.sink) return;
    this.sink = new wrtc.nonstandard.RTCAudioSink(track);
    this.sink.ondata = (data) => this.onData(data);
  }
  onData(data) {
    const receivedAt = Date.now();
    const samples = downmixPcm16(data.samples, data.channelCount || 1);
    const sampleRate = data.sampleRate || OUTBOUND_SAMPLE_RATE;
    if (!this.firstFrameAt) this.firstFrameAt = receivedAt;
    this.sampleRate = sampleRate;
    const pcm = pcm16ToBuffer(samples);
    this.livePublisher.enqueue({
      direction: 'target_to_tester',
      turnPair: this.turnPair,
      sampleRate,
      pcm16Base64: pcm.toString('base64'),
    });
    let peak = 0;
    for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
    const audible = peak >= AUDIBLE_PEAK;
    if (audible) {
      if (!this.firstAudibleAt) this.firstAudibleAt = receivedAt;
      this.lastAudibleAt = receivedAt;
      this.silentSince = null;
    } else if (this.silentSince === null) {
      this.silentSince = receivedAt;
    }
    const capture = this.capture;
    if (!capture) return;
    if (!capture.onsetAt) {
      const enoughSilence = capture.silenceSeenAt !== null
        || (this.silentSince !== null && receivedAt - this.silentSince >= PRE_RESPONSE_SILENCE_MS);
      if (enoughSilence) capture.silenceSeenAt ??= this.silentSince;
      if (audible && receivedAt >= capture.minOnsetAt && enoughSilence) {
        capture.onsetAt = receivedAt;
        capture.lastAudibleAt = receivedAt;
        capture.chunks.push(pcm);
      }
      return;
    }
    capture.chunks.push(pcm);
    if (audible) capture.lastAudibleAt = receivedAt;
  }
  async waitForGreetingBoundary(timeoutMs) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
      if (this.firstAudibleAt && this.silentSince && Date.now() - this.silentSince >= PRE_RESPONSE_SILENCE_MS) return;
      if (!this.firstAudibleAt && this.firstFrameAt && Date.now() - this.firstFrameAt >= 1500) return;
      await sleep(50);
    }
  }
  arm(turnPair) {
    this.turnPair = turnPair;
    this.capture = {
      turnPair,
      minOnsetAt: Infinity,
      silenceSeenAt: this.silentSince && Date.now() - this.silentSince >= PRE_RESPONSE_SILENCE_MS
        ? this.silentSince : null,
      onsetAt: null,
      lastAudibleAt: null,
      chunks: [],
    };
    return this.capture;
  }
  async finish(capture, callerEndedAt, timeoutMs) {
    capture.minOnsetAt = callerEndedAt + POST_CALLER_GRACE_MS;
    await waitUntil(() => capture.onsetAt, Math.min(timeoutMs, 12000), `target response onset for exchange ${capture.turnPair}`);
    const captureDeadline = Math.min(Date.now() + RESPONSE_MAX_CAPTURE_MS, callerEndedAt + timeoutMs);
    while (Date.now() < captureDeadline) {
      const elapsed = Date.now() - capture.onsetAt;
      const silence = capture.lastAudibleAt ? Date.now() - capture.lastAudibleAt : 0;
      if (elapsed >= RESPONSE_MIN_CAPTURE_MS && silence >= RESPONSE_END_SILENCE_MS) break;
      await sleep(50);
    }
    this.capture = null;
    if (!capture.chunks.length || !this.sampleRate) throw new Error('Target response contained no PCM audio.');
    const buffer = createPcmWav(capture.chunks, this.sampleRate);
    return {
      buffer,
      durationMs: Math.round((buffer.length - 44) / 2 * 1000 / this.sampleRate),
      onsetAt: capture.onsetAt,
      silenceBoundaryAt: capture.silenceSeenAt,
    };
  }
  stop() {
    if (this.sink) this.sink.stop();
    this.sink = null;
  }
}

async function playPcm(source, wavBuffer, turnPair, livePublisher) {
  const { samples } = parsePcmWav(wavBuffer);
  const playback = {
    source: 'server_side_pcm_track',
    turn_pair: turnPair,
    readiness_trigger: 'signalwire_call_connected_remote_audio_track',
    started_epoch_ms: Date.now(),
    first_outbound_sample_epoch_ms: null,
    ended_epoch_ms: null,
    ended: false,
    sample_rate: OUTBOUND_SAMPLE_RATE,
  };
  for (let offset = 0; offset < samples.length; offset += FRAME_SAMPLE_COUNT) {
    const frame = new Int16Array(FRAME_SAMPLE_COUNT);
    frame.set(samples.subarray(offset, offset + FRAME_SAMPLE_COUNT));
    const frameBuffer = pcm16ToBuffer(frame);
    const frameStart = Date.now();
    source.onData({
      samples: frame,
      sampleRate: OUTBOUND_SAMPLE_RATE,
      bitsPerSample: 16,
      channelCount: 1,
      numberOfFrames: FRAME_SAMPLE_COUNT,
    });
    playback.first_outbound_sample_epoch_ms ??= frameStart;
    livePublisher.enqueue({
      direction: 'tester_to_target',
      turnPair,
      sampleRate: OUTBOUND_SAMPLE_RATE,
      pcm16Base64: frameBuffer.toString('base64'),
    });
    await sleep(Math.max(0, FRAME_DURATION_MS - (Date.now() - frameStart)));
  }
  playback.ended_epoch_ms = Date.now();
  playback.ended = true;
  playback.duration_ms = playback.ended_epoch_ms - playback.started_epoch_ms;
  return playback;
}

async function generateTesterFollowup(args, targetAudioWavBase64, history, turnPair, timeoutMs) {
  if (!args.livePublishBaseUrl || !args.livePublishToken) {
    throw new Error('Multi-exchange SignalWire execution requires the existing Pipecat tester runtime.');
  }
  const goal = String(args.scenario?.goal || args.callerText).trim();
  const persona = String(args.scenario?.persona || 'the original caller').trim();
  const response = await fetch(`${args.livePublishBaseUrl}/reference-tester/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-cae-reference-token': args.livePublishToken },
    body: JSON.stringify({
      scenario_instruction: `${args.scenario?.id || 'signalwire-holyguacamole'}: ${goal}`,
      act_id: `caller-follow-up-${turnPair}`,
      act_objective: `Respond naturally as ${persona} and move the conversation toward this caller goal: ${goal}`,
      example_utterance: args.callerText,
      history,
      target_audio_wav_base64: targetAudioWavBase64,
      model_name: args.testerModelName,
    }),
    signal: AbortSignal.timeout(Math.max(1, timeoutMs)),
  });
  if (!response.ok) throw new Error(`Existing Pipecat tester turn failed with HTTP ${response.status}.`);
  const payload = await response.json();
  const testerText = String(payload.tester_text || '').trim();
  const testerAudioWavBase64 = String(payload.tester_audio_wav_base64 || '').trim();
  const targetText = String(payload.tester_asr_receipt || '').trim();
  if (!testerText || !testerAudioWavBase64 || !targetText) {
    throw new Error('Existing Pipecat tester turn returned incomplete text or audio evidence.');
  }
  return { testerText, testerAudioWavBase64, targetText };
}

async function synthesizeCallerAudio(args, runDir) {
  if (args.callerAudio) {
    const source = path.resolve(REPO_ROOT, args.callerAudio);
    const sourceStat = await fs.stat(source);
    if (!sourceStat.isFile() || sourceStat.size <= 0) throw new Error(`Caller audio file is empty: ${args.callerAudio}`);
    const target = path.join(runDir, `caller-audio${path.extname(source)}`);
    await fs.copyFile(source, target);
    return { path: target, source: 'supplied_audio_file', callerTextVerified: false };
  }
  const target = path.join(runDir, 'caller-audio.wav');
  if (process.env.KOKORO_BASE_URL) {
    const response = await fetch(`${process.env.KOKORO_BASE_URL.replace(/\/$/, '')}/v1/audio/speech`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: process.env.KOKORO_MODEL || 'kokoro',
        voice: process.env.KOKORO_TESTER_VOICE || 'af_heart',
        input: args.callerText,
        response_format: 'wav',
      }),
    });
    if (!response.ok) throw new Error(`Kokoro returned HTTP ${response.status}`);
    await fs.writeFile(target, Buffer.from(await response.arrayBuffer()));
    return { path: target, source: 'kokoro_tts', callerTextVerified: true };
  }
  if (process.platform === 'darwin') {
    await execFileAsync('/usr/bin/say', ['-o', target, '--file-format=WAVE', '--data-format=LEI16@16000', args.callerText]);
    return { path: target, source: 'macos_say_tts', callerTextVerified: true };
  }
  throw new Error('Caller audio synthesis unavailable. Provide --caller-audio or configure KOKORO_BASE_URL.');
}

function combineResponseWavs(responseAudios) {
  if (!responseAudios.length) return null;
  if (responseAudios.length === 1) return { ...responseAudios[0], mimeType: 'audio/wav', extension: 'target-audio.wav' };
  const parsed = responseAudios.map((item) => parsePcmWav(item.buffer));
  const pcmChunks = parsed.map((item) => pcm16ToBuffer(item.samples));
  return {
    buffer: createPcmWav(pcmChunks, OUTBOUND_SAMPLE_RATE),
    mimeType: 'audio/wav',
    extension: 'target-audio.wav',
    durationMs: responseAudios.reduce((total, item) => total + item.durationMs, 0),
  };
}

async function writeArtifacts(result, runDir, responseAudios) {
  const transcriptPath = path.join(runDir, 'transcript.txt');
  await fs.writeFile(transcriptPath, `${result.transcript.text || ''}\n`, 'utf8');
  result.transcript.artifact_path = relativeToRepo(transcriptPath);
  const targetAudio = combineResponseWavs(responseAudios);
  if (targetAudio) {
    const audioPath = path.join(runDir, targetAudio.extension);
    await fs.writeFile(audioPath, targetAudio.buffer);
    result.media.target_audio_bytes = targetAudio.buffer.length;
    result.media.target_audio_duration_ms = targetAudio.durationMs;
    result.artifacts.target_audio = relativeToRepo(audioPath);
    result.artifacts.target_audio_mime = targetAudio.mimeType;
    result.artifacts.target_audio_sha256 = crypto.createHash('sha256').update(targetAudio.buffer).digest('hex');
  }
  result.artifacts.target_response_audio_turns = [];
  for (let index = 0; index < responseAudios.length; index += 1) {
    const audio = responseAudios[index];
    const turnPath = path.join(runDir, `target-response-turn-${index + 1}.wav`);
    await fs.writeFile(turnPath, audio.buffer);
    result.artifacts.target_response_audio_turns.push({
      turn_pair: index + 1,
      path: relativeToRepo(turnPath),
      mime_type: 'audio/wav',
      sha256: crypto.createHash('sha256').update(audio.buffer).digest('hex'),
      duration_ms: audio.durationMs,
    });
    if (index === 0) result.artifacts.target_response_audio = relativeToRepo(turnPath);
  }
  if (responseAudios[0]) {
    result.artifacts.target_response_audio_mime = 'audio/wav';
    result.artifacts.target_response_audio_sha256 = crypto.createHash('sha256').update(responseAudios[0].buffer).digest('hex');
    result.media.target_response_audio_bytes = responseAudios[0].buffer.length;
    result.media.target_response_audio_duration_ms = responseAudios[0].durationMs;
  }
  const resultPath = path.join(runDir, 'result.json');
  const latestPath = path.join(path.dirname(runDir), 'latest.json');
  result.artifacts.result_json = relativeToRepo(resultPath);
  result.artifacts.transcript_txt = relativeToRepo(transcriptPath);
  result.artifacts.latest_json = relativeToRepo(latestPath);
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  await fs.writeFile(resultPath, serialized, 'utf8');
  await fs.writeFile(latestPath, serialized, 'utf8');
}

function deriveTranscript(result, args) {
  const verified = result.tester.caller_text_verified === true;
  result.transcript.caller_text = verified ? args.callerText : '';
  result.transcript.caller_text_verified = verified;
  result.transcript.caller_text_source = verified ? result.tester.media_source : 'unverified_supplied_audio';
  result.transcript.text = verified ? `Caller: ${args.callerText}` : 'Caller audio: supplied audio file (speech text unverified)';
}

async function fetchGuestCredential(targetUrl, includeVoice, result) {
  const url = new URL(includeVoice ? '/get_token?voice=elevenlabs.adam' : '/get_token', targetUrl);
  const startedAt = Date.now();
  const response = await fetch(url, { signal: AbortSignal.timeout(15000) });
  result.connection.token_endpoint_seen = true;
  result.connection.token_status = response.status;
  result.network_events.push({ kind: 'guest_token', status: response.status, t_ms: Date.now() - startedAt });
  const payload = await response.json();
  const normalized = Array.isArray(payload) ? payload[0] || {} : payload;
  if (!response.ok || normalized.error || !normalized.token || !normalized.address) {
    throw new Error(normalized.error || `SignalWire guest token failed with HTTP ${response.status}.`);
  }
  return { token: normalized.token, address: normalized.address, elapsedMs: Date.now() - startedAt };
}

async function runSmoke(args) {
  const startedAt = nowIso();
  const startedMs = Date.now();
  const deadline = startedMs + args.timeoutMs;
  const remaining = () => Math.max(1, deadline - Date.now());
  const runId = `signalwire-holyguacamole-${startedAt.replace(/[:.-]/g, '')}`;
  const runDir = path.resolve(REPO_ROOT, args.artifactRoot, runId);
  await fs.mkdir(runDir, { recursive: true });
  const result = baseResult(args, startedAt);
  const livePublisher = new CaeLiveAudioPublisher(args);
  const audioSource = new wrtc.nonstandard.RTCAudioSource();
  const audioTrack = audioSource.createTrack();
  const remoteMonitor = new RemoteAudioMonitor(livePublisher);
  const subscriptions = [];
  const responseAudios = [];
  let client = null;
  let call = null;
  try {
    result.connection.cae_live_broadcast_connected = await livePublisher.open();
    const callerAudio = await synthesizeCallerAudio(args, runDir);
    const firstCallerWav = await fs.readFile(callerAudio.path);
    parsePcmWav(firstCallerWav);
    result.tester.media_source = callerAudio.source;
    result.tester.caller_text_verified = callerAudio.callerTextVerified === true;
    result.artifacts.caller_audio = relativeToRepo(callerAudio.path);

    const firstCredential = await fetchGuestCredential(args.targetUrl, true, result);
    result.latency_metrics.token_fetch_ms = firstCredential.elapsedMs;
    let usedInitialToken = false;
    const credentialProvider = {
      authenticate: async () => {
        if (!usedInitialToken) {
          usedInitialToken = true;
          return { token: firstCredential.token };
        }
        return { token: (await fetchGuestCredential(args.targetUrl, false, result)).token };
      },
    };
    const mediaDevices = {
      getUserMedia: async (constraints) => constraints?.audio
        ? new wrtc.MediaStream([audioTrack]) : new wrtc.MediaStream(),
      enumerateDevices: async () => [],
      getSupportedConstraints: () => ({}),
      addEventListener: () => {},
      removeEventListener: () => {},
    };
    const clientStartedAt = Date.now();
    client = new SignalWire(credentialProvider, {
      webRTCApiProvider: { RTCPeerConnection: wrtc.RTCPeerConnection, mediaDevices },
      webSocketConstructor: WebSocket,
      storageImplementation: new MemoryStorage(),
      skipDeviceMonitoring: true,
      persistSession: false,
      logLevel: 'error',
    });
    subscriptions.push(client.errors$.subscribe((error) => {
      result.console_events.push({ kind: 'client_error', text: redact(error?.message || error), t_ms: Date.now() - startedMs });
    }));
    subscriptions.push(client.warnings$.subscribe((warning) => {
      result.console_events.push({ kind: 'client_warning', text: redact(warning?.message || warning), t_ms: Date.now() - startedMs });
    }));
    await waitForObservable(client.isConnected$, Boolean, Math.min(15000, remaining()), 'SignalWire SDK connection');
    result.connection.sdk_connected = true;
    result.latency_metrics.token_to_sdk_connected_ms = Date.now() - clientStartedAt;

    const dialStartedAt = Date.now();
    call = await client.dial(firstCredential.address, {
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: false,
      receiveAudio: true,
      receiveVideo: false,
      userVariables: {
        userName: 'CAE Voice Tester',
        interface: 'cae-direct-webrtc',
        timestamp: nowIso(),
        extension: 'holy_guacamole',
      },
    });
    subscriptions.push(call.status$.subscribe((status) => {
      result.connection.terminal_status = status;
      result.call_events.push({ kind: 'status', status, t_ms: Date.now() - startedMs });
    }));
    subscriptions.push(call.subscribe('user_event').subscribe((event) => {
      result.call_events.push({ kind: 'user_event', event: event?.params ?? event, t_ms: Date.now() - startedMs });
    }));
    await waitForObservable(call.status$, (status) => status === 'connected', Math.min(20000, remaining()), 'SignalWire call connection');
    result.connection.call_connected = true;
    result.latency_metrics.sdk_connected_to_call_connected_ms = Date.now() - dialStartedAt;
    const remoteStream = await waitForObservable(
      call.remoteStream$,
      (stream) => Boolean(stream?.getAudioTracks?.().length),
      Math.min(15000, remaining()),
      'remote SignalWire audio track',
    );
    remoteMonitor.attach(remoteStream.getAudioTracks()[0]);
    result.connection.remote_stream_seen = true;
    result.latency_metrics.call_connected_to_remote_track_ms = Date.now() - dialStartedAt;
    await waitUntil(() => remoteMonitor.firstFrameAt, Math.min(5000, remaining()), 'first remote PCM frame');
    result.connection.remote_audio_sample_seen = true;
    await remoteMonitor.waitForGreetingBoundary(Math.min(7000, remaining()));

    let callerText = args.callerText;
    let callerWav = firstCallerWav;
    let priorTargetText = '';
    for (let turnPair = 1; turnPair <= args.maxExchanges; turnPair += 1) {
      const capture = remoteMonitor.arm(turnPair);
      const playback = await playPcm(audioSource, callerWav, turnPair, livePublisher);
      if (turnPair === 1) result.tester.caller_audio_playback = playback;
      else result.tester[`caller_audio_playback_turn_${turnPair}`] = playback;
      result.connection.caller_audio_played = true;
      result.connection.caller_audio_completed = true;
      const response = await remoteMonitor.finish(capture, playback.ended_epoch_ms, remaining());
      responseAudios.push(response);
      const latencyMs = response.onsetAt - playback.ended_epoch_ms;
      result.connection.remote_audio_after_caller_seen = true;
      if (turnPair === 1) result.latency_metrics.caller_audio_completed_to_remote_audio_ms = latencyMs;
      else result.latency_metrics[`exchange_${turnPair}_caller_audio_completed_to_remote_audio_ms`] = latencyMs;
      result.exchanges.push({
        turn_pair: turnPair,
        caller_text: callerText,
        caller_audio_source: turnPair === 1 ? result.artifacts.caller_audio : 'reference-tester/turn',
        ...(turnPair > 1 ? { caller_audio_wav_base64: callerWav.toString('base64') } : {}),
        agent_text: '',
        target_response_audio_turn: turnPair,
        target_response_latency_ms: latencyMs,
      });
      if (turnPair < args.maxExchanges) {
        const followup = await generateTesterFollowup(
          args,
          response.buffer.toString('base64'),
          [{ speaker: 'Caller', text: callerText }, ...(priorTargetText ? [{ speaker: 'Agent', text: priorTargetText }] : [])],
          turnPair + 1,
          remaining(),
        );
        result.exchanges[turnPair - 1].agent_text = followup.targetText;
        priorTargetText = followup.targetText;
        callerText = followup.testerText;
        callerWav = Buffer.from(followup.testerAudioWavBase64, 'base64');
      }
    }
    result.exchanges = result.exchanges.filter(Boolean);
    deriveTranscript(result, args);
    result.status = 'pass';
    result.reason = `Holy Guacamole connected through direct server-side SignalWire WebRTC and completed ${args.maxExchanges} grounded exchange${args.maxExchanges === 1 ? '' : 's'}.`;
  } catch (error) {
    result.status = 'blocked';
    result.reason_code = classifyError(error);
    result.reason = redact(error instanceof Error ? error.message : String(error));
    deriveTranscript(result, args);
  } finally {
    result.timestamps.completed_at = nowIso();
    result.latency_metrics.total_run_ms = Date.now() - startedMs;
    try { if (call) await call.hangup(); } catch {}
    try { if (client) await client.disconnect(); } catch {}
    subscriptions.forEach((subscription) => subscription.unsubscribe());
    remoteMonitor.stop();
    audioTrack.stop();
    await livePublisher.close();
    if (livePublisher.error) result.connection.cae_live_broadcast_error = livePublisher.error;
    await writeArtifacts(result, runDir, responseAudios);
  }
  return { ...result, artifact_path: result.artifacts.result_json };
}

function classifyError(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (/timeout/i.test(message)) return 'connection_timeout';
  if (/ECONN|ENOTFOUND|fetch|network/i.test(message)) return 'target_unreachable';
  if (/Kokoro|say|Caller audio synthesis|caller audio/i.test(message)) return 'tester_audio_synthesis_failed';
  if (/WebRTC|ICE|SDP|peer connection/i.test(message)) return 'signalwire_webrtc_failed';
  return 'unsupported_signalwire_media_path';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const result = await runSmoke(args);
  const resultPath = path.resolve(REPO_ROOT, result.artifact_path || result.artifacts.result_json);
  const summary = {
    status: result.status,
    reason_code: result.reason_code,
    reason: result.reason,
    result_path: relativeToRepo(resultPath),
    transcript_path: result.transcript.artifact_path,
    target_audio_path: result.artifacts.target_audio || null,
    caller_audio_playback: result.tester.caller_audio_playback || null,
    latency_metrics: result.latency_metrics,
    connection: result.connection,
  };
  console.log(JSON.stringify(summary, null, args.jsonOnly ? 0 : 2));
  // Native WebRTC may retain background handles after every call and track has
  // been closed. This is a bounded CLI process, so terminate deterministically.
  process.exit(result.status === 'pass' ? 0 : 2);
}

main().catch((error) => {
  console.error(redact(error instanceof Error ? error.message : String(error)));
  process.exit(1);
});
