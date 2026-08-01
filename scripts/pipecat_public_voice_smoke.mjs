#!/usr/bin/env node
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_TARGET_URL = 'https://www.pipecat.ai/';
const DEFAULT_AGENT = '09-cascade-d';
const RESULT_SCHEMA_VERSION = 'pipecat-public-voice-smoke-result-v1';
const PUBLIC_REMOTE_AGENTS = [
  { id: '09-cascade-d', label: 'Soniox + OpenAI + Gradium' },
  { id: '10-gradium', label: 'Gradium + OpenAI + Gradium' },
  { id: '02-cascade-b', label: 'Soniox + OpenAI + Cartesia' },
  { id: '06-awsnovasonic', label: 'AWS Nova Sonic' },
  { id: '04-openai-realtime', label: 'OpenAI Realtime' },
  { id: '07-cascade-gemini-3', label: 'Deepgram + Gemini 3 Flash + Google TTS Chirp 3' },
  { id: '01-cascade-a', label: 'Deepgram + Google + Google TTS Chirp 3' },
  { id: '03-cascade-c', label: 'Speechmatics + AWS Nova Pro + ElevenLabs' },
  { id: '05-gemini-live', label: 'Gemini Live' },
];

function parseArgs(argv) {
  const args = {
    targetUrl: process.env.PIPECAT_PUBLIC_TARGET_URL || DEFAULT_TARGET_URL,
    agent: process.env.PIPECAT_PUBLIC_AGENT || DEFAULT_AGENT,
    artifactRoot: process.env.PIPECAT_PUBLIC_ARTIFACT_ROOT || 'artifacts/pipecat-public-voice-smoke',
    timeoutMs: Number(process.env.PIPECAT_PUBLIC_TIMEOUT_MS || 45000),
    headed: process.env.PIPECAT_PUBLIC_HEADED === '1',
    useRealMic: process.env.PIPECAT_PUBLIC_USE_REAL_MIC === '1',
  };

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--target-url') {
      args.targetUrl = requireValue(argv, ++index, value);
    } else if (value === '--agent') {
      args.agent = requireValue(argv, ++index, value);
    } else if (value === '--artifact-root') {
      args.artifactRoot = requireValue(argv, ++index, value);
    } else if (value === '--timeout-ms') {
      args.timeoutMs = Number(requireValue(argv, ++index, value));
    } else if (value === '--headed') {
      args.headed = true;
    } else if (value === '--use-real-mic') {
      args.useRealMic = true;
    } else if (value === '--help' || value === '-h') {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown option: ${value}`);
    }
  }

  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs < 5000) {
    throw new Error('--timeout-ms must be a number >= 5000.');
  }
  return args;
}

function requireValue(argv, index, flag) {
  const value = argv[index];
  if (!value || value.startsWith('--')) {
    throw new Error(`${flag} requires a value.`);
  }
  return value;
}

function printHelp() {
  console.log(`Run a real public Pipecat browser voice smoke.

Usage:
  npm run test:pipecat-public-voice-smoke -- [options]

Options:
  --target-url <url>       Public target URL. Default: ${DEFAULT_TARGET_URL}
  --agent <id>             Public demo agent id. Default: ${DEFAULT_AGENT}
  --artifact-root <path>   Artifact directory. Default: artifacts/pipecat-public-voice-smoke
  --timeout-ms <ms>        Overall wait budget. Default: 45000
  --headed                 Show Chromium during the run.
  --use-real-mic           Use the browser's real mic path instead of Chromium fake media.
`);
}

function nowIso() {
  return new Date().toISOString();
}

function relativeToRepo(value) {
  return path.relative(REPO_ROOT, value);
}

function redactedUrl(value) {
  try {
    const url = new URL(value);
    if (url.hostname.endsWith('.daily.co')) {
      return `${url.origin}/${url.pathname.split('/').filter(Boolean).map((part, index) => (
        index === 0 && part === 'rooms' ? part : '<redacted>'
      )).join('/')}`;
    }
    const redactedSearch = url.search ? '?<redacted>' : '';
    const redactedHash = url.hash ? '#<redacted>' : '';
    return `${url.origin}${url.pathname}${redactedSearch}${redactedHash}`;
  } catch {
    return '<unparseable-url>';
  }
}

function agentLabel(agent) {
  return PUBLIC_REMOTE_AGENTS.find((item) => item.id === agent || item.label === agent)?.label || agent;
}

function redactStartPayload(payload) {
  if (!payload || typeof payload !== 'object') {
    return null;
  }
  const dailyRoom = typeof payload.dailyRoom === 'string' ? payload.dailyRoom : null;
  return {
    dailyRoomOrigin: dailyRoom ? new URL(dailyRoom).origin : null,
    dailyRoomPathRedacted: dailyRoom ? redactedUrl(dailyRoom) : null,
    dailyToken: payload.dailyToken ? '<redacted>' : null,
    sessionIdPresent: Boolean(payload.sessionId),
  };
}

function baseResult(args, startedAt) {
  return {
    schema_version: RESULT_SCHEMA_VERSION,
    status: 'running',
    reason_code: null,
    reason: null,
    target: {
      id: 'pipecat-public-demo',
      url: redactedUrl(args.targetUrl),
      selected_agent: args.agent,
      kind: 'public_browser_voice_demo',
      execution: 'real_external_public_target',
    },
    tester: {
      id: 'cae_pipecat_public_browser_smoke',
      executor_id: 'playwright_chromium',
      browser: 'chromium',
      microphone_permission: args.useRealMic ? 'browser_permission_granted_for_real_mic' : 'browser_permission_granted_with_fake_media',
      media_source: args.useRealMic ? 'real_browser_microphone' : 'chromium_fake_media_stream',
    },
    provenance: {
      cae_path: 'scripts/pipecat_public_voice_smoke.mjs',
      fixture_backed: false,
      mock_execution: false,
      live_external_connection: true,
      saved_replay: false,
      tokens_redacted: true,
      device_ids_persisted: false,
    },
    timestamps: {
      started_at: startedAt,
      completed_at: null,
    },
    connection: {
      page_loaded: false,
      start_endpoint_seen: false,
      start_status: null,
      start_payload: null,
      daily_room_check_seen: false,
      ui_connected: false,
    },
    latency_metrics: {
      page_load_ms: null,
      connect_click_to_start_response_ms: null,
      connect_click_to_daily_room_check_ms: null,
      connect_click_to_transcript_ms: null,
      total_run_ms: null,
    },
    transcript: {
      text: '',
      artifact_path: null,
      source: 'public_page_transcript_dom',
      complete_as_observed: true,
    },
    network_events: [],
    console_events: [],
    artifacts: {},
  };
}

async function writeArtifacts(result, artifactRoot) {
  const root = path.resolve(REPO_ROOT, artifactRoot);
  const runId = `pipecat-public-${result.timestamps.started_at.replace(/[:.]/g, '').replace(/-/g, '')}`;
  const runDir = path.join(root, runId);
  await fs.mkdir(runDir, { recursive: true });

  const transcriptPath = path.join(runDir, 'transcript.txt');
  await fs.writeFile(transcriptPath, `${result.transcript.text || ''}\n`, 'utf8');
  result.transcript.artifact_path = relativeToRepo(transcriptPath);

  const resultPath = path.join(runDir, 'result.json');
  const latestPath = path.join(root, 'latest.json');
  result.artifacts = {
    result_json: relativeToRepo(resultPath),
    transcript_txt: relativeToRepo(transcriptPath),
    latest_json: relativeToRepo(latestPath),
  };
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  await fs.writeFile(resultPath, serialized, 'utf8');
  await fs.writeFile(latestPath, serialized, 'utf8');
  return resultPath;
}

function extractTranscript(bodyText) {
  const lines = bodyText.split('\n').map((line) => line.trim()).filter(Boolean);
  const transcriptMarker = 'Transcript appears here';
  if (lines.includes(transcriptMarker)) {
    return '';
  }
  const startIndex = lines.lastIndexOf('Disconnect');
  const endIndex = lines.findIndex((line, index) => index > startIndex && line === 'Browse community events');
  if (startIndex >= 0 && endIndex > startIndex + 1) {
    return lines
      .slice(startIndex + 1, endIndex)
      .filter((line) => !PUBLIC_REMOTE_AGENTS.some((agent) => agent.label === line))
      .filter((line) => line !== '\u2197')
      .join('\n')
      .trim();
  }
  return '';
}

async function selectAgent(page, agent) {
  const optionName = agentLabel(agent || DEFAULT_AGENT);
  const trigger = page.locator('button[role="combobox"]').first();
  if (!(await trigger.count())) {
    throw new Error('Pipecat public agent selector was not available.');
  }
  await trigger.click();
  const escaped = optionName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const option = page.getByRole('option', { name: new RegExp(escaped) });
  if (!(await option.count())) {
    await page.keyboard.press('Escape').catch(() => {});
    throw new Error(`Requested Pipecat public agent was not available: ${agent}`);
  }
  await option.first().click();
  await page.waitForTimeout(250);
  const selectedText = ((await trigger.textContent().catch(() => '')) || '').replace(/\s+/g, ' ').trim();
  if (!selectedText.includes(optionName)) {
    throw new Error(`Requested Pipecat public agent was not selected. Expected "${optionName}", observed "${selectedText || '<empty>'}".`);
  }
}

async function runSmoke(args) {
  const startedAt = nowIso();
  const startedMs = Date.now();
  const result = baseResult(args, startedAt);
  let browser;
  try {
    const launchArgs = args.useRealMic
      ? ['--autoplay-policy=no-user-gesture-required']
      : ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'];
    browser = await chromium.launch({ headless: !args.headed, args: launchArgs });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, permissions: ['microphone'] });
    await context.grantPermissions(['microphone'], { origin: new URL(args.targetUrl).origin });
    const page = await context.newPage();
    let clickMs = null;
    let transcriptFirstSeenMs = null;
    let botStoppedSpeaking = false;

    page.on('console', (message) => {
      const text = message.text();
      if (/error|warn|Daily|Pipecat|RTVI|connected|disconnect|transcription/i.test(text)) {
        result.console_events.push({
          type: message.type(),
          text: text.slice(0, 500),
          t_ms: Date.now() - startedMs,
        });
      }
      if (/bot-stopped-speaking/i.test(text)) {
        botStoppedSpeaking = true;
      }
    });

    page.on('request', (request) => {
      const url = request.url();
      if (/\/api\/start|daily\.co/i.test(url)) {
        result.network_events.push({
          phase: 'request',
          method: request.method(),
          url: redactedUrl(url),
          t_ms: Date.now() - startedMs,
        });
      }
    });

    page.on('response', async (response) => {
      const url = response.url();
      if (!/\/api\/start|daily\.co/i.test(url)) {
        return;
      }
      const event = {
        phase: 'response',
        status: response.status(),
        url: redactedUrl(url),
        t_ms: Date.now() - startedMs,
      };
      result.network_events.push(event);
      if (url.includes('/api/start')) {
        result.connection.start_endpoint_seen = true;
        result.connection.start_status = response.status();
        if (clickMs !== null) {
          result.latency_metrics.connect_click_to_start_response_ms = Date.now() - clickMs;
        }
        try {
          result.connection.start_payload = redactStartPayload(await response.json());
        } catch {
          result.connection.start_payload = null;
        }
      }
      if (url.includes('daily.co') && clickMs !== null) {
        result.connection.daily_room_check_seen = true;
        if (result.latency_metrics.connect_click_to_daily_room_check_ms === null) {
          result.latency_metrics.connect_click_to_daily_room_check_ms = Date.now() - clickMs;
        }
      }
    });

    const pageStartMs = Date.now();
    await page.goto(args.targetUrl, { waitUntil: 'networkidle', timeout: args.timeoutMs });
    result.connection.page_loaded = true;
    result.latency_metrics.page_load_ms = Date.now() - pageStartMs;

    const connectButton = page.getByText('Connect', { exact: true });
    if (!(await connectButton.count())) {
      result.status = 'blocked';
      result.reason_code = 'no_public_voice_endpoint';
      result.reason = 'The public Pipecat page loaded, but no Connect control was available.';
      return result;
    }

    await selectAgent(page, args.agent);
    clickMs = Date.now();
    await connectButton.first().click({ timeout: Math.min(args.timeoutMs, 15000) });

    const deadlineMs = startedMs + args.timeoutMs;
    let lastTranscript = '';
    let lastTranscriptChangeMs = Date.now();
    while (Date.now() < deadlineMs) {
      const bodyText = await page.locator('body').innerText({ timeout: 3000 });
      const transcript = extractTranscript(bodyText);
      result.connection.ui_connected = bodyText.includes('Disconnect');
      if (transcript) {
        result.transcript.text = transcript;
        transcriptFirstSeenMs = transcriptFirstSeenMs ?? Date.now();
        result.latency_metrics.connect_click_to_transcript_ms = transcriptFirstSeenMs - clickMs;
        if (transcript !== lastTranscript) {
          lastTranscript = transcript;
          lastTranscriptChangeMs = Date.now();
        }
        if (botStoppedSpeaking || Date.now() - lastTranscriptChangeMs >= 6000) {
          break;
        }
      }
      await page.waitForTimeout(500);
    }

    if (result.transcript.text) {
      result.status = 'pass';
      result.reason_code = null;
      result.reason = 'Public Pipecat demo connected and emitted transcript text.';
    } else if (result.connection.start_status && result.connection.start_status >= 400) {
      result.status = 'blocked';
      result.reason_code = 'target_changed';
      result.reason = `The public start endpoint returned HTTP ${result.connection.start_status}.`;
    } else if (result.connection.start_endpoint_seen && result.connection.ui_connected) {
      result.status = 'blocked';
      result.reason_code = 'transcript_unavailable';
      result.reason = 'The public Pipecat demo connected, but no transcript text appeared before timeout.';
    } else if (result.connection.start_endpoint_seen) {
      result.status = 'blocked';
      result.reason_code = 'connection_timeout';
      result.reason = 'The public start endpoint responded, but the browser did not reach a connected state before timeout.';
    } else {
      result.status = 'blocked';
      result.reason_code = 'no_public_voice_endpoint';
      result.reason = 'The public page did not expose a usable /api/start voice connection during this run.';
    }

    const disconnectButton = page.getByText('Disconnect', { exact: true });
    if (await disconnectButton.count()) {
      await disconnectButton.first().click({ timeout: 5000 }).catch(() => {});
    }
    return result;
  } catch (error) {
    result.status = 'blocked';
    result.reason_code = classifyError(error);
    result.reason = error instanceof Error ? error.message : String(error);
    return result;
  } finally {
    result.timestamps.completed_at = nowIso();
    result.latency_metrics.total_run_ms = Date.now() - startedMs;
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

function classifyError(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (/agent selector|agent was not available|agent was not selected/i.test(message)) {
    return 'target_changed';
  }
  if (/permission|microphone|media/i.test(message)) {
    return 'browser_permission_denied';
  }
  if (/timeout/i.test(message)) {
    return 'connection_timeout';
  }
  if (/net::|ECONN|ENOTFOUND|fetch|navigation/i.test(message)) {
    return 'target_unreachable';
  }
  return 'unsupported_media_path';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const result = await runSmoke(args);
  const resultPath = await writeArtifacts(result, args.artifactRoot);
  console.log(JSON.stringify({
    status: result.status,
    reason_code: result.reason_code,
    result_path: relativeToRepo(resultPath),
    transcript_path: result.transcript.artifact_path,
    latency_metrics: result.latency_metrics,
    connection: result.connection,
  }, null, 2));
  process.exitCode = result.status === 'pass' ? 0 : 2;
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
