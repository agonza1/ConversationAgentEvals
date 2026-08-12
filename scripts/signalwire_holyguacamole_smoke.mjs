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

function parseArgs(argv) {
  const args = {
    targetUrl: process.env.SIGNALWIRE_HOLYGUACAMOLE_TARGET_URL || DEFAULT_TARGET_URL,
    callerText: process.env.SIGNALWIRE_HOLYGUACAMOLE_CALLER_TEXT || 'I would like one chicken taco and a small drink.',
    callerAudio: process.env.SIGNALWIRE_HOLYGUACAMOLE_CALLER_AUDIO || '',
    artifactRoot: process.env.SIGNALWIRE_HOLYGUACAMOLE_ARTIFACT_ROOT || 'artifacts/signalwire-holyguacamole-smoke',
    timeoutMs: Number(process.env.SIGNALWIRE_HOLYGUACAMOLE_TIMEOUT_MS || 60000),
    headed: process.env.SIGNALWIRE_HOLYGUACAMOLE_HEADED === '1',
    jsonOnly: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--target-url') args.targetUrl = requireValue(argv, ++index, value);
    else if (value === '--caller-text') args.callerText = requireValue(argv, ++index, value);
    else if (value === '--caller-audio') args.callerAudio = requireValue(argv, ++index, value);
    else if (value === '--artifact-root') args.artifactRoot = requireValue(argv, ++index, value);
    else if (value === '--timeout-ms') args.timeoutMs = Number(requireValue(argv, ++index, value));
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
      remote_audio_after_caller_seen: false,
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
    artifacts: {},
  };
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
    return { path: target, source: 'supplied_audio_file' };
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
    return { path: target, source: 'kokoro_tts' };
  }
  if (process.platform === 'darwin') {
    await execFileAsync('/usr/bin/say', [
      '-o', target,
      '--file-format=WAVE',
      '--data-format=LEI16@16000',
      args.callerText,
    ], { timeout: 30000 });
    return { path: target, source: 'macos_say_tts' };
  }
  throw new Error(
    'Caller audio synthesis unavailable. Provide --caller-audio with real speech audio, '
    + 'or set KOKORO_BASE_URL so the requested caller text can be synthesized.'
  );
}

