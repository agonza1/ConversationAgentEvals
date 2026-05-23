const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const net = require('node:net');

const root = path.resolve(__dirname, '..');
const envPath = path.join(root, '.env');
const apiVenvPython = path.join(root, 'apps', 'api', '.venv', 'bin', 'python');

function loadDotEnv(filePath) {
  if (!fs.existsSync(filePath)) return;
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const separatorIndex = trimmed.indexOf('=');
    if (separatorIndex <= 0) continue;
    const key = trimmed.slice(0, separatorIndex).trim();
    const value = trimmed.slice(separatorIndex + 1).trim();
    if (!(key in process.env)) {
      process.env[key] = value.replace(/^['\"]|['\"]$/g, '');
    }
  }
}

loadDotEnv(envPath);

if (!fs.existsSync(apiVenvPython)) {
  console.error('Missing apps/api/.venv. Run `npm run setup` first.');
  process.exit(1);
}

for (const key of ['API_BASE_URL', 'PIPECAT_SERVICE_URL', 'NEXT_PUBLIC_API_BASE_URL', 'NEXT_PUBLIC_PIPECAT_SERVICE_URL']) {
  delete process.env[key];
}

const children = [];

function isPortFree(port, host = '0.0.0.0') {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close(() => resolve(true));
    });
    server.listen({ port, host, exclusive: true });
  });
}

async function findFreePort(preferredPort, attempts = 200, reservedPorts = new Set()) {
  for (let offset = 0; offset < attempts; offset += 1) {
    const port = preferredPort + offset;
    if (reservedPorts.has(port)) continue;
    // eslint-disable-next-line no-await-in-loop
    if (await isPortFree(port)) return port;
  }
  throw new Error(`No free port found starting at ${preferredPort}`);
}

async function resolvePort({ name, envKey, preferredPort, attempts = 400, reservedPorts }) {
  const configuredPort = Number(process.env[envKey]);
  const requestedPort = Number.isFinite(configuredPort) && configuredPort > 0 ? configuredPort : preferredPort;

  if (!reservedPorts.has(requestedPort) && await isPortFree(requestedPort)) {
    return requestedPort;
  }

  const fallbackPort = await findFreePort(requestedPort + 1, attempts, reservedPorts);
  const source = configuredPort ? `${envKey}=${configuredPort}` : `preferred ${preferredPort}`;
  console.warn(`${name} port ${requestedPort} (${source}) is busy; using ${fallbackPort} for this dev run.`);
  return fallbackPort;
}

function start(name, command, args, cwd, extraEnv = {}) {
  const child = spawn(command, args, {
    cwd,
    stdio: 'inherit',
    env: { ...process.env, ...extraEnv },
  });

  child.on('exit', (code, signal) => {
    if (signal) {
      console.log(`${name} exited from signal ${signal}`);
    } else if (code && code !== 0) {
      console.error(`${name} exited with code ${code}`);
      shutdown(code);
    }
  });

  children.push(child);
}

function shutdown(code = 0) {
  for (const child of children) {
    if (!child.killed) {
      child.kill('SIGTERM');
    }
  }
  process.exit(code);
}

async function main() {
  const reservedPorts = new Set();

  const apiPort = await resolvePort({
    name: 'API',
    envKey: 'API_PORT',
    preferredPort: 18000,
    reservedPorts,
  });
  reservedPorts.add(apiPort);

  const pipecatPort = await resolvePort({
    name: 'Pipecat',
    envKey: 'PIPECAT_PORT',
    preferredPort: 8110,
    reservedPorts,
  });
  reservedPorts.add(pipecatPort);

  const webPort = await resolvePort({
    name: 'Web',
    envKey: 'WEB_PORT',
    preferredPort: 13000,
    reservedPorts,
  });
  reservedPorts.add(webPort);

  const apiBaseUrl = process.env.API_BASE_URL || `http://localhost:${apiPort}`;
  const pipecatServiceUrl = process.env.PIPECAT_SERVICE_URL || `http://localhost:${pipecatPort}`;

  process.on('SIGINT', () => shutdown(0));
  process.on('SIGTERM', () => shutdown(0));

  console.log('\n🚀 LiveSalesAIPresenter dev stack');
  console.log(`   API      ${apiBaseUrl}`);
  console.log(`   Web      http://localhost:${webPort}`);
  console.log(`   Pipecat  ${pipecatServiceUrl}`);
  console.log(`\n👉 Open the operator UI at http://localhost:${webPort}`);
  console.log('   Use the printed URL above if default ports were busy.\n');

  start('api', 'npm', ['run', 'dev:api'], root, {
    PORT: String(apiPort),
    API_PORT: String(apiPort),
    API_BASE_URL: apiBaseUrl,
    PIPECAT_SERVICE_URL: pipecatServiceUrl,
  });

  start('pipecat', 'npm', ['run', 'dev:pipecat'], root, {
    PIPECAT_PORT: String(pipecatPort),
    API_BASE_URL: apiBaseUrl,
    PIPECAT_SERVICE_URL: pipecatServiceUrl,
  });

  start('web', 'npm', ['run', 'dev:web'], root, {
    WEB_PORT: String(webPort),
    PORT: String(webPort),
    NEXT_PUBLIC_API_BASE_URL: apiBaseUrl,
    NEXT_PUBLIC_PIPECAT_SERVICE_URL: pipecatServiceUrl,
  });
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
