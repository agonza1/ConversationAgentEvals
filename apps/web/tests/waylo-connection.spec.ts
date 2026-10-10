import { expect, test, type Page } from '@playwright/test';

const endpoint = 'https://api.poc.app.waylovoice.ai';
const agentId = 'b722ea62-524a-4664-8702-f38f2bed5cbe';
const workspaceId = '11111111-1111-4111-8111-111111111111';
const tenantId = '22222222-2222-4222-8222-222222222222';
const proof = 'fixture-private-proof-not-a-provider-token';

async function fixtureApis(page: Page, admin = false) {
  const agents: Record<string, unknown>[] = [];
  const requests: Array<{ path: string; method: string; headers: Record<string, string>; body?: Record<string, unknown> }> = [];
  let allowLogin: (() => void) | undefined;
  const loginGate = new Promise<void>((resolve) => { allowLogin = resolve; });
  const status = () => ({ connected: true, expires_at: new Date(Date.now() + 120_000).toISOString(),
    endpoint_url: admin ? `${endpoint}/admin/tenants/${tenantId}` : endpoint,
    workspace_id: workspaceId, waylo_agent_id: agentId, agent_name: 'Mike fixture' });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const body = ['POST', 'PATCH'].includes(method) ? request.postDataJSON() as Record<string, unknown> : undefined;
    requests.push({ path, method, headers: request.headers(), body });
    let value: unknown = {};
    if (path === '/api/waylo/connection/config') value = { endpoint_url: endpoint };
    else if (path === '/api/waylo/connection/connect') { await loginGate; value = { ...status(), session_proof: proof }; }
    else if (path === '/api/waylo/connection/status') value = status();
    else if (path === '/api/waylo/connection/disconnect') value = { connected: false };
    else if (path === '/api/agents' && method === 'POST') { const agent = { id: 'waylo-memory-fixture', ...body, metadata: {} }; agents.push(agent); value = agent; }
    else if (path === '/api/agents') value = { agents };
    else if (path.endsWith('/capture')) value = { vcon: { vcon: '0.4.0', uuid: workspaceId, parties: [], dialog: [] } };
    else if (path === '/api/benchmarks/suites') value = [{ id: 'waylo-mike-notes', title: 'Waylo Mike notes', scenarios: [{ id: 'mike-five-bags', title: 'Five bags, not catalog pack size', caller_steps: ['Please note five bags of white potatoes.', 'Read back my notes.'] }] }];
    else if (path.endsWith('/providers/openai/status')) value = { status: 'disconnected', provider: 'openai_codex' };
    else if (path === '/api/execution/runs' && method === 'POST') value = { execution_run_id: 'exec-waylo-memory-fixture', status: 'completed', mode: 'pipecat_webrtc', conversations: [] };
    else if (path.endsWith('/runs') || path.endsWith('/projects') || path.endsWith('/audit-events') || path.includes('/suite-runs')) value = [];
    else if (path.includes('/regression-summary')) value = null;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) });
  });
  return { requests, allowLogin: () => allowLogin?.() };
}

async function openTemporaryForm(page: Page) {
  await page.goto('/targets');
  await page.locator('.agents-page-header').getByRole('button', { name: 'Add agent target' }).click();
  await page.getByLabel('Target channel').selectOption('voice');
  await page.getByLabel('Target connection').selectOption('waylo');
  await expect(page.getByLabel('Waylo authorization')).toHaveValue('waylo_browser_session');
  await expect(page.getByLabel('Waylo password')).toBeEnabled();
  await page.getByLabel('Waylo email').fill('fixture@example.test');
  await page.getByLabel('Waylo password').fill('synthetic-password-not-a-real-secret');
  await expect(page.getByRole('button', { name: 'Connect Waylo', exact: true })).toBeDisabled();
  await page.getByLabel('Confirm temporary Waylo sign-in').check();
}

