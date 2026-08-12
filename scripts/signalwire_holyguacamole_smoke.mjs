#!/usr/bin/env node
import { chromium } from 'playwright';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const execFileAsync = promisify(execFile);
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_TARGET_URL = 'https://holyguacamole.signalwire.me/';
const RESULT_SCHEMA_VERSION = 'signalwire-holyguacamole-smoke-result-v1';
const POST_CALLER_REMOTE_AUDIO_GRACE_MS = 500;
const REMOTE_AUDIO_SILENCE_BOUNDARY_MS = 700;
const POST_CALLER_RESPONSE_END_SILENCE_MS = 1200;
const POST_CALLER_RESPONSE_MIN_CAPTURE_MS = 3000;
const POST_CALLER_RESPONSE_TAIL_MS = 8000;

function parseArgs(argv) {
  const args = {
    targetUrl: process.env.SIGNALWIRE_HOLYGUACAMOLE_TARGET_URL || DEFAULT_TARGET_URL,
    callerText: process.env.SIGNALWIRE_HOLYGUACAMOLE_CALLER_TEXT || 'I would like one chicken taco and a small drink.',
    callerAudio: process.env.SIGNALWIRE_HOLYGUACAMOLE_CALLER_AUDIO || '',
    artifactRoot: process.env.SIGNALWIRE_HOLYGUACAMOLE_ARTIFACT_ROOT || 'artifacts/signalwire-holyguacamole-smoke',
    timeoutMs: Number(process.env.SIGNALWIRE_HOLYGUACAMOLE_TIMEOUT_MS || 60000),
    headed: process.env.SIGNALWIRE_HOLYGUACAMOLE_HEADED === '1',
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
    else if (value === '--headed') args.headed = true;
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

function normalizeAllowlistedTargetUrl(value) {
  const url = new URL(value);
  const expected = new URL(DEFAULT_TARGET_URL);
  if (url.href !== expected.href) {
    throw new Error(`Holy Guacamole smoke is allowlisted to ${DEFAULT_TARGET_URL}.`);
  }
  return expected.href;
}

function requireValue(argv, index, flag) {
  const value = argv[index];
  if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value.`);
  return value;
}

function printHelp() {
  console.log(`Run a real Holy Guacamole SignalWire browser voice smoke.

Usage:
  SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC=1 node scripts/signalwire_holyguacamole_smoke.mjs [options]

Options:
  --target-url <url>       Fixed target URL. Default: ${DEFAULT_TARGET_URL}
  --caller-text <text>     Caller utterance to synthesize/play.
  --caller-audio <path>    WAV/MP3/OGG audio file to inject as browser microphone.
  --artifact-root <path>   Artifact directory. Default: artifacts/signalwire-holyguacamole-smoke
  --timeout-ms <ms>        Overall wait budget. Default: 60000
  --max-exchanges <1|2>   Exchanges to run in the same browser call. Default: 1
  --headed                 Show Chromium during the run.
  --json-only              Print only the machine-readable summary.
`);
}

function nowIso() {
  return new Date().toISOString();
}

function relativeToRepo(value) {
  return path.relative(REPO_ROOT, value);
}

function redact(value) {
  return String(value)
    .replace(/token["'=:\s]+[^"',\s}]+/gi, 'token=<redacted>')
    .replace(/bearer\s+[A-Za-z0-9._-]+/gi, 'Bearer <redacted>');
}

class CaeLiveAudioPublisher {
  constructor(args) {
    this.baseUrl = args.livePublishBaseUrl;
    this.token = args.livePublishToken;
    this.executionRunId = args.livePublishExecutionRunId;
    this.sessionId = args.livePublishSessionId || `${this.executionRunId}:signalwire-browser`;
    this.publisherId = args.livePublishPublisherId || null;
    this.queue = [];
    this.flushTimer = null;
    this.pending = Promise.resolve();
    this.error = null;
  }
  get configured() {
    return Boolean(this.baseUrl && this.token && this.executionRunId);
  }

  async request(pathname, body) {
    const response = await fetch(`${this.baseUrl}${pathname}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-cae-reference-token': this.token,
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`CAE live audio publisher returned HTTP ${response.status}.`);
    }
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
        if (
          previous
          && previous.direction === frame.direction
          && previous.sampleRate === frame.sampleRate
          && previous.turnPair === frame.turnPair
        ) {
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
    if (this.flushTimer !== null) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
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
      id: 'cae_signalwire_browser_tester',
      executor_id: 'signalwire_public_browser',
      browser: 'chromium',
      headless_browser: !args.headed,
      microphone_permission: 'browser_permission_granted_with_injected_current_run_audio',
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
    },
    timestamps: { started_at: startedAt, completed_at: null },
    connection: {
      page_loaded: false,
      token_endpoint_seen: false,
      token_status: null,
      sdk_connected: false,
      ui_connected: false,
      remote_stream_seen: false,
      caller_audio_played: false,
      caller_audio_completed: false,
      post_caller_silence_boundary_seen: false,
      remote_audio_after_caller_seen: false,
      post_caller_response_end_seen: false,
      terminal_status: null,
    },
    latency_metrics: {
      page_load_ms: null,
      connect_click_to_token_response_ms: null,
      connect_click_to_ui_connected_ms: null,
      connect_click_to_remote_track_ms: null,
      connect_click_to_remote_audio_ms: null,
      connect_click_to_first_audible_audio_ms: null,
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
    media: {
      target_audio_duration_ms: null,
      target_audio_bytes: 0,
    },
    network_events: [],
    console_events: [],
    page_events: [],
    exchanges: [],
    artifacts: {},
  };
}

