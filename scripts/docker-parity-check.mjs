import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.cwd();
const compose = readFileSync(join(root, 'docker-compose.yml'), 'utf8');
const envExample = readFileSync(join(root, '.env.example'), 'utf8');
const webDockerfile = readFileSync(join(root, 'apps/web/Dockerfile'), 'utf8');
const environmentDocs = readFileSync(join(root, 'docs/environment.md'), 'utf8');

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

function requireProfile(label, source, profile) {
  requireIncludes(label, source, 'profiles:');
  requireIncludes(label, source, `- ${profile}`);
}

for (const [name, dockerfile] of [
  ['seed', 'apps/api/Dockerfile'],
  ['api', 'apps/api/Dockerfile'],
  ['worker', 'apps/api/Dockerfile'],
  ['pipecat', 'apps/pipecat/Dockerfile'],
  ['web', 'apps/web/Dockerfile'],
]) {
  const block = serviceBlock(name);
  const imageName = ['seed', 'worker'].includes(name) ? 'api' : name;
  requireIncludes(`${name} service`, block, `image: conversation-agent-evals-${imageName}:latest`);
  requireIncludes(`${name} service`, block, 'build:');
  requireIncludes(`${name} service`, block, 'context: .');
  requireIncludes(`${name} service`, block, `dockerfile: ${dockerfile}`);
  requireIncludes(`${name} service`, block, 'env_file:');
  requireIncludes(`${name} service`, block, '- .env');
}

const db = serviceBlock('db');
requireIncludes('db service', db, 'image: postgres:16-alpine');
requireProfile('db service', db, 'persistence');
requireIncludes('db service', db, 'POSTGRES_DB: ${POSTGRES_DB:-conversation_agent_evals}');
requireIncludes('db service', db, '"${POSTGRES_PORT:-54329}:5432"');
requireIncludes('db service', db, 'postgres_data:/var/lib/postgresql/data');
requireIncludes('db service', db, 'pg_isready');

const seed = serviceBlock('seed');
requireProfile('seed service', seed, 'persistence');
requireIncludes('seed service', seed, 'DATABASE_URL: postgresql://${POSTGRES_USER:-cae}:${POSTGRES_PASSWORD:-cae_local_password}@db:5432/${POSTGRES_DB:-conversation_agent_evals}');
requireIncludes('seed service', seed, 'condition: service_healthy');
requireIncludes('seed service', seed, 'command: ["python", "-m", "app.seed"]');
requireIncludes('seed service', seed, 'restart: "no"');

const api = serviceBlock('api');
requireIncludes('api service', api, '"${API_PORT:-8025}:8000"');
requireIncludes('api service', api, 'PORT: 8000');
requireIncludes('api service', api, 'DATABASE_URL: ${COMPOSE_DATABASE_URL:-sqlite:////workspace/storage/conversation_agent_evals.db}');
requireNotIncludes('api service', api, 'profiles:');
requireIncludes('api service', api, 'PIPECAT_SERVICE_URL: http://pipecat:8110');
requireIncludes('api service', api, 'condition: service_completed_successfully');
requireIncludes('api service', api, 'required: false');
requireIncludes('api service', api, 'http://localhost:8000/health');
requireNotIncludes('api service', api, './apps/api:/workspace/apps/api');
requireNotIncludes('api service', api, './apps/api/sales_presenter.db:/workspace/apps/api/sales_presenter.db');

const worker = serviceBlock('worker');
requireProfile('worker service', worker, 'persistence');
requireIncludes('worker service', worker, 'DATABASE_URL: postgresql://${POSTGRES_USER:-cae}:${POSTGRES_PASSWORD:-cae_local_password}@db:5432/${POSTGRES_DB:-conversation_agent_evals}');
requireIncludes('worker service', worker, 'WORKER_POLL_INTERVAL_SECONDS: ${WORKER_POLL_INTERVAL_SECONDS:-30}');
requireIncludes('worker service', worker, 'command: ["python", "-m", "app.worker"]');
requireIncludes('worker service', worker, 'condition: service_completed_successfully');
requireIncludes('worker service', worker, 'conversation-agent-evals-worker-health');
requireNotIncludes('worker service', worker, './apps/api:/workspace/apps/api');

