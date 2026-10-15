import { expect, test, type Page } from '@playwright/test';

const endpoint = 'https://api.poc.app.waylovoice.ai';
const agentId = 'b722ea62-524a-4664-8702-f38f2bed5cbe';
const workspaceId = '11111111-1111-4111-8111-111111111111';
const tenantId = '22222222-2222-4222-8222-222222222222';
const proof = 'fixture-private-proof-not-a-provider-token';

async function fixtureApis(page: Page, admin = false, initialAgents: Record<string, unknown>[] = [], caseCount = 1) {
  const agents: Record<string, unknown>[] = [...initialAgents];
  const requests: Array<{ path: string; method: string; headers: Record<string, string>; body?: Record<string, unknown> }> = [];
  let allowLogin: (() => void) | undefined;
  const loginGate = new Promise<void>((resolve) => { allowLogin = resolve; });
  const status = () => ({ connected: true, expires_at: new Date(Date.now() + 900_000).toISOString(),
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
    else if (path === '/api/benchmarks/suites') value = [{ id: 'waylo-mike-notes', title: 'Waylo Mike notes', scenarios: Array.from({ length: caseCount }, (_, index) => ({ id: index ? `case-${index}` : 'mike-five-bags', title: index ? `Case ${index}` : 'Five bags, not catalog pack size', caller_steps: ['Please note five bags of white potatoes.', 'Read back my notes.'] })) }];
    else if (path.endsWith('/providers/openai/status')) value = { status: 'disconnected', provider: 'openai_codex' };
    else if (path === '/api/execution/runs' && method === 'POST') {
      if (request.headers()['x-cae-waylo-control'] !== '1' || request.headers()['x-cae-waylo-session'] !== proof) {
        await route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Waylo connection control requires a local browser request.' }) });
        return;
      }
      value = { execution_run_id: 'exec-waylo-memory-fixture', status: 'completed', mode: 'pipecat_webrtc', conversations: [] };
    }
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

async function openConnectedRun(page: Page, caseCount: number) {
  const fixture = await fixtureApis(page, false, [], caseCount);
  await openTemporaryForm(page);
  fixture.allowLogin();
  await page.getByRole('button', { name: 'Connect Waylo', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Create target' })).toBeEnabled();
  await page.getByRole('button', { name: 'Create target' }).click();
  const card = page.getByRole('article').filter({ hasText: 'Mike fixture' });
  await card.getByRole('button', { name: /Show details/ }).click();
  await card.getByRole('link', { name: 'Test Mike’s five-bags case' }).click();
  await expect(page.getByLabel('Execution agent target')).toHaveValue('waylo-memory-fixture');
  return fixture;
}

test('temporary grant visibly blocks oversized suite scope and iterations while single calls remain available', async ({ page }) => {
  const fixture = await openConnectedRun(page, 10);
  const entireSuite = page.getByRole('button', { name: /Entire suite/ });
  const run = page.getByRole('button', { name: /^Run \d+ voice tests?$/ });
  await expect(entireSuite).toBeDisabled();
  await expect(page.getByRole('status', { name: 'Waylo suite access limit' })).toContainText('15-minute');
  await expect(run).toBeEnabled();
  await page.getByLabel('Duplex session timeout').fill('30');
  await expect(entireSuite).toBeDisabled();
  await page.getByLabel('Execution iterations').fill('7');
  await expect(run).toBeDisabled();
  await expect(page.getByRole('status', { name: 'Waylo run access limit' })).toContainText('fewer iterations');
  await page.getByLabel('Execution iterations').fill('1');
  await expect(run).toBeEnabled();
  expect(fixture.requests.filter((request) => request.path === '/api/execution/runs' && request.method === 'POST')).toHaveLength(0);
});

test('temporary grant supports small whole suites that fit its capture budget', async ({ page }) => {
  const fixture = await openConnectedRun(page, 3);
  const entireSuite = page.getByRole('button', { name: /Entire suite/ });
  await expect(entireSuite).toBeEnabled();
  await entireSuite.click();
  await page.getByRole('button', { name: 'Run 3 voice tests', exact: true }).click();
  await expect.poll(() => fixture.requests.some((request) => request.path === '/api/execution/runs' && request.method === 'POST')).toBe(true);
  const request = fixture.requests.find((item) => item.path === '/api/execution/runs' && item.method === 'POST');
  expect(request?.body?.scenario_ids).toEqual(['mike-five-bags', 'case-1', 'case-2']);
});

test('synthetic noise is opt-in and included only when enabled for a Waylo call', async ({ page }) => {
  const fixture = await openConnectedRun(page, 1);
  const noise = page.getByRole('checkbox', { name: 'Add synthetic background noise' });
  await expect(noise).not.toBeChecked();
  await noise.check();
  await page.getByRole('button', { name: 'Run 1 voice test', exact: true }).click();
  await expect.poll(() => fixture.requests.filter((item) => item.path === '/api/execution/runs' && item.method === 'POST').length).toBe(1);
  expect(fixture.requests.find((item) => item.path === '/api/execution/runs' && item.method === 'POST')?.body?.tester_noise).toEqual({ snr_db: 20 });
});

test('idle time reduces the usable grant budget without extending temporary access', async ({ page }) => {
  await page.clock.install();
  await openConnectedRun(page, 1);
  const run = page.getByRole('button', { name: 'Run 1 voice test', exact: true });
  await expect(run).toBeEnabled();
  await page.clock.fastForward(12 * 60_000);
  await expect(run).toBeDisabled();
  await expect(page.getByRole('status', { name: 'Waylo run access limit' })).toContainText('expires before');
  await page.getByLabel('Duplex session timeout').fill('30');
  await expect(run).toBeEnabled();
});

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
  await expect(card.getByRole('status', { name: 'Waylo connection status' })).toContainText('Connected in this tab');
  await expect(card.locator('.waylo-card-status')).toHaveClass(/is-connected/);
  const saved = fixture.requests.find((request) => request.path === '/api/agents' && request.method === 'POST');
  expect(saved?.body).toMatchObject({ target: 'waylo', connection: {
    auth_type: 'waylo_browser_session', secret_ref: null, endpoint_url: endpoint,
    workspace_id: workspaceId, waylo_agent_id: agentId,
  } });
  expect(JSON.stringify(saved?.body)).not.toMatch(/fixture-private-proof|password|session_proof|access_token/);
  expect(saved?.headers['x-cae-waylo-session']).toBe(proof);
  const stored = await page.evaluate(() => ({ local: JSON.stringify({ ...localStorage }), session: JSON.stringify({ ...sessionStorage }), cookies: document.cookie }));
  expect(JSON.stringify(stored)).not.toMatch(/fixture-private-proof|synthetic-password|fixture@example.test/);
  await card.getByRole('button', { name: /Show details/ }).click();
  await expect(card.getByText(/Connected to Mike fixture/)).toBeVisible();
  await card.getByRole('button', { name: /Hide details/ }).click();
  await expect(card.getByRole('status', { name: 'Waylo connection status' })).toBeVisible();
  await card.getByRole('button', { name: /Show details/ }).click();
  await expect(card.getByText(/Connected to Mike fixture/)).toBeVisible();
  await card.locator('summary').filter({ hasText: /^Import existing human call$/ }).click();
  await card.getByLabel('Session UUID for Mike fixture').fill(workspaceId);
  await card.getByRole('button', { name: 'Download session vCon' }).click();
  await expect.poll(() => fixture.requests.some((request) => request.path.endsWith('/capture'))).toBeTruthy();
  expect(fixture.requests.find((request) => request.path.endsWith('/capture'))?.headers['x-cae-waylo-session']).toBe(proof);
  await card.getByText('Refresh a finished CAE tester run instead', { exact: true }).click();
  await card.getByLabel('CAE run ID for Mike fixture').fill('exec-fixture-refresh');
  await expect(card.getByRole('button', { name: 'Download session vCon' })).toBeDisabled();
  await card.getByLabel('CAE conversation ID for Mike fixture').fill('conv-fixture-refresh');
  await card.getByRole('button', { name: 'Download session vCon' }).click();
  await expect.poll(() => fixture.requests.filter((request) => request.path.endsWith('/capture')).length).toBe(2);
  const refresh = fixture.requests.filter((request) => request.path.endsWith('/capture'))[1];
  expect(refresh?.body).toMatchObject({ session_id: workspaceId, execution_run_id: 'exec-fixture-refresh', conversation_id: 'conv-fixture-refresh' });
  expect(refresh?.body?.user_id).toBeTruthy();
  expect(refresh?.headers['x-cae-waylo-session']).toBe(proof);
  await expect(card.getByRole('status').filter({ hasText: /Refreshed the existing CAE run export/ })).toBeVisible();
  await card.getByRole('link', { name: 'Test Mike’s five-bags case' }).click();
  await expect(page.getByLabel('Execution agent target')).toHaveValue('waylo-memory-fixture');
  await expect(page.getByText(/Connected to Mike fixture/)).toBeVisible();
  expect(fixture.requests.filter((request) => request.path === '/api/execution/runs' && request.method === 'POST')).toHaveLength(0);
  await page.getByRole('button', { name: 'Run 1 voice test', exact: true }).click();
  await expect.poll(() => fixture.requests.filter((request) => request.path === '/api/execution/runs' && request.method === 'POST').length).toBe(1);
  const run = fixture.requests.find((request) => request.path === '/api/execution/runs' && request.method === 'POST');
  expect(run?.headers['x-cae-waylo-session']).toBe(proof);
  expect(run?.body).toMatchObject({ agent_id: 'waylo-memory-fixture', suite_id: 'waylo-mike-notes', scenario_ids: ['mike-five-bags'], evaluate: false, executor_id: 'waylo_livekit' });
  expect(JSON.stringify(run?.body)).not.toContain(proof);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Connect Waylo', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run 1 voice test', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Waylo password')).toHaveValue('');
  await page.goto('/targets');
  await expect(page.getByRole('status', { name: 'Waylo connection status' })).toContainText('Not connected');
  await expect(page.locator('.waylo-card-status')).toHaveClass(/is-disconnected/);
});

test('automatically selected saved Waylo target launches with the voice executor and temporary authorization', async ({ page }) => {
  const fixture = await fixtureApis(page, false, [{
    id: 'waylo-memory-fixture', name: 'Mike fixture', target: 'waylo', channel: 'voice', metadata: {},
    connection: { endpoint_url: endpoint, workspace_id: workspaceId, waylo_agent_id: agentId, auth_type: 'waylo_browser_session' },
  }]);
  // No agent_id query parameter: exercise default selection, not the explicit target link.
  await page.goto('/runs?suite_id=waylo-mike-notes&scenario_id=mike-five-bags');
  await expect(page.getByLabel('Execution agent target')).toHaveValue('waylo-memory-fixture');
  await expect(page.getByRole('button', { name: 'Run 1 voice test', exact: true })).toBeDisabled();
  await page.getByLabel('Waylo email').fill('fixture@example.test');
  await page.getByLabel('Waylo password').fill('synthetic-password-not-a-real-secret');
  await page.getByLabel('Confirm temporary Waylo sign-in').check();
  fixture.allowLogin();
  await page.getByRole('button', { name: 'Connect Waylo', exact: true }).click();
  await expect(page.getByText(/Connected to Mike fixture/)).toBeVisible();
  await page.getByRole('button', { name: 'Run 1 voice test', exact: true }).click();
  await expect.poll(() => fixture.requests.filter((request) => request.path === '/api/execution/runs' && request.method === 'POST').length).toBe(1);
  const run = fixture.requests.find((request) => request.path === '/api/execution/runs' && request.method === 'POST');
  expect(run?.headers['x-cae-waylo-control']).toBe('1');
  expect(run?.headers['x-cae-waylo-session']).toBe(proof);
  expect(run?.body).toMatchObject({ agent_id: 'waylo-memory-fixture', mode: 'pipecat_webrtc', tester_id: 'pipecat_tester', executor_id: 'waylo_livekit', evaluate: false });
  expect(JSON.stringify(run?.body)).not.toContain(proof);
  await expect(page.getByText('Waylo connection control requires a local browser request.', { exact: true })).toHaveCount(0);
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
