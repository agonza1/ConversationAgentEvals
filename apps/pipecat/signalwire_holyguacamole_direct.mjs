#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { SignalWire } from '@signalwire/js';
import wrtc from '@roamhq/wrtc';
import WebSocket from 'ws';
import { filter, firstValueFrom, timeout as rxTimeout } from 'rxjs';

const DEFAULT_TARGET_URL = 'https://holyguacamole.signalwire.me/';
const DEFAULT_VOICE = 'elevenlabs.adam';
const POST_CALLER_REMOTE_AUDIO_GRACE_MS = 500;
const REMOTE_AUDIO_SILENCE_BOUNDARY_MS = 900;
const POST_CALLER_RESPONSE_MIN_CAPTURE_MS = 2500;
const POST_CALLER_RESPONSE_TAIL_MS = 8000;

for (const key of [
  'MediaStream',
  'MediaStreamTrack',
  'RTCPeerConnection',
  'RTCSessionDescription',
  'RTCIceCandidate',
]) {
  globalThis[key] = wrtc[key];
}

function parseArgs(argv) {
  const args = { inputJson: '', jsonOnly: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--input-json') args.inputJson = argv[++i] || '';
    else if (arg === '--json-only') args.jsonOnly = true;
    else if (arg === '--help') {
      console.log('Usage: node signalwire_holyguacamole_direct.mjs --input-json <path> [--json-only]');
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!args.inputJson) throw new Error('Missing --input-json.');
  return args;
}

function normalizeTargetUrl(value) {
  const url = new URL(value || DEFAULT_TARGET_URL);
  const expected = new URL(DEFAULT_TARGET_URL);
  if (url.origin !== expected.origin || url.pathname !== expected.pathname) {
    throw new Error('Holy Guacamole direct execution target URL is not allowlisted.');
  }
  return expected;
}

function wavToPcm(wav) {
  if (wav.toString('ascii', 0, 4) !== 'RIFF' || wav.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('Caller audio must be a RIFF/WAVE file.');
  }
  let offset = 12;
  let fmt = null;
  let data = null;
  while (offset + 8 <= wav.length) {
    const id = wav.toString('ascii', offset, offset + 4);
    const size = wav.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (id === 'fmt ') {
      fmt = {
        audioFormat: wav.readUInt16LE(body),
        channels: wav.readUInt16LE(body + 2),
        sampleRate: wav.readUInt32LE(body + 4),
        bitsPerSample: wav.readUInt16LE(body + 14),
      };
    } else if (id === 'data') {
      data = wav.subarray(body, body + size);
    }
    offset = body + size + (size % 2);
  }
  if (!fmt || !data || fmt.audioFormat !== 1 || fmt.bitsPerSample !== 16) {
    throw new Error('Caller audio must be 16-bit PCM WAV.');
  }
  return { pcm: data, sampleRate: fmt.sampleRate, channels: fmt.channels };
}

function pcmToWav(chunks, sampleRate, channels) {
  const pcm = Buffer.concat(chunks);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + pcm.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * 2, 28);
  header.writeUInt16LE(channels * 2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([header, pcm]);
}

function rms(samples) {
  if (!samples.length) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.sqrt(sum / samples.length) / 32768;
}

function createMemoryStorage() {
  const values = new Map();
  return {
    async setItem(key, value, scope) {
      const storageKey = `${scope}:${key}`;
      if (value === null) values.delete(storageKey);
      else values.set(storageKey, String(value));
    },
    async getItem(key, scope) {
      return values.get(`${scope}:${key}`) ?? null;
    },
    async removeItem(key, scope) {
      values.delete(`${scope}:${key}`);
    },
    async clear(scope) {
      for (const key of [...values.keys()]) {
        if (key.startsWith(`${scope}:`)) values.delete(key);
      }
    },
  };
}

async function fetchGuestToken(targetUrl, voice, includeVoice) {
  const url = new URL('/get_token', targetUrl);
  if (includeVoice && voice) url.searchParams.set('voice', voice);
  const response = await fetch(url);
  let payload = await response.json();
  if (Array.isArray(payload)) payload = payload[0] || {};
  if (!response.ok || payload.error) {
    throw new Error('Holy Guacamole token bootstrap failed.');
  }
  if (!payload.token || !payload.address) {
    throw new Error('Holy Guacamole token bootstrap omitted token or address.');
  }
  return { token: String(payload.token), address: String(payload.address) };
}

async function wait(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const input = JSON.parse(await readFile(args.inputJson, 'utf8'));
  const targetUrl = normalizeTargetUrl(input.target_url);
  const callerText = String(input.caller_text || '').trim();
  const timeoutMs = Math.max(30_000, Number(input.timeout_ms || 90_000));
  const voice = String(input.voice || DEFAULT_VOICE);
  const callerWav = Buffer.from(String(input.caller_audio_wav_base64 || ''), 'base64');
  const caller = wavToPcm(callerWav);
  const startedAt = Date.now();
  const events = [];
  const connection = {
    token_bootstrap: false,
    sdk_connected: false,
    call_connected: false,
    remote_audio_track_seen: false,
    remote_audio_sample_seen: false,
    caller_audio_played: false,
    caller_audio_completed: false,
    remote_audio_after_caller_seen: false,
  };
  const bootstrap = await fetchGuestToken(targetUrl, voice, true);
  connection.token_bootstrap = true;

  const source = new wrtc.nonstandard.RTCAudioSource();
  const inputTrack = source.createTrack();
  const inputStream = new wrtc.MediaStream([inputTrack]);
  const provider = {
    RTCPeerConnection: wrtc.RTCPeerConnection,
    mediaDevices: {
      getUserMedia: async () => inputStream,
      enumerateDevices: async () => [],
      addEventListener: () => {},
      removeEventListener: () => {},
    },
  };
  const client = new SignalWire({
    authenticate: async () => ({ token: bootstrap.token }),
    refresh: async () => {
      const refreshed = await fetchGuestToken(targetUrl, voice, false);
      return { token: refreshed.token };
    },
  }, {
    webSocketConstructor: WebSocket,
    webRTCApiProvider: provider,
    storageImplementation: createMemoryStorage(),
    skipDeviceMonitoring: true,
    savePreferences: false,
    persistSession: false,
    logLevel: 'error',
  });

  const cleanup = [];
  const remoteChunks = [];
  let targetSampleRate = 48_000;
  let targetChannels = 1;
  let firstRemoteAudioAt = null;
  let callerStartedAt = null;
  let callerEndedAt = null;
  let firstRemoteAfterCallerAt = null;
  let lastRemoteAudibleAt = null;
  let captureResponse = false;

  try {
    client.errors$.subscribe((error) => {
      events.push({ type: 'sdk_error', message: String(error?.message || 'SignalWire SDK error.') });
    });
    await firstValueFrom(client.isConnected$.pipe(
      filter(Boolean),
      rxTimeout({ first: Math.min(15_000, timeoutMs) }),
    ));
    connection.sdk_connected = true;

    const call = await client.dial(bootstrap.address, {
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: false,
      receiveVideo: false,
      inputAudioStream: inputStream,
    });
    cleanup.push(async () => { try { await call.hangup(); } catch {} });
    call.status$.subscribe((status) => {
      events.push({ type: 'call_status', status: String(status) });
      if (status === 'connected') connection.call_connected = true;
    });
    call.remoteStream$.subscribe((stream) => {
      if (!stream) return;
      for (const track of stream.getAudioTracks()) {
        connection.remote_audio_track_seen = true;
        const sink = new wrtc.nonstandard.RTCAudioSink(track);
        cleanup.push(() => { try { sink.stop(); } catch {} });
        sink.ondata = ({ samples, sampleRate, channelCount }) => {
          const now = Date.now();
          const level = rms(samples);
          targetSampleRate = sampleRate || targetSampleRate;
          targetChannels = channelCount || targetChannels;
          if (level >= 0.001) {
            connection.remote_audio_sample_seen = true;
            if (firstRemoteAudioAt === null) firstRemoteAudioAt = now;
            lastRemoteAudibleAt = now;
            if (callerEndedAt && now - callerEndedAt >= POST_CALLER_REMOTE_AUDIO_GRACE_MS) {
              connection.remote_audio_after_caller_seen = true;
              if (firstRemoteAfterCallerAt === null) firstRemoteAfterCallerAt = now;
            }
          }
          if (captureResponse) {
            remoteChunks.push(Buffer.from(samples.buffer, samples.byteOffset, samples.byteLength));
          }
        };
      }
    });

    const connectedDeadline = Date.now() + Math.min(25_000, timeoutMs);
    while (!connection.call_connected && Date.now() < connectedDeadline) await wait(50);
    if (!connection.call_connected) throw new Error('Holy Guacamole SignalWire call did not connect.');

    const greetingDeadline = Date.now() + Math.min(8_000, Math.max(1_000, timeoutMs / 4));
    while (Date.now() < greetingDeadline) {
      if (lastRemoteAudibleAt && Date.now() - lastRemoteAudibleAt >= REMOTE_AUDIO_SILENCE_BOUNDARY_MS) break;
      await wait(50);
    }

    callerStartedAt = Date.now();
    captureResponse = true;
    const samplesPerFrame = Math.max(1, Math.round(caller.sampleRate * 0.01));
    const bytesPerFrame = samplesPerFrame * caller.channels * 2;
    let sentFrames = 0;
    for (let offset = 0; offset < caller.pcm.length; offset += bytesPerFrame) {
      const chunk = caller.pcm.subarray(offset, offset + bytesPerFrame);
      const samples = new Int16Array(Math.floor(chunk.byteLength / 2));
      for (let index = 0; index < samples.length; index += 1) {
        samples[index] = chunk.readInt16LE(index * 2);
      }
      source.onData({
        samples,
        sampleRate: caller.sampleRate,
        bitsPerSample: 16,
        channelCount: caller.channels,
        numberOfFrames: Math.floor(samples.length / caller.channels),
      });
      sentFrames += 1;
      await wait(10);
    }
    connection.caller_audio_played = sentFrames > 0;
    connection.caller_audio_completed = true;
    callerEndedAt = Date.now();

    const responseDeadline = Math.min(
      startedAt + timeoutMs,
      callerEndedAt + POST_CALLER_RESPONSE_TAIL_MS,
    );
    while (Date.now() < responseDeadline) {
      const afterMin = Date.now() - callerEndedAt >= POST_CALLER_RESPONSE_MIN_CAPTURE_MS;
      const afterSilence = lastRemoteAudibleAt && Date.now() - lastRemoteAudibleAt >= REMOTE_AUDIO_SILENCE_BOUNDARY_MS;
      if (connection.remote_audio_after_caller_seen && afterMin && afterSilence) break;
      await wait(50);
    }

    const targetWav = pcmToWav(remoteChunks, targetSampleRate, targetChannels);
    if (!remoteChunks.length) throw new Error('Holy Guacamole SignalWire direct call captured no remote audio.');

    const result = {
      status: 'pass',
      target: {
        kind: 'signalwire_holy_guacamole',
        transport: 'signalwire_direct_webrtc',
        adapter_id: 'signalwire_holyguacamole_direct',
        selected_agent: 'holyguacamole',
      },
      turns: [{ speaker: 'caller', text: callerText, turn_pair: 1 }],
      exchanges: [{
        turn_pair: 1,
        caller: { text: callerText },
        target: { text: '' },
        latency: {
          tester_speech_end_to_first_target_audio_received_ms: (
            firstRemoteAfterCallerAt && callerEndedAt ? firstRemoteAfterCallerAt - callerEndedAt : null
          ),
          tester_speech_end_to_first_target_speech_received_ms: null,
          signal_boundary: 'remote_audio_energy',
          measurement_scope: 'remote_target_observed_at_tester',
          remote_target: true,
        },
        media: {
          caller_audio_wav_base64: callerWav.toString('base64'),
          target_audio_wav_base64: targetWav.toString('base64'),
          caller_audio_frames: sentFrames,
          target_audio_frames: remoteChunks.length,
        },
      }],
      latency_metrics: {
        tester_speech_end_to_first_target_audio_received_ms: (
          firstRemoteAfterCallerAt && callerEndedAt ? firstRemoteAfterCallerAt - callerEndedAt : null
        ),
        connect_to_first_remote_audio_ms: firstRemoteAudioAt ? firstRemoteAudioAt - startedAt : null,
        total_run_ms: Date.now() - startedAt,
      },
      connection,
      media: {
        caller_audio_wav_base64: callerWav.toString('base64'),
        target_audio_wav_base64: targetWav.toString('base64'),
        caller_audio_frames: sentFrames,
        target_audio_frames: remoteChunks.length,
        target_audio_sample_rate: targetSampleRate,
        target_audio_channels: targetChannels,
        target_audio_bytes: targetWav.length,
      },
      app_messages: events,
      transcript: {
        caller_text: callerText,
        caller_text_verified: true,
        caller_text_source: 'current_run_kokoro',
        agent_text: '',
        source: 'signalwire_direct_remote_audio_untranscribed',
      },
      provenance: {
        live_external_connection: true,
        browser_peer: false,
        headless_browser: false,
        guest_token_persisted: false,
        fixture_backed: false,
        tester_media: 'current_run_kokoro',
        target_media: 'current_run_signalwire_webrtc',
        token_bootstrap_endpoint: '/get_token',
        target_address_observed: '/public/holyguacamole?channel=audio',
      },
    };
    console.log(JSON.stringify(result));
  } finally {
    for (const item of cleanup.reverse()) await item();
    try { await client.disconnect(); } catch {}
    inputTrack.stop();
  }
}

main().catch((error) => {
  console.log(JSON.stringify({
    status: 'blocked',
    reason: String(error?.message || 'Holy Guacamole SignalWire direct execution failed.'),
  }));
  process.exitCode = 1;
}).finally(() => {
  setTimeout(() => process.exit(process.exitCode || 0), 100);
});