async function writeArtifacts(result, runDir, targetAudio) {
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
  result.transcript.text = `Caller: ${args.callerText}`;
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
  try {
    const callerAudio = await synthesizeCallerAudio(args, runDir);
    const callerBytes = await fs.readFile(callerAudio.path);
    result.tester.media_source = callerAudio.source;
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
          const bytes = Uint8Array.from(atob(audioBase64), (char) => char.charCodeAt(0));
          const buffer = await context.decodeAudioData(bytes.buffer.slice(0));
          const source = context.createBufferSource();
          source.buffer = buffer;
          source.loop = false;
          const gain = context.createGain();
          gain.gain.value = 0.95;
          const processor = context.createScriptProcessor(2048, 1, 1);
          const sink = context.createGain();
          sink.gain.value = 0;
          const startAt = context.currentTime + 5;
          const playback = {
            loop: false,
            duration_ms: Math.round(buffer.duration * 1000),
            start_delay_ms: 5000,
            scheduled_start_epoch_ms: Date.now() + 5000,
            first_outbound_sample_epoch_ms: null,
            first_outbound_sample_offset_ms: null,
            ended_epoch_ms: null,
            ended: false,
            observed_peak: 0,
            get_user_media_request_count: 1,
          };
          processor.onaudioprocess = (event) => {
            const input = event.inputBuffer.getChannelData(0);
            let peak = 0;
            for (let index = 0; index < input.length; index += 1) {
              const sample = Math.abs(input[index]);
              if (sample > peak) peak = sample;
            }
            if (peak > playback.observed_peak) playback.observed_peak = peak;
            if (playback.first_outbound_sample_epoch_ms === null && peak >= 0.001) {
              playback.first_outbound_sample_epoch_ms = Date.now();
              playback.first_outbound_sample_offset_ms = Math.max(0, Math.round((context.currentTime - startAt) * 1000));
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
          await context.resume().catch(() => {});
          source.start(startAt);
          source.stop(startAt + buffer.duration);
          window.__caeInjectedAudioContext = context;
          window.__caeInjectedAudioSource = source;
          window.__caeInjectedAudioPlayback = playback;
          window.__caeInjectedAudioStream = destination.stream;
          return window.__caeInjectedAudioStream;
        })();
        return window.__caeInjectedAudioStreamPromise;
      };
    }, { audioBase64: callerBytes.toString('base64') });

    const page = await context.newPage();
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

    const recorderPromise = page.evaluate(async ({ timeoutMs, postCallerRemoteAudioGraceMs }) => {
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
      if (audioContext) {
        await audioContext.resume().catch(() => {});
        sourceNode = audioContext.createMediaStreamSource(audioOnly);
        processor = audioContext.createScriptProcessor(2048, 1, 1);
        sink = audioContext.createGain();
        sink.gain.value = 0;
        processor.onaudioprocess = (event) => {
          const input = event.inputBuffer.getChannelData(0);
          let peak = 0;
          for (let index = 0; index < input.length; index += 1) {
            const sample = Math.abs(input[index]);
            if (sample > peak) peak = sample;
          }
          if (peak >= 0.001) {
            const sampleEpochMs = Date.now();
            if (firstAudibleAudioEpochMs === null) firstAudibleAudioEpochMs = sampleEpochMs;
            const callerPlayback = window.__caeInjectedAudioPlayback || null;
            if (
              callerPlayback?.ended_epoch_ms
              && sampleEpochMs >= callerPlayback.ended_epoch_ms + postCallerRemoteAudioGraceMs
              && firstAudibleAudioAfterCallerEpochMs === null
            ) {
              firstAudibleAudioAfterCallerEpochMs = sampleEpochMs;
            }
          }
        };
        sourceNode.connect(processor);
        processor.connect(sink);
        sink.connect(audioContext.destination);
      }
      const started = Date.now();
      recorder.start(250);
      const recordDeadline = Date.now() + Math.min(18000, Math.max(4000, timeoutMs - 1000));
      while (Date.now() < recordDeadline) {
        if (firstAudibleAudioAfterCallerEpochMs !== null && Date.now() - firstAudibleAudioAfterCallerEpochMs >= 1000) {
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
      return {
        ok: true,
        mimeType,
        base64: btoa(binary),
        bytes: array.length,
        durationMs: Date.now() - started,
        trackAttachedEpochMs,
        firstAudibleAudioEpochMs,
        firstAudibleAudioAfterCallerEpochMs,
        postCallerRemoteAudioGraceMs,
      };
    }, { timeoutMs: args.timeoutMs, postCallerRemoteAudioGraceMs: POST_CALLER_REMOTE_AUDIO_GRACE_MS });

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
        };
      });
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
      if (connectedMs && Date.now() - connectedMs > 12000) break;
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
    deriveTranscript(result, args);
    if (
      result.connection.ui_connected
      && result.connection.caller_audio_played
      && result.connection.caller_audio_completed
      && targetAudio?.buffer?.length
      && result.connection.remote_audio_sample_seen
      && result.connection.remote_audio_after_caller_seen
    ) {
      result.status = 'pass';
      result.reason = 'Holy Guacamole SignalWire connected, caller audio playback was observed, and post-caller audible remote audio was captured.';
    } else {
      result.status = 'blocked';
      result.reason_code = !result.connection.caller_audio_played
        ? 'caller_audio_not_played'
        : !result.connection.caller_audio_completed
          ? 'caller_audio_not_completed'
          : !result.connection.remote_audio_after_caller_seen
            ? 'post_caller_remote_audible_audio_not_captured'
            : recorded.reason || 'remote_audible_audio_not_captured';
      result.reason = 'The public SignalWire page was reached, but grounded caller playback and post-caller audible remote audio evidence were not both captured before timeout.';
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
    await writeArtifacts(result, runDir, targetAudio);
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