const pipecat = serviceBlock('pipecat');
requireProfile('pipecat service', pipecat, 'voice');
requireIncludes('pipecat service', pipecat, '"${PIPECAT_PORT:-8110}:8110"');
requireIncludes('pipecat service', pipecat, 'condition: service_healthy');
requireIncludes('pipecat service', pipecat, 'http://localhost:8110/health');
requireIncludes('pipecat service', pipecat, 'RTC_ASR_BASE_URL: ${RTC_ASR_BASE_URL:-}');
requireIncludes('pipecat service', pipecat, 'RTC_ASR_HEALTH_PATH: ${RTC_ASR_HEALTH_PATH:-/health}');
requireIncludes('pipecat service', pipecat, 'RTC_ASR_STREAM_PATH: ${RTC_ASR_STREAM_PATH:-/v1/stt/stream}');
requireNotIncludes('pipecat service', pipecat, './apps/pipecat:/app');

const web = serviceBlock('web');
requireNotIncludes('web service', web, 'profiles:');
requireIncludes('web service', web, '"${PORT:-3012}:3000"');
requireIncludes('web service', web, 'args:');
requireIncludes('web service', web, 'API_BASE_URL: http://api:8000');
requireIncludes('web service', web, 'PIPECAT_SERVICE_URL: http://pipecat:8110');
requireIncludes('web service', web, 'NEXT_PUBLIC_API_BASE_URL: http://localhost:${API_PORT:-8025}');
requireIncludes('web service', web, 'NEXT_PUBLIC_PIPECAT_SERVICE_URL: http://localhost:${PIPECAT_PORT:-8110}');
requireIncludes('web service', web, 'APP_ENV: ${APP_ENV:-development}');
requireIncludes('web service', web, 'PRODUCTION: ${PRODUCTION:-false}');
requireNotIncludes('web service', web, 'worker:');
requireIncludes('web service', web, 'pipecat:');
requireIncludes('web service', web, 'condition: service_healthy');
requireIncludes('web service', web, 'required: false');
requireNotIncludes('web service', web, './apps/web:/app/apps/web');

for (const envName of [
  'PORT=3012',
  'API_PORT=8025',
  'PIPECAT_PORT=8110',
  'APP_ENV=development',
  'PRODUCTION=false',
  'PLAYWRIGHT_BASE_URL=http://127.0.0.1:3012',
  'PLAYWRIGHT_REUSE_EXISTING_SERVER=1',
]) {
  requireIncludes('.env.example', envExample, envName);
}

for (const advancedEnvName of [
  'OPENAI_API_KEY=',
  'OPENAI_REALTIME_MODEL=gpt-realtime-mini',
  'OPENAI_RESPONSES_MODEL=gpt-4.1-mini',
  'POSTGRES_DB=conversation_agent_evals',
  'COMPOSE_DATABASE_URL=sqlite:////workspace/storage/conversation_agent_evals.db',
  'RTC_ASR_BASE_URL=http://localhost:8000',
  'STRIPE_SECRET_KEY=',
  'FIREBASE_PROJECT_ID=',
]) {
  requireIncludes('docs/environment.md', environmentDocs, advancedEnvName);
  requireNotIncludes('.env.example first-run env', envExample, advancedEnvName);
}

requireIncludes('api Dockerfile', readFileSync(join(root, 'apps/api/Dockerfile'), 'utf8'), 'ENV PYTHONPATH=/workspace/apps/api');

requireIncludes('web Dockerfile', webDockerfile, 'RUN cd /app/apps/web && npm run build');
requireIncludes('web Dockerfile', webDockerfile, 'ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8025');
requireIncludes('web Dockerfile', webDockerfile, 'ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL');
requireIncludes('web Dockerfile', webDockerfile, 'npm run start -- --hostname 0.0.0.0 --port 3000');
requireNotIncludes('web Dockerfile', webDockerfile, 'rm -rf .next && npm run build && npm run start');

requireIncludes('compose volumes', compose, 'postgres_data:');

if (failures.length > 0) {
  console.error('Docker parity check failed:');
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log('Docker parity check passed.');
