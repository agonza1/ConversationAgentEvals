const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const ts = require('typescript');

const endpoint = 'https://api.poc.app.waylovoice.ai';
const agent = 'b722ea62-524a-4664-8702-f38f2bed5cbe';
const workspace = '11111111-1111-4111-8111-111111111111';
const tenant = '22222222-2222-4222-8222-222222222222';
const compiled = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../lib/wayloConnection.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function memoryClient() {
  let now = Date.parse('2026-10-10T12:00:00Z');
  let nextTimer = 0;
  const timers = new Map();
  const calls = [];
  const microtasks = [];
  const window = { location: { hostname: '127.0.0.1', origin: 'http://127.0.0.1:3012', search: '' } };
  for (const name of ['localStorage', 'sessionStorage']) Object.defineProperty(window, name, {
    get() { throw new Error('Waylo must not access browser storage'); },
  });
  let handler = (url) => ({ status: 200, body: url.endsWith('/config') ? { endpoint_url: endpoint } : {
    connected: true, session_proof: 'fixture-private-proof', endpoint_url: endpoint,
    workspace_id: workspace, waylo_agent_id: agent, agent_name: 'Mike fixture',
    expires_at: new Date(now + 300_000).toISOString(),
  } });
  const fetch = async (url, init) => {
    calls.push({ url, init });
    const result = await handler(url, init);
    return { ok: result.status >= 200 && result.status < 300, status: result.status, json: async () => result.body };
  };
  class Clock extends Date { static now() { return now; } }
  const module = { exports: {} };
  new Function('module', 'exports', 'window', 'fetch', 'setTimeout', 'clearTimeout', 'Date', 'queueMicrotask', compiled)(
    module, module.exports, window, fetch,
    (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id; },
    (id) => timers.delete(id), Clock, (fn) => microtasks.push(fn),
  );
  return {
    ...module.exports, calls, window, setHandler(next) { handler = next; },
    now: () => now, advance(ms) { now += ms; for (const { fn, at } of [...timers.values()]) if (at <= now) fn(); },
    flushMicrotasks() { for (const fn of microtasks.splice(0)) fn(); },
  };
}

const login = (overrides = {}) => ({ email: 'fixture@example.test', password: 'synthetic-password-not-a-real-secret',
  waylo_agent_id: agent, expected_endpoint_url: endpoint, confirm: true, ...overrides });
const target = (overrides = {}) => ({ endpoint_url: endpoint, workspace_id: workspace,
  waylo_agent_id: agent, auth_type: 'waylo_browser_session', ...overrides });

test('normal connection exposes safe status only and sends proof solely in same-origin headers', async () => {
  const client = memoryClient();
  assert.equal(await client.loadWayloConnectionConfig(), endpoint);
  const info = await client.connectWaylo(login());
  assert.equal(info.agent_name, 'Mike fixture');
  assert.ok(!JSON.stringify(info).includes('fixture-private-proof'));
  assert.ok(!JSON.stringify(client.getWayloConnection()).includes('password'));
  assert.deepEqual(client.wayloRequestHeaders(target(), '/api/execution/runs'), {
    'X-CAE-Waylo-Control': '1', 'X-CAE-Waylo-Session': 'fixture-private-proof',
  });
  for (const call of client.calls) assert.equal(call.init.credentials, 'omit');
  assert.equal(JSON.parse(client.calls[1].init.body).expected_endpoint_url, endpoint);
  assert.deepEqual(client.wayloRequestHeaders(target(), 'https://other.example.test/api/execution/runs'), {});
  assert.deepEqual(client.wayloRequestHeaders(target({ workspace_id: tenant })), {});
  assert.deepEqual(client.wayloRequestHeaders(target({ auth_type: 'bearer_secret' })), {});
});

test('operator-configured destination and explicit admin tenant support canonical uppercase UUID input', async () => {
  const client = memoryClient();
  const configured = 'https://waylo.fixture.test';
  client.setHandler(() => ({ status: 200, body: {
    connected: true, session_proof: 'admin-private-proof', endpoint_url: `${configured}/admin/tenants/${tenant}`,
    workspace_id: workspace, waylo_agent_id: agent, agent_name: 'Mike fixture',
    expires_at: new Date(client.now() + 90_000).toISOString(),
  } }));
  const info = await client.connectWaylo(login({ expected_endpoint_url: configured, tenant_id: tenant.toUpperCase(), waylo_agent_id: agent.toUpperCase() }));
  assert.equal(info.endpoint_url, `${configured}/admin/tenants/${tenant}`);
  assert.equal(client.wayloRequestHeaders(target({ endpoint_url: info.endpoint_url }))['X-CAE-Waylo-Session'], 'admin-private-proof');
});

