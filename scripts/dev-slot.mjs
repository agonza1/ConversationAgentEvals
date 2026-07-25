#!/usr/bin/env node

import { spawn } from 'node:child_process';

const BASE_PORTS = {
  web: 3012,
  api: 8025,
  pipecat: 8110,
};
const SLOT_OFFSET = 100;

function usage() {
  console.log('Usage: npm run dev:slot -- <slot> [--print]');
  console.log('Example: npm run dev:slot -- 1');
}

const args = process.argv.slice(2);
const printOnly = args.includes('--print');
const positional = args.filter((arg) => arg !== '--print');

if (positional.includes('--help') || positional.includes('-h')) {
  usage();
  process.exit(0);
}

if (positional.length !== 1 || !/^\d+$/.test(positional[0])) {
  usage();
  process.exit(2);
}

const slot = Number(positional[0]);
if (!Number.isSafeInteger(slot) || slot > 99) {
  console.error('Slot must be a whole number from 0 through 99.');
  process.exit(2);
}

const offset = slot * SLOT_OFFSET;
const ports = {
  web: BASE_PORTS.web + offset,
  api: BASE_PORTS.api + offset,
  pipecat: BASE_PORTS.pipecat + offset,
};
const webUrl = `http://127.0.0.1:${ports.web}`;

console.log(`CAE development slot ${slot}`);
console.log(`  Web      ${webUrl}`);
console.log(`  API      http://127.0.0.1:${ports.api}`);
console.log(`  Pipecat  http://127.0.0.1:${ports.pipecat}`);

if (printOnly) {
  process.exit(0);
}

const command = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const child = spawn(command, ['run', 'dev'], {
  stdio: 'inherit',
  env: {
    ...process.env,
    PORT: String(ports.web),
    WEB_PORT: String(ports.web),
    API_PORT: String(ports.api),
    PIPECAT_PORT: String(ports.pipecat),
    PLAYWRIGHT_BASE_URL: webUrl,
    PLAYWRIGHT_REUSE_EXISTING_SERVER: '1',
  },
});

child.on('error', (error) => {
  console.error(`Failed to start CAE development slot ${slot}: ${error.message}`);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