test('temporary connection clears password, saves IDs only, and preserves proof through explicit SPA test launch', async ({ page }) => {
  const fixture = await fixtureApis(page);
  await openTemporaryForm(page);
  await expect(page.getByRole('button', { name: 'Create target' })).toBeDisabled();
  await page.getByRole('button', { name: 'Connect Waylo', exact: true }).click();
  await expect(page.getByLabel('Waylo password')).toHaveValue('');
  expect(fixture.requests.find((request) => request.path.endsWith('/connect'))?.body).toMatchObject({
    expected_endpoint_url: endpoint, confirm: true, waylo_agent_id: agentId,
  });
  fixture.allowLogin();
  await expect(page.getByText(/Connected to Mike fixture/)).toBeVisible();
  await page.getByRole('button', { name: 'Create target' }).click();
  const card = page.getByRole('article').filter({ hasText: 'Mike fixture' });
  await expect(card).toBeVisible();
  const saved = fixture.requests.find((request) => request.path === '/api/agents' && request.method === 'POST');
  expect(saved?.body).toMatchObject({ target: 'waylo', connection: {
    auth_type: 'waylo_browser_session', secret_ref: null, endpoint_url: endpoint,
    workspace_id: workspaceId, waylo_agent_id: agentId,
  } });
  expect(JSON.stringify(saved?.body)).not.toMatch(/fixture-private-proof|password|session_proof|access_token/);
  expect(saved?.headers['x-cae-waylo-session']).toBe(proof);
  const stored = await page.evaluate(() => ({ local: JSON.stringify({ ...localStorage }), session: JSON.stringify({ ...sessionStorage }), cookies: document.cookie }));
  expect(JSON.stringify(stored)).not.toMatch(/fixture-private-proof|synthetic-password|fixture@example.test/);
  await card.getByLabel('Session UUID for Mike fixture').fill(workspaceId);
  await card.getByRole('button', { name: 'Download session vCon' }).click();
  await expect.poll(() => fixture.requests.some((request) => request.path.endsWith('/capture'))).toBeTruthy();
  expect(fixture.requests.find((request) => request.path.endsWith('/capture'))?.headers['x-cae-waylo-session']).toBe(proof);
  await card.getByRole('link', { name: 'Test Mike’s five-bags case' }).click();
  await expect(page.getByLabel('Execution agent target')).toHaveValue('waylo-memory-fixture');
  await expect(page.getByText(/Connected to Mike fixture/)).toBeVisible();
  expect(fixture.requests.filter((request) => request.path === '/api/execution/runs' && request.method === 'POST')).toHaveLength(0);
  await page.getByRole('button', { name: 'Run evaluation', exact: true }).click();
  await expect.poll(() => fixture.requests.filter((request) => request.path === '/api/execution/runs' && request.method === 'POST').length).toBe(1);
  const run = fixture.requests.find((request) => request.path === '/api/execution/runs' && request.method === 'POST');
  expect(run?.headers['x-cae-waylo-session']).toBe(proof);
  expect(run?.body).toMatchObject({ agent_id: 'waylo-memory-fixture', suite_id: 'waylo-mike-notes', scenario_ids: ['mike-five-bags'], evaluate: false, executor_id: 'waylo_livekit' });
  expect(JSON.stringify(run?.body)).not.toContain(proof);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Connect Waylo', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run evaluation', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Waylo password')).toHaveValue('');
});

test('explicit platform-admin tenant connection retains its safe mount and disconnects without persisting proof', async ({ page }) => {
  const fixture = await fixtureApis(page, true);
  await openTemporaryForm(page);
  await page.getByLabel('Waylo sign-in tenant UUID').fill(tenantId);
  fixture.allowLogin();
  await page.getByRole('button', { name: 'Connect Waylo', exact: true }).click();
  await expect(page.getByLabel('Waylo API base URL')).toHaveValue(`${endpoint}/admin/tenants/${tenantId}`);
  await expect(page.getByRole('button', { name: 'Create target' })).toBeEnabled();
  const connect = fixture.requests.find((request) => request.path.endsWith('/connect'));
  expect(connect?.body?.tenant_id).toBe(tenantId);
  await page.getByRole('button', { name: 'Disconnect Waylo', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Create target' })).toBeDisabled();
  expect(fixture.requests.find((request) => request.path.endsWith('/disconnect'))?.headers['x-cae-waylo-session']).toBe(proof);
  await expect(page.getByLabel('Waylo password')).toHaveValue('');
});
