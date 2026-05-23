import path from 'node:path';
import { defineConfig } from '@playwright/test';

const repoRoot = path.resolve(__dirname, '../..');
// Use a per-run high port block by default so Playwright does not accidentally
// attach to a stale local dev stack on the well-known manual-dev ports.
const runPortBase = String(30_000 + (process.pid % 2_000) * 10);
const port = process.env.PORT ?? process.env.PLAYWRIGHT_PORT ?? runPortBase;
const apiPort = process.env.API_PORT ?? process.env.PLAYWRIGHT_API_PORT ?? String(Number(runPortBase) + 1);
const pipecatPort = process.env.PIPECAT_PORT ?? process.env.PLAYWRIGHT_PIPECAT_PORT ?? String(Number(runPortBase) + 2);
const hostname = process.env.PLAYWRIGHT_HOSTNAME ?? '127.0.0.1';
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://${hostname}:${port}`;
const apiBaseURL = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? `http://${hostname}:${apiPort}`;
const pipecatBaseURL = process.env.PLAYWRIGHT_PIPECAT_BASE_URL ?? process.env.NEXT_PUBLIC_PIPECAT_SERVICE_URL ?? `http://${hostname}:${pipecatPort}`;
process.env.PLAYWRIGHT_API_BASE_URL ??= apiBaseURL;
process.env.PLAYWRIGHT_PIPECAT_BASE_URL ??= pipecatBaseURL;
const usingExternalBaseUrl = Boolean(process.env.PLAYWRIGHT_BASE_URL);
const shouldAttachToExistingServer = usingExternalBaseUrl
  || process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === '1';
const webServerCommand = process.env.PLAYWRIGHT_WEB_SERVER_COMMAND
  ?? [
    `WEB_PORT=${port}`,
    `PORT=${port}`,
    `API_PORT=${apiPort}`,
    `API_BASE_URL=${apiBaseURL}`,
    `PIPECAT_SERVICE_URL=${pipecatBaseURL}`,
    `NEXT_PUBLIC_API_BASE_URL=${apiBaseURL}`,
    `NEXT_PUBLIC_PIPECAT_SERVICE_URL=${pipecatBaseURL}`,
    `PIPECAT_PORT=${pipecatPort}`,
    'npm run dev',
  ].join(' ');

if (!process.env.PLAYWRIGHT_WEB_SERVER_COMMAND && process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === '1') {
  // No-op: keep explicit env path for external servers.
}

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  webServer: shouldAttachToExistingServer
    ? undefined
    : {
        command: webServerCommand,
        cwd: repoRoot,
        url: baseURL,
        // The default web server command starts the full local stack (API,
        // realtime, Pipecat, and web). Reusing an already-running web-only
        // process can leave API/Pipecat down and make voice-proof fail before
        // it reaches the product checks.
        reuseExistingServer: false,
        timeout: 120_000,
      },
  use: {
    baseURL,
    headless: true,
  },
});