test('consent retains exact configured HTTPS spelling while grant comparison accepts canonical default port', async () => {
  const client = memoryClient();
  const configured = 'https://API.POC.APP.WAYLOVOICE.AI:443';
  client.setHandler((url) => ({ status: 200, body: url.endsWith('/config') ? { endpoint_url: configured } : {
    connected: true, session_proof: 'fixture-private-proof', endpoint_url: endpoint,
    workspace_id: workspace, waylo_agent_id: agent, expires_at: new Date(client.now() + 90_000).toISOString(),
  } }));
  assert.equal(await client.loadWayloConnectionConfig(), configured);
  await client.connectWaylo(login({ expected_endpoint_url: configured }));
  assert.equal(JSON.parse(client.calls.at(-1).init.body).expected_endpoint_url, configured);
  assert.equal(client.wayloRequestHeaders(target({ endpoint_url: configured }))['X-CAE-Waylo-Session'], 'fixture-private-proof');
});

test('failed relogin preserves the old grant; successful switch includes old proof and replaces it', async () => {
  const client = memoryClient();
  await client.connectWaylo(login());
  client.setHandler(() => ({ status: 400, body: { detail: 'Synthetic sign-in failure.' } }));
  await assert.rejects(client.connectWaylo(login()), /Synthetic sign-in failure/);
  assert.equal(client.wayloRequestHeaders(target())['X-CAE-Waylo-Session'], 'fixture-private-proof');
  client.setHandler(() => ({ status: 200, body: {
    connected: true, session_proof: 'new-private-proof', endpoint_url: endpoint,
    workspace_id: tenant, waylo_agent_id: agent, expires_at: new Date(client.now() + 300_000).toISOString(),
  } }));
  await client.connectWaylo(login());
  assert.equal(client.calls.at(-1).init.headers['X-CAE-Waylo-Session'], 'fixture-private-proof');
  assert.deepEqual(client.wayloRequestHeaders(target()), {});
  assert.equal(client.wayloRequestHeaders(target({ workspace_id: tenant }))['X-CAE-Waylo-Session'], 'new-private-proof');
});

test('failed disconnect retains proof for retry and never claims revocation', async () => {
  const client = memoryClient();
  await client.connectWaylo(login());
  client.setHandler(() => ({ status: 503, body: {} }));
  await assert.rejects(client.disconnectWaylo(target()), /remains available to retry/);
  assert.equal(client.wayloRequestHeaders(target())['X-CAE-Waylo-Session'], 'fixture-private-proof');
  client.setHandler(() => ({ status: 200, body: { connected: false } }));
  await client.disconnectWaylo(target());
  assert.equal(client.getWayloConnection(target()), null);
  assert.equal(JSON.parse(client.calls.at(-1).init.body).session_proof, undefined);
});

test('expiry is capped at fifteen minutes and a new page/module cannot recover proof', async () => {
  const client = memoryClient();
  client.setHandler(() => ({ status: 200, body: {
    connected: true, session_proof: 'fixture-private-proof', endpoint_url: endpoint,
    workspace_id: workspace, waylo_agent_id: agent, expires_at: new Date(client.now() + 3_600_000).toISOString(),
  } }));
  const info = await client.connectWaylo(login());
  assert.equal(Date.parse(info.expires_at) - client.now(), 900_000);
  assert.equal(memoryClient().getWayloConnection(target()), null);
  client.advance(900_001);
  assert.deepEqual(client.wayloRequestHeaders(target()), {});
  assert.equal(client.getWayloConnection(target()), null);
});

test('server expiry clears local proof; configuration failure cannot send credentials', async () => {
  const client = memoryClient();
  client.setHandler(() => ({ status: 503, body: {} }));
  await assert.rejects(client.loadWayloConnectionConfig(), /No credentials were sent/);
  assert.equal(client.calls[0].init.body, undefined);
  client.setHandler(() => ({ status: 200, body: {
    connected: true, session_proof: 'fixture-private-proof', endpoint_url: endpoint,
    workspace_id: workspace, waylo_agent_id: agent, expires_at: new Date(client.now() + 300_000).toISOString(),
  } }));
  await client.connectWaylo(login());
  client.setHandler(() => ({ status: 401, body: {} }));
  assert.equal(await client.refreshWayloConnection(target()), null);
  assert.deepEqual(client.wayloRequestHeaders(target()), {});
});

test('cross-origin API overrides never receive a temporary proof or enable local login', async () => {
  const client = memoryClient();
  await client.connectWaylo(login());
  client.window.location.search = '?api_base=https://remote.example.test';
  assert.equal(client.isWayloLocalControlPage(), false);
  assert.deepEqual(client.wayloRequestHeaders(), {});
  await assert.rejects(client.connectWaylo(login()), /only on local CAE/);
  assert.equal(client.calls.length, 1);
});
