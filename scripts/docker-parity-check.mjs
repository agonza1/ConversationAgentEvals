import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.cwd();
const compose = readFileSync(join(root, 'docker-compose.yml'), 'utf8');
const envExample = readFileSync(join(root, '.env.example'), 'utf8');
const webDockerfile = readFileSync(join(root, 'apps/web/Dockerfile'), 'utf8');

const failures = [];

function fail(message) {
  failures.push(message);
}

function serviceBlock(name) {
  const lines = compose.split(/\r?\n/);
  const start = lines.findIndex((line) => line === `  ${name}:`);
  if (start === -1) {
    fail(`Missing compose service: ${name}`);
    return '';
  }

  const block = [];
  for (let index = start; index < lines.length; index += 1) {
    const line = lines[index];
    if (index !== start && /^  [A-Za-z0-9_-]+:\s*$/.test(line)) {
      break;
    }
    block.push(line);
  }
  return block.join('\n');
}

function requireIncludes(label, source, expected) {
  if (!source.includes(expected)) {
    fail(`${label} must include "${expected}"`);
  }
}

function requireNotIncludes(label, source, unexpected) {
  if (source.includes(unexpected)) {
    fail(`${label} must not include "${unexpected}"`);
  }
}

for (const [name, dockerfile] of [
  ['api', 'apps/api/Dockerfile'],
  ['pipecat', 'apps/pipecat/Dockerfile'],
  ['web', 'apps/web/Dockerfile'],
]) {
  const block = serviceBlock(name);
  requireIncludes(`${name} service`, block, `image: conversation-agent-evals-${name}:latest`);
  requireIncludes(`${name} service`, block, 'build:');
  requireIncludes(`${name} service`, block, 'context: .');
  requireIncludes(`${name} service`, block, `dockerfile: ${dockerfile}`);
  requireIncludes(`${name} service`, block, 'env_file:');
  requireIncludes(`${name} service`, block, '- .env');
}

const api = serviceBlock('api');
requireIncludes('api service', api, '"${API_PORT:-8025}:8000"');
requireIncludes('api service', api, 'PIPECAT_SERVICE_URL: http://pipecat:8110');
requireIncludes('api service', api, 'http://localhost:8000/health');
requireNotIncludes('api service', api, './apps/api:/workspace/apps/api');

const pipecat = serviceBlock('pipecat');
requireIncludes('pipecat service', pipecat, '"${PIPECAT_PORT:-8110}:8110"');
requireIncludes('pipecat service', pipecat, 'condition: service_healthy');
requireIncludes('pipecat service', pipecat, 'http://localhost:8110/health');
requireNotIncludes('pipecat service', pipecat, './apps/pipecat:/app');

const web = serviceBlock('web');
requireIncludes('web service', web, '"${PORT:-3012}:3000"');
requireIncludes('web service', web, 'args:');
requireIncludes('web service', web, 'API_BASE_URL: http://api:8000');
requireIncludes('web service', web, 'PIPECAT_SERVICE_URL: http://pipecat:8110');
requireIncludes('web service', web, 'NEXT_PUBLIC_API_BASE_URL: http://localhost:${API_PORT:-8025}');
requireIncludes('web service', web, 'NEXT_PUBLIC_PIPECAT_SERVICE_URL: http://localhost:${PIPECAT_PORT:-8110}');
requireIncludes('web service', web, 'APP_ENV: ${APP_ENV:-development}');
requireIncludes('web service', web, 'PRODUCTION: ${PRODUCTION:-false}');
requireNotIncludes('web service', web, './apps/web:/app/apps/web');

for (const envName of [
  'PORT=3012',
  'API_PORT=8025',
  'PIPECAT_PORT=8110',
  'API_BASE_URL=http://localhost:8025',
  'PIPECAT_SERVICE_URL=http://localhost:8110',
  'NEXT_PUBLIC_API_BASE_URL=http://localhost:8025',
  'NEXT_PUBLIC_PIPECAT_SERVICE_URL=http://localhost:8110',
]) {
  requireIncludes('.env.example', envExample, envName);
}

requireIncludes('web Dockerfile', webDockerfile, 'RUN cd /app/apps/web && npm run build');
requireIncludes('web Dockerfile', webDockerfile, 'ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8025');
requireIncludes('web Dockerfile', webDockerfile, 'ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL');
requireIncludes('web Dockerfile', webDockerfile, 'npm run start -- --hostname 0.0.0.0 --port 3000');
requireNotIncludes('web Dockerfile', webDockerfile, 'rm -rf .next && npm run build && npm run start');

if (failures.length > 0) {
  console.error('Docker parity check failed:');
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log('Docker parity check passed.');
