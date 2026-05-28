#!/usr/bin/env node
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const mode = process.env.API_TEST_MODE || 'auto';
const localPython = 'apps/api/.venv/bin/python';

function commandAvailable(command, args) {
  const result = spawnSync(command, args, { stdio: 'ignore' });
  return result.status === 0;
}

function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', env: process.env });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

function runDockerTests() {
  run('docker', ['build', '-t', 'conversation-agent-evals-api:test', '-f', 'apps/api/Dockerfile', '.']);
  run('docker', [
    'run',
    '--rm',
    'conversation-agent-evals-api:test',
    'python',
    '-m',
    'pytest',
    '/workspace/apps/api/tests',
    '-q',
  ]);
}

function runLocalTests() {
  if (!existsSync(localPython)) {
    console.error('Missing apps/api/.venv. Install Docker for hermetic API tests, or run npm run setup for local-only iteration.');
    process.exit(1);
  }
  run(localPython, ['-m', 'pytest', 'apps/api/tests', '-q']);
}

if (mode === 'docker') {
  if (!commandAvailable('docker', ['--version'])) {
    console.error('Docker is required for API_TEST_MODE=docker.');
    process.exit(1);
  }
  runDockerTests();
} else if (mode === 'local') {
  runLocalTests();
} else if (mode === 'auto') {
  if (commandAvailable('docker', ['--version'])) {
    runDockerTests();
  } else {
    console.error('Docker is not available; falling back to existing local API venv.');
    runLocalTests();
  }
} else {
  console.error(`Unsupported API_TEST_MODE: ${mode}`);
  process.exit(1);
}
