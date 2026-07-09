import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.cwd();
const envPath = join(root, '.env');
const envExamplePath = join(root, '.env.example');
const allowExample = process.env.CHECK_ENV_ALLOW_EXAMPLE === '1';

const required = [
  { name: 'PORT', kind: 'port', description: 'web app port' },
  { name: 'API_PORT', kind: 'port', description: 'FastAPI port' },
  { name: 'PIPECAT_PORT', kind: 'port', description: 'Pipecat service port' },
  { name: 'APP_ENV', kind: 'string', description: 'runtime environment label' },
  { name: 'PRODUCTION', kind: 'boolean', description: 'testing-controls visibility flag' },
];

function parseDotEnv(filePath) {
  const values = new Map();
  if (!existsSync(filePath)) return values;

  for (const rawLine of readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const separatorIndex = line.indexOf('=');
    if (separatorIndex <= 0) continue;
    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1).trim().replace(/^["']|["']$/g, '');
    values.set(key, value);
  }

  return values;
}

function isValidPort(value) {
  if (!/^\d+$/.test(value)) return false;
  const port = Number(value);
  return Number.isInteger(port) && port > 0 && port <= 65535;
}

function validateValue(spec, value) {
  if (value === undefined || value === null || value === '') {
    return `Missing required variable ${spec.name} (${spec.description}).`;
  }

  if (spec.kind === 'port' && !isValidPort(value)) {
    return `${spec.name} must be a TCP port between 1 and 65535; got ${JSON.stringify(value)}.`;
  }

  if (spec.kind === 'boolean' && !['true', 'false'].includes(value.toLowerCase())) {
    return `${spec.name} must be true or false; got ${JSON.stringify(value)}.`;
  }

  return null;
}

const failures = [];
const envExists = existsSync(envPath);
const envExampleExists = existsSync(envExamplePath);

if (!envExists && !allowExample) {
  failures.push('Missing .env at ' + envPath + '. Create it with: cp .env.example .env');
}

if (!envExampleExists) {
  failures.push('Missing .env.example; restore it before onboarding a local demo.');
}

const fileValues = parseDotEnv(envExists ? envPath : envExamplePath);
const values = new Map(fileValues);
for (const [key, value] of Object.entries(process.env)) {
  if (value !== undefined) values.set(key, value);
}

for (const spec of required) {
  const failure = validateValue(spec, values.get(spec.name));
  if (failure) failures.push(failure);
}

const usedPorts = new Map();
for (const spec of required.filter((item) => item.kind === 'port')) {
  const value = values.get(spec.name);
  if (!value || !isValidPort(value)) continue;
  const existing = usedPorts.get(value);
  if (existing) {
    failures.push(`${spec.name} and ${existing} both use port ${value}; choose distinct ports in .env.`);
  } else {
    usedPorts.set(value, spec.name);
  }
}

if (failures.length > 0) {
  console.error('Environment check failed for the minimal local demo:');
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  console.error('Required variables: PORT, API_PORT, PIPECAT_PORT, APP_ENV, PRODUCTION.');
  console.error('Optional and advanced variables are documented in docs/environment.md.');
  process.exit(1);
}

const checkedFile = envExists ? '.env' : '.env.example';
console.log('Environment check passed for the minimal local demo using ' + checkedFile + '.');