async function generateTesterFollowup(args, targetAudioWavBase64, history, turnPair) {
  if (!args.livePublishBaseUrl || !args.livePublishToken) {
    throw new Error('Multi-exchange SignalWire execution requires the existing Pipecat tester runtime.');
  }
  const goal = String(args.scenario?.goal || args.callerText).trim();
  const persona = String(args.scenario?.persona || 'the original caller').trim();
  const response = await fetch(`${args.livePublishBaseUrl}/reference-tester/turn`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-cae-reference-token': args.livePublishToken,
    },
    body: JSON.stringify({
      scenario_instruction: `${args.scenario?.id || 'signalwire-holyguacamole'}: ${goal}`,
      act_id: `caller-follow-up-${turnPair}`,
      act_objective: `Respond naturally as ${persona} and move the conversation toward this caller goal: ${goal}`,
      example_utterance: args.callerText,
      history,
      target_audio_wav_base64: targetAudioWavBase64,
      model_name: args.testerModelName,
    }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Existing Pipecat tester turn failed with HTTP ${response.status}: ${detail.slice(0, 240)}`);
  }
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
    if (!sourceStat.isFile() || sourceStat.size <= 0) {
      throw new Error(`Caller audio file is empty or not a file: ${args.callerAudio}`);
    }
    const target = path.join(runDir, 'caller-audio' + path.extname(source));
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
    await execFileAsync('/usr/bin/say', [
      '-o', target,
      '--file-format=WAVE',
      '--data-format=LEI16@16000',
      args.callerText,
    ], { timeout: 30000 });
    return { path: target, source: 'macos_say_tts', callerTextVerified: true };
  }
  throw new Error(
    'Caller audio synthesis unavailable. Provide --caller-audio with real speech audio, '
    + 'or set KOKORO_BASE_URL so the requested caller text can be synthesized.'
  );
}

async function writeArtifacts(result, runDir, targetAudio, responseAudios) {
  const transcriptPath = path.join(runDir, 'transcript.txt');
  await fs.writeFile(transcriptPath, `${result.transcript.text || ''}\n`, 'utf8');
  result.transcript.artifact_path = relativeToRepo(transcriptPath);
  if (targetAudio?.buffer?.length) {
    const audioPath = path.join(runDir, targetAudio.extension || 'target-audio.webm');
    await fs.writeFile(audioPath, targetAudio.buffer);
    result.media.target_audio_bytes = targetAudio.buffer.length;
    result.media.target_audio_duration_ms = targetAudio.durationMs;
    result.artifacts.target_audio = relativeToRepo(audioPath);
    result.artifacts.target_audio_mime = targetAudio.mimeType || 'audio/webm';
    result.artifacts.target_audio_sha256 = crypto.createHash('sha256').update(targetAudio.buffer).digest('hex');
  }
  const responseAudio = responseAudios[0];
  if (responseAudio?.buffer?.length) {
    const responsePath = path.join(runDir, 'target-response.wav');
    await fs.writeFile(responsePath, responseAudio.buffer);
    result.media.target_response_audio_bytes = responseAudio.buffer.length;
    result.media.target_response_audio_duration_ms = responseAudio.durationMs;
    result.artifacts.target_response_audio = relativeToRepo(responsePath);
    result.artifacts.target_response_audio_mime = 'audio/wav';
    result.artifacts.target_response_audio_sha256 = crypto
      .createHash('sha256')
      .update(responseAudio.buffer)
      .digest('hex');
  }
  result.artifacts.target_response_audio_turns = [];
  for (let index = 0; index < responseAudios.length; index += 1) {
    const turnAudio = responseAudios[index];
    if (!turnAudio?.buffer?.length) continue;
    const turnPath = path.join(runDir, `target-response-turn-${index + 1}.wav`);
    await fs.writeFile(turnPath, turnAudio.buffer);
    result.artifacts.target_response_audio_turns.push({
      turn_pair: index + 1,
      path: relativeToRepo(turnPath),
      mime_type: 'audio/wav',
      sha256: crypto.createHash('sha256').update(turnAudio.buffer).digest('hex'),
      duration_ms: turnAudio.durationMs,
    });
  }
  const resultPath = path.join(runDir, 'result.json');
  const latestPath = path.join(path.dirname(runDir), 'latest.json');
  result.artifacts.result_json = relativeToRepo(resultPath);
  result.artifacts.transcript_txt = relativeToRepo(transcriptPath);
  result.artifacts.latest_json = relativeToRepo(latestPath);
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  await fs.writeFile(resultPath, serialized, 'utf8');
  await fs.writeFile(latestPath, serialized, 'utf8');
  return resultPath;
}

function deriveTranscript(result, args) {
  result.transcript.agent_text = '';
  result.transcript.agent_text_available = false;
  result.transcript.untranscribed_target_audio = Boolean(result.connection.remote_stream_seen);
  const callerTextVerified = result.tester.caller_text_verified === true;
  result.transcript.caller_text = callerTextVerified ? args.callerText : '';
  result.transcript.caller_text_verified = callerTextVerified;
  result.transcript.caller_text_source = callerTextVerified
    ? String(result.tester.media_source || 'current_run_tts')
    : 'unverified_supplied_audio';
  result.transcript.text = callerTextVerified
    ? `Caller: ${args.callerText}`
    : 'Caller audio: supplied audio file (speech text unverified)';
}

async function runSmoke(args) {
  const startedAt = nowIso();
  const startedMs = Date.now();
  const runId = `signalwire-holyguacamole-${startedAt.replace(/[:.]/g, '').replace(/-/g, '')}`;
  const runDir = path.resolve(REPO_ROOT, args.artifactRoot, runId);
  await fs.mkdir(runDir, { recursive: true });
  const result = baseResult(args, startedAt);
  let browser;
  let targetAudio = null;
  const responseAudios = [];
  const livePublisher = new CaeLiveAudioPublisher(args);
  try {
    result.connection.cae_live_broadcast_connected = await livePublisher.open();
    if (livePublisher.error) result.connection.cae_live_broadcast_error = livePublisher.error;
    const callerAudio = await synthesizeCallerAudio(args, runDir);
    const callerBytes = await fs.readFile(callerAudio.path);
    result.tester.media_source = callerAudio.source;
    result.tester.caller_text_verified = callerAudio.callerTextVerified === true;
    result.artifacts.caller_audio = relativeToRepo(callerAudio.path);

    browser = await chromium.launch({
      headless: !args.headed,
      args: ['--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
    });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, permissions: ['microphone'] });
    await context.grantPermissions(['microphone'], { origin: new URL(args.targetUrl).origin });
    await context.addInitScript(({ audioBase64 }) => {
      const originalGetUserMedia = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async (constraints) => {
        if (!constraints || !constraints.audio) {
          return originalGetUserMedia ? originalGetUserMedia(constraints) : new MediaStream();
        }
        if (window.__caeInjectedAudioPlayback) {
          window.__caeInjectedAudioPlayback.get_user_media_request_count += 1;
        }
        if (window.__caeInjectedAudioStreamPromise) {
          return window.__caeInjectedAudioStreamPromise;
        }
        window.__caeInjectedAudioStreamPromise = (async () => {
          const context = new AudioContext();
          const destination = context.createMediaStreamDestination();
          await context.resume().catch(() => {});
          const playAudio = async (nextAudioBase64, reason, turnPair) => {
            const bytes = Uint8Array.from(atob(nextAudioBase64), (char) => char.charCodeAt(0));
            const buffer = await context.decodeAudioData(bytes.buffer.slice(0));
            const source = context.createBufferSource();
            source.buffer = buffer;
            source.loop = false;
            const gain = context.createGain();
            gain.gain.value = 0.95;
            const processor = context.createScriptProcessor(2048, 1, 1);
            const sink = context.createGain();
            sink.gain.value = 0;
            const playback = {
              turn_pair: turnPair,
              loop: false,
              duration_ms: Math.round(buffer.duration * 1000),
              start_delay_ms: 0,
              readiness_trigger: reason,
              readiness_triggered_epoch_ms: Date.now(),
              scheduled_start_epoch_ms: null,
              first_outbound_sample_epoch_ms: null,
              first_outbound_sample_offset_ms: null,
              ended_epoch_ms: null,
              ended: false,
              observed_peak: 0,
              get_user_media_request_count: window.__caeInjectedAudioPlayback?.get_user_media_request_count || 1,
            };
            const scheduledStartContextTime = context.currentTime;
            playback.scheduled_start_epoch_ms = Date.now();
            processor.onaudioprocess = (event) => {
              const input = event.inputBuffer.getChannelData(0);
              if (typeof window.__caePublishLivePcm === 'function') {
                const pcm = new Uint8Array(input.length * 2);
                const view = new DataView(pcm.buffer);
                for (let index = 0; index < input.length; index += 1) {
                  const sample = Math.max(-1, Math.min(1, input[index]));
                  view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
                }
                let binary = '';
                for (let index = 0; index < pcm.length; index += 1) binary += String.fromCharCode(pcm[index]);
                window.__caePublishLivePcm({
                  direction: 'tester_to_target',
                  turnPair,
                  sampleRate: context.sampleRate,
                  pcm16Base64: btoa(binary),
                }).catch(() => {});
              }
              let peak = 0;
              for (let index = 0; index < input.length; index += 1) peak = Math.max(peak, Math.abs(input[index]));
              playback.observed_peak = Math.max(playback.observed_peak, peak);
              if (playback.first_outbound_sample_epoch_ms === null && peak >= 0.001) {
                playback.first_outbound_sample_epoch_ms = Date.now();
                playback.first_outbound_sample_offset_ms = Math.max(
                  0,
                  Math.round((context.currentTime - scheduledStartContextTime) * 1000),
                );
              }
            };
            source.onended = () => {
              playback.ended = true;
              playback.ended_epoch_ms = Date.now();
              processor.disconnect();
              sink.disconnect();
            };
            source.connect(gain);
            gain.connect(destination);
            gain.connect(processor);
            processor.connect(sink);
            sink.connect(context.destination);
            window.__caeInjectedAudioSource = source;
            window.__caeInjectedAudioPlayback = playback;
            source.start(scheduledStartContextTime);
            source.stop(scheduledStartContextTime + buffer.duration);
            return playback;
          };
          let initialStarted = false;
          window.__caeStartInjectedAudio = async (reason = 'webrtc_media_ready') => {
            if (initialStarted) return window.__caeInjectedAudioPlayback;
            initialStarted = true;
            return playAudio(audioBase64, reason, 1);
          };
          window.__caePlayInjectedAudioBase64 = async (nextAudioBase64, reason, turnPair) => (
            playAudio(nextAudioBase64, reason, turnPair)
          );
          window.__caeInjectedAudioContext = context;
          window.__caeInjectedAudioStream = destination.stream;
          return window.__caeInjectedAudioStream;
        })();
        return window.__caeInjectedAudioStreamPromise;
      };
    }, { audioBase64: callerBytes.toString('base64') });

    const page = await context.newPage();
    await page.exposeFunction('__caePublishLivePcm', (frame) => {
      livePublisher.enqueue(frame);
    });
    let clickMs = null;
    let connectedMs = null;
    let remoteAudioMs = null;

    page.on('console', (message) => {
      const text = message.text();
      if (/SignalWire|Call status|Connected|user_event|error|warn|order|token/i.test(text)) {
        result.console_events.push({ type: message.type(), text: redact(text).slice(0, 700), t_ms: Date.now() - startedMs });
      }
      const statusMatch = text.match(/Call status:\s*(\w+)/i);
      if (statusMatch) {
        result.page_events.push({ kind: 'call_status', text: statusMatch[1], t_ms: Date.now() - startedMs });
        result.connection.terminal_status = statusMatch[1];
      }
    });
    page.on('response', async (response) => {
      const url = response.url();
      if (!url.includes('/get_token')) return;
      result.connection.token_endpoint_seen = true;
      result.connection.token_status = response.status();
      if (clickMs !== null) result.latency_metrics.connect_click_to_token_response_ms = Date.now() - clickMs;
      result.network_events.push({ phase: 'response', status: response.status(), url: `${new URL(url).origin}/get_token`, t_ms: Date.now() - startedMs });
    });

    const pageStartMs = Date.now();
    await page.goto(args.targetUrl, { waitUntil: 'networkidle', timeout: args.timeoutMs });
    result.connection.page_loaded = true;
    result.latency_metrics.page_load_ms = Date.now() - pageStartMs;

    const connectButton = page.locator('#connectBtn');
    if (!(await connectButton.count())) {
      result.status = 'blocked';
      result.reason_code = 'no_start_control';
      result.reason = 'The Holy Guacamole page loaded, but no Start Ordering control was available.';
      return result;
    }
    clickMs = Date.now();
    await connectButton.first().click({ timeout: Math.min(args.timeoutMs, 15000) });

    const recorderConfig = {
      turnPair: 1,
      timeoutMs: args.timeoutMs,
      postCallerRemoteAudioGraceMs: POST_CALLER_REMOTE_AUDIO_GRACE_MS,
      remoteAudioSilenceBoundaryMs: REMOTE_AUDIO_SILENCE_BOUNDARY_MS,
      postCallerResponseEndSilenceMs: POST_CALLER_RESPONSE_END_SILENCE_MS,
      postCallerResponseMinCaptureMs: POST_CALLER_RESPONSE_MIN_CAPTURE_MS,
      postCallerResponseTailMs: POST_CALLER_RESPONSE_TAIL_MS,
    };
    const recorderPromise = page.evaluate(async (config) => {
      window.__caeCaptureRemoteResponse = async ({
      turnPair,
      timeoutMs,
      postCallerRemoteAudioGraceMs,
      remoteAudioSilenceBoundaryMs,
      postCallerResponseEndSilenceMs,
      postCallerResponseMinCaptureMs,
      postCallerResponseTailMs,
    }) => {
      const deadline = Date.now() + timeoutMs;
      let video = null;
      while (Date.now() < deadline) {
        video = document.querySelector('#remote-video');
        if (video && video.srcObject && video.srcObject.getAudioTracks().length) break;
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      if (!video || !video.srcObject || !video.srcObject.getAudioTracks().length) {
        return { ok: false, reason: 'remote_stream_timeout' };
      }
      const trackAttachedEpochMs = Date.now();
      const stream = video.captureStream ? video.captureStream() : video.srcObject;
      const audioOnly = new MediaStream(stream.getAudioTracks());
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
      const recorder = new MediaRecorder(audioOnly, { mimeType });
      const chunks = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      };
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      const audioContext = AudioContextClass ? new AudioContextClass() : null;
      let sourceNode = null;
      let processor = null;
      let sink = null;
      let firstAudibleAudioEpochMs = null;
      let firstAudibleAudioAfterCallerEpochMs = null;
      let postCallerSilenceBoundaryEpochMs = null;
      let postCallerResponseEndEpochMs = null;
      let responseLastAudibleEpochMs = null;
      let wasAudible = false;
      let silentSinceEpochMs = Date.now();
      const capturedPcmChunks = [];
      let capturedPcmSamples = 0;
      let responseStartSampleIndex = null;
      let responseLastAudibleSampleIndex = null;
      if (audioContext) {
        await audioContext.resume().catch(() => {});
        sourceNode = audioContext.createMediaStreamSource(audioOnly);
        processor = audioContext.createScriptProcessor(2048, 1, 1);
        sink = audioContext.createGain();
        sink.gain.value = 0;
        processor.onaudioprocess = (event) => {
          const input = event.inputBuffer.getChannelData(0);
          if (typeof window.__caePublishLivePcm === 'function') {
            const pcm = new Uint8Array(input.length * 2);
            const view = new DataView(pcm.buffer);
            for (let index = 0; index < input.length; index += 1) {
              const sample = Math.max(-1, Math.min(1, input[index]));
              view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
            }
            let binary = '';
            for (let index = 0; index < pcm.length; index += 1) binary += String.fromCharCode(pcm[index]);
            window.__caePublishLivePcm({
              direction: 'target_to_tester',
              turnPair,
              sampleRate: audioContext.sampleRate,
              pcm16Base64: btoa(binary),
            }).catch(() => {});
          }
          const chunkStartSample = capturedPcmSamples;
          capturedPcmChunks.push(Float32Array.from(input));
          capturedPcmSamples += input.length;
          let peak = 0;
          for (let index = 0; index < input.length; index += 1) {
            const sample = Math.abs(input[index]);
            if (sample > peak) peak = sample;
          }
          const sampleEpochMs = Date.now();
          const callerPlayback = window.__caeInjectedAudioPlayback || null;
          const callerEndedEpochMs = callerPlayback?.ended_epoch_ms || null;
          const postCallerReady = Boolean(
            callerEndedEpochMs
            && sampleEpochMs >= callerEndedEpochMs + postCallerRemoteAudioGraceMs
          );
          if (peak >= 0.001) {
            if (firstAudibleAudioEpochMs === null) firstAudibleAudioEpochMs = sampleEpochMs;
            const isAudibleOnset = !wasAudible;
            const hadSilenceBoundary = silentSinceEpochMs !== null
              && sampleEpochMs - silentSinceEpochMs >= remoteAudioSilenceBoundaryMs;
            if (postCallerReady && hadSilenceBoundary && postCallerSilenceBoundaryEpochMs === null) {
              postCallerSilenceBoundaryEpochMs = silentSinceEpochMs;
            }
            if (
              postCallerReady
              && isAudibleOnset
              && postCallerSilenceBoundaryEpochMs !== null
              && firstAudibleAudioAfterCallerEpochMs === null
            ) {
              firstAudibleAudioAfterCallerEpochMs = sampleEpochMs;
              responseStartSampleIndex = chunkStartSample;
            }
            if (firstAudibleAudioAfterCallerEpochMs !== null) {
              responseLastAudibleEpochMs = sampleEpochMs;
              responseLastAudibleSampleIndex = capturedPcmSamples;
            }
            wasAudible = true;
            silentSinceEpochMs = null;
          } else {
            if (wasAudible || silentSinceEpochMs === null) {
              silentSinceEpochMs = sampleEpochMs;
            }
            wasAudible = false;
            if (
              postCallerReady
              && silentSinceEpochMs !== null
              && sampleEpochMs - silentSinceEpochMs >= remoteAudioSilenceBoundaryMs
              && postCallerSilenceBoundaryEpochMs === null
            ) {
              postCallerSilenceBoundaryEpochMs = silentSinceEpochMs;
            }
            if (
              firstAudibleAudioAfterCallerEpochMs !== null
              && responseLastAudibleEpochMs !== null
              && postCallerResponseEndEpochMs === null
              && sampleEpochMs - responseLastAudibleEpochMs >= postCallerResponseEndSilenceMs
            ) {
              postCallerResponseEndEpochMs = sampleEpochMs;
            }
          }
        };
        sourceNode.connect(processor);
        processor.connect(sink);
        sink.connect(audioContext.destination);
      }
      const started = Date.now();
      recorder.start(250);
      const recordDeadline = Date.now() + Math.min(24000, Math.max(12000, timeoutMs - 1000));
      while (Date.now() < recordDeadline) {
        const responseCaptureMs = firstAudibleAudioAfterCallerEpochMs === null
          ? 0
          : Date.now() - firstAudibleAudioAfterCallerEpochMs;
        if (
          postCallerResponseEndEpochMs !== null
          && responseCaptureMs >= postCallerResponseMinCaptureMs
        ) {
          break;
        }
        if (
          firstAudibleAudioAfterCallerEpochMs !== null
          && responseCaptureMs >= postCallerResponseTailMs
        ) {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      await new Promise((resolve) => {
        recorder.onstop = resolve;
        recorder.stop();
      });
      if (processor) processor.disconnect();
      if (sourceNode) sourceNode.disconnect();
      if (sink) sink.disconnect();
      if (audioContext) await audioContext.close().catch(() => {});
      const blob = new Blob(chunks, { type: mimeType });
      const array = new Uint8Array(await blob.arrayBuffer());
      let binary = '';
      for (const byte of array) binary += String.fromCharCode(byte);
      let responseWavBase64 = null;
      let responseWavBytes = 0;
      let responseWavDurationMs = null;
      if (
        audioContext
        && responseStartSampleIndex !== null
        && responseLastAudibleSampleIndex !== null
        && responseLastAudibleSampleIndex > responseStartSampleIndex
      ) {
        const sampleRate = audioContext.sampleRate;
        const responseEndSampleIndex = Math.min(
          capturedPcmSamples,
          responseLastAudibleSampleIndex + Math.round(sampleRate * 0.25),
        );
        const responseSamples = new Float32Array(responseEndSampleIndex - responseStartSampleIndex);
        let sourceOffset = 0;
        let targetOffset = 0;
        for (const chunk of capturedPcmChunks) {
          const chunkEnd = sourceOffset + chunk.length;
          const overlapStart = Math.max(sourceOffset, responseStartSampleIndex);
          const overlapEnd = Math.min(chunkEnd, responseEndSampleIndex);
          if (overlapEnd > overlapStart) {
            const from = overlapStart - sourceOffset;
            const to = overlapEnd - sourceOffset;
            responseSamples.set(chunk.subarray(from, to), targetOffset);
            targetOffset += to - from;
          }
          sourceOffset = chunkEnd;
          if (sourceOffset >= responseEndSampleIndex) break;
        }
        const wav = new Uint8Array(44 + responseSamples.length * 2);
        const view = new DataView(wav.buffer);
        const writeText = (offset, text) => {
          for (let index = 0; index < text.length; index += 1) {
            view.setUint8(offset + index, text.charCodeAt(index));
          }
        };
        writeText(0, 'RIFF');
        view.setUint32(4, 36 + responseSamples.length * 2, true);
        writeText(8, 'WAVE');
        writeText(12, 'fmt ');
        view.setUint32(16, 16, true);
        view.setUint16(20, 1, true);
        view.setUint16(22, 1, true);
        view.setUint32(24, sampleRate, true);
        view.setUint32(28, sampleRate * 2, true);
        view.setUint16(32, 2, true);
        view.setUint16(34, 16, true);
        writeText(36, 'data');
        view.setUint32(40, responseSamples.length * 2, true);
        for (let index = 0; index < responseSamples.length; index += 1) {
          const sample = Math.max(-1, Math.min(1, responseSamples[index]));
          view.setInt16(44 + index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        }
        const binaryParts = [];
        for (let offset = 0; offset < wav.length; offset += 0x8000) {
          binaryParts.push(String.fromCharCode(...wav.subarray(offset, offset + 0x8000)));
        }
        responseWavBase64 = btoa(binaryParts.join(''));
        responseWavBytes = wav.length;
        responseWavDurationMs = Math.round(responseSamples.length * 1000 / sampleRate);
      }
      return {
        ok: true,
        mimeType,
        base64: btoa(binary),
        bytes: array.length,
        durationMs: Date.now() - started,
        trackAttachedEpochMs,
        firstAudibleAudioEpochMs,
        firstAudibleAudioAfterCallerEpochMs,
        postCallerSilenceBoundaryEpochMs,
        postCallerResponseEndEpochMs,
        postCallerRemoteAudioGraceMs,
        remoteAudioSilenceBoundaryMs,
        postCallerResponseEndSilenceMs,
        postCallerResponseMinCaptureMs,
        postCallerResponseTailMs,
        responseWavBase64,
        responseWavBytes,
        responseWavDurationMs,
      };
      };
      return window.__caeCaptureRemoteResponse(config);
    }, recorderConfig);

    const deadline = startedMs + args.timeoutMs;
    while (Date.now() < deadline) {
      const state = await page.evaluate(() => {
        const text = (selector) => document.querySelector(selector)?.textContent?.replace(/\s+/g, ' ').trim() || '';
        return {
          status: text('#status'),
          result: text('#result-message'),
          order: text('#order-display'),
          connected: Boolean(document.querySelector('#remote-video')) || document.body.innerText.includes('Connected!'),
          remoteAudio: Boolean(document.querySelector('#remote-video')?.srcObject?.getAudioTracks?.().length),
          callerPlayback: window.__caeInjectedAudioPlayback || null,
          canStartCallerPlayback: typeof window.__caeStartInjectedAudio === 'function',
        };
      });
      if (
        state.connected
        && state.remoteAudio
        && state.canStartCallerPlayback
        && !state.callerPlayback?.readiness_triggered_epoch_ms
      ) {
        const startedPlayback = await page.evaluate(async () => (
          window.__caeStartInjectedAudio
            ? window.__caeStartInjectedAudio('webrtc_connected_remote_audio_track')
            : null
        )).catch(() => null);
        if (startedPlayback) state.callerPlayback = startedPlayback;
      }
      if (state.callerPlayback) {
        result.tester.caller_audio_playback = state.callerPlayback;
        result.connection.caller_audio_played = Boolean(state.callerPlayback.first_outbound_sample_epoch_ms);
        result.connection.caller_audio_completed = Boolean(state.callerPlayback.ended);
      }
      if (state.status) result.page_events.push({ kind: 'status', text: state.status, t_ms: Date.now() - startedMs });
      if (state.result) result.page_events.push({ kind: 'result', text: state.result, t_ms: Date.now() - startedMs });
      if (state.order && !state.order.includes('Your order will appear here')) {
        result.page_events.push({ kind: 'order', text: state.order, t_ms: Date.now() - startedMs });
      }
      if (state.connected && !connectedMs) {
        connectedMs = Date.now();
        result.connection.ui_connected = true;
        result.connection.sdk_connected = true;
        result.latency_metrics.connect_click_to_ui_connected_ms = clickMs ? connectedMs - clickMs : null;
      }
      if (state.remoteAudio && !remoteAudioMs) {
        remoteAudioMs = Date.now();
        result.connection.remote_stream_seen = true;
        result.latency_metrics.connect_click_to_remote_track_ms = clickMs ? remoteAudioMs - clickMs : null;
      }
      if (state.callerPlayback?.ended_epoch_ms && Date.now() - state.callerPlayback.ended_epoch_ms > 1500) break;
      await page.waitForTimeout(500);
    }

    const recorded = await recorderPromise.catch((error) => ({ ok: false, reason: String(error) }));
    const callerPlayback = await page.evaluate(() => window.__caeInjectedAudioPlayback || null).catch(() => null);
    if (callerPlayback) {
      result.tester.caller_audio_playback = callerPlayback;
      result.connection.caller_audio_played = Boolean(callerPlayback.first_outbound_sample_epoch_ms);
      result.connection.caller_audio_completed = Boolean(callerPlayback.ended);
    }
    if (recorded.ok && recorded.bytes > 0) {
      targetAudio = {
        buffer: Buffer.from(recorded.base64, 'base64'),
        mimeType: recorded.mimeType,
        durationMs: recorded.durationMs,
        extension: 'target-audio.webm',
      };
      if (recorded.responseWavBase64 && recorded.responseWavBytes > 44) {
        responseAudios.push({
          buffer: Buffer.from(recorded.responseWavBase64, 'base64'),
          durationMs: recorded.responseWavDurationMs,
        });
      }
      result.connection.post_caller_silence_boundary_seen = Number.isFinite(
        recorded.postCallerSilenceBoundaryEpochMs
      );
      result.connection.post_caller_response_end_seen = Number.isFinite(
        recorded.postCallerResponseEndEpochMs
      );
      if (Number.isFinite(recorded.firstAudibleAudioEpochMs) && clickMs) {
        const latencyMs = recorded.firstAudibleAudioEpochMs - clickMs;
        if (latencyMs >= 0) {
          result.connection.remote_audio_sample_seen = true;
          result.latency_metrics.connect_click_to_remote_audio_ms = latencyMs;
          result.latency_metrics.connect_click_to_first_audible_audio_ms = latencyMs;
        }
      }
      if (Number.isFinite(recorded.firstAudibleAudioAfterCallerEpochMs) && callerPlayback?.ended_epoch_ms) {
        const latencyMs = recorded.firstAudibleAudioAfterCallerEpochMs - callerPlayback.ended_epoch_ms;
        if (latencyMs >= 0) {
          result.connection.remote_audio_after_caller_seen = true;
          result.latency_metrics.caller_audio_completed_to_remote_audio_ms = latencyMs;
        }
      }
    }
    if (recorded.responseWavBase64 && recorded.responseWavBytes > 44) {
      result.exchanges.push({
        turn_pair: 1,
        caller_text: args.callerText,
        caller_audio_source: result.artifacts.caller_audio,
        agent_text: '',
        target_response_audio_turn: 1,
        target_response_latency_ms: result.latency_metrics.caller_audio_completed_to_remote_audio_ms,
      });
    }
    if (args.maxExchanges === 2 && recorded.responseWavBase64 && recorded.responseWavBytes > 44) {
      const followup = await generateTesterFollowup(
        args,
        recorded.responseWavBase64,
        [{ speaker: 'Caller', text: args.callerText }],
        2,
      );
      result.exchanges[0].agent_text = followup.targetText;
      const secondRecorderPromise = page.evaluate(
        (config) => window.__caeCaptureRemoteResponse({ ...config, turnPair: 2 }),
        recorderConfig,
      );
      await page.waitForTimeout(300);
      const secondPlayback = await page.evaluate(async ({ audioBase64 }) => (
        window.__caePlayInjectedAudioBase64
          ? window.__caePlayInjectedAudioBase64(audioBase64, 'tester_follow_up_2', 2)
          : null
      ), { audioBase64: followup.testerAudioWavBase64 });
      if (!secondPlayback) throw new Error('SignalWire browser could not play tester follow-up audio.');
      const secondRecorded = await secondRecorderPromise.catch((error) => ({
        ok: false,
        reason: String(error),
      }));
      if (
        !secondRecorded.ok
        || !secondRecorded.responseWavBase64
        || secondRecorded.responseWavBytes <= 44
        || !Number.isFinite(secondRecorded.firstAudibleAudioAfterCallerEpochMs)
      ) {
        throw new Error(
          `SignalWire exchange 2 did not capture a grounded target response: ${secondRecorded.reason || 'no audible response'}`,
        );
      }
      responseAudios.push({
        buffer: Buffer.from(secondRecorded.responseWavBase64, 'base64'),
        durationMs: secondRecorded.responseWavDurationMs,
      });
      const completedSecondPlayback = await page.evaluate(
        () => window.__caeInjectedAudioPlayback || null,
      );
      const secondLatencyMs = Number.isFinite(completedSecondPlayback?.ended_epoch_ms)
        ? secondRecorded.firstAudibleAudioAfterCallerEpochMs - completedSecondPlayback.ended_epoch_ms
        : null;
      result.exchanges.push({
        turn_pair: 2,
        caller_text: followup.testerText,
        caller_audio_wav_base64: followup.testerAudioWavBase64,
        caller_audio_source: 'reference-tester/turn',
        agent_text: '',
        target_response_audio_turn: 2,
        target_response_latency_ms: secondLatencyMs,
      });
      result.latency_metrics.exchange_2_caller_audio_completed_to_remote_audio_ms = secondLatencyMs;
      result.tester.caller_audio_playback_turn_2 = completedSecondPlayback;
    }
    deriveTranscript(result, args);
    if (
      result.connection.ui_connected
      && result.connection.caller_audio_played
      && result.connection.caller_audio_completed
      && targetAudio?.buffer?.length
      && result.connection.remote_audio_sample_seen
      && result.connection.remote_audio_after_caller_seen
      && result.exchanges.length === args.maxExchanges
      && responseAudios.length === args.maxExchanges
    ) {
      result.status = 'pass';
      result.reason = 'Holy Guacamole SignalWire connected, caller audio playback was observed, and a new post-caller audible remote response onset was captured.';
    } else {
      result.status = 'blocked';
      result.reason_code = !result.connection.caller_audio_played
        ? 'caller_audio_not_played'
        : !result.connection.caller_audio_completed
          ? 'caller_audio_not_completed'
          : !result.connection.remote_audio_after_caller_seen
            ? 'post_caller_remote_audible_onset_not_captured'
            : recorded.reason || 'remote_audible_audio_not_captured';
      result.reason = 'The public SignalWire page was reached, but grounded caller playback and a new post-caller audible remote response onset were not both captured before timeout.';
    }

    await page.locator('#hangupBtn').click({ timeout: 3000 }).catch(() => {});
    return result;
  } catch (error) {
    result.status = 'blocked';
    result.reason_code = classifyError(error);
    result.reason = redact(error instanceof Error ? error.message : String(error));
    deriveTranscript(result, args);
    return result;
  } finally {
    result.timestamps.completed_at = nowIso();
    result.latency_metrics.total_run_ms = Date.now() - startedMs;
    if (browser) await browser.close().catch(() => {});
    await livePublisher.close();
    if (livePublisher.error) result.connection.cae_live_broadcast_error = livePublisher.error;
    await writeArtifacts(result, runDir, targetAudio, responseAudios);
  }
}

function classifyError(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (/permission|microphone|media/i.test(message)) return 'browser_permission_denied';
  if (/timeout/i.test(message)) return 'connection_timeout';
  if (/net::|ECONN|ENOTFOUND|fetch|navigation/i.test(message)) return 'target_unreachable';
  if (/Kokoro|say|Caller audio synthesis|caller audio/i.test(message)) return 'tester_audio_synthesis_failed';
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
  process.exitCode = result.status === 'pass' ? 0 : 2;
}

main().catch((error) => {
  console.error(redact(error instanceof Error ? error.message : String(error)));
  process.exit(1);
});
