import { expect, test } from '@playwright/test';

test('primary destinations and the two evaluation inputs are consistent on desktop and mobile', async ({ page }) => {
  for (const width of [1280, 641, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/?api_base=http%3A%2F%2Fapi.example.test');
    // Compact fonts can otherwise keep the action on the links' row, moving
    // a left-anchored dropdown beyond the mobile viewport on Linux.
    if (width <= 641) await page.addStyleTag({ content: '.top-nav { font-family: Arial, sans-serif; }' });
    const nav = page.getByRole('navigation', { name: 'Primary' });
    await expect(nav.getByRole('link')).toHaveText(['ConVoice QA', 'Overview', 'Tests', 'Agents', 'Results', 'Settings']);
    await nav.getByText('New evaluation', { exact: true }).click();
    await expect(nav.getByRole('link', { name: /Test an agent/ })).toHaveAttribute('href', '/runs?api_base=http%3A%2F%2Fapi.example.test');
    await expect(nav.getByRole('link', { name: /Analyze a conversation/ })).toHaveAttribute('href', '/eval?api_base=http%3A%2F%2Fapi.example.test');
    const bounds = await nav.locator('.new-evaluation-options').boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await nav.getByRole('link', { name: /Test an agent/ }).focus();
    await page.keyboard.press('Escape');
    await expect(nav.getByRole('link', { name: /Test an agent/ })).toHaveCount(0);
    await expect(nav.getByText('New evaluation', { exact: true })).toBeFocused();
  }
});

test('Tests and the original scenario link expose the same catalog and preserve connection overrides', async ({ page }) => {
  for (const route of ['/tests', '/scenarios']) {
    await page.goto(`${route}?suite_id=call-center-voice-ai&scenario_id=billing-address-change&api_base=http%3A%2F%2Fapi.example.test`);
    // Only the link behavior needs an alternate backend; catalog errors remain explicit.
    await expect(page.getByRole('link', { name: 'Create test set' })).toHaveAttribute('href', '/specs/new?api_base=http%3A%2F%2Fapi.example.test');
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Tests' })).toHaveAttribute('aria-current', 'page');
  }
});

test('legacy voice query parameters survive without implicitly starting a call', async ({ page }) => {
  let posts = 0;
  await page.route('**/api/**', async (route) => {
    if (route.request().method() === 'POST') posts++;
    await route.fulfill({ json: [] });
  });
  await page.goto('/voice?api_base=http%3A%2F%2Fapi.example.test&suite_id=custom-suite&scenario_id=custom-case&agent_id=custom-agent&run_scope=suite&launch=demo&tag=a&tag=b');
  await expect(page.getByRole('heading', { name: 'Test an agent', exact: true })).toBeVisible();
  const query = new URL(page.url()).searchParams;
  expect(new URL(page.url()).pathname).toBe('/runs');
  expect(query.get('api_base')).toBe('http://api.example.test');
  expect(query.get('suite_id')).toBe('custom-suite');
  expect(query.get('scenario_id')).toBe('custom-case');
  expect(query.get('agent_id')).toBe('custom-agent');
  expect(query.get('run_scope')).toBe('suite');
  expect(query.getAll('tag')).toEqual(['a', 'b']);
  expect(query.has('launch')).toBe(false);
  expect(posts).toBe(0);
});

test('Settings loads connections and models without loading catalogs or execution history', async ({ page }) => {
  const requests: string[] = [];
  let connected = false;
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    if (path.endsWith('/openai/oauth/start')) return route.fulfill({ json: { authorize_url: 'about:blank' } });
    if (path.endsWith('/openai/disconnect')) connected = false;
    if (path.endsWith('/openai/status') || path.endsWith('/openai/disconnect')) return route.fulfill({ json: { status: connected ? 'connected' : 'disconnected' } });
    await route.fulfill({ json: {} });
  });
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: 'Connections and models' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Draft-generation model' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Judge with your ChatGPT plan' })).toBeVisible();
  await page.getByRole('button', { name: 'Connect OpenAI', exact: true }).click();
  connected = true;
  await expect(page.getByRole('button', { name: 'Disconnect OpenAI' })).toBeVisible();
  await page.getByRole('button', { name: 'Disconnect OpenAI' }).click();
  await expect(page.getByRole('button', { name: 'Connect OpenAI', exact: true })).toBeVisible();
  expect(requests.some((path) => path.includes('/execution/') || path.includes('/benchmarks/') || path === '/api/agents')).toBe(false);
});

test('Results shows project-wide saved evaluations and exports all displayed history', async ({ page }) => {
  const historyQueries: URLSearchParams[] = [];
  const exports: URLSearchParams[] = [];
  const records = Array.from({ length: 6 }, (_, index) => ({
    id: `saved-${index}`, project_id: 'workspace-test', firestore_path: `test/saved-${index}`, plan: 'free',
    created_at: '2026-10-11T00:00:00Z', transcript: `Retained transcript ${index}`,
    report: { run_id: `report-${index}`, suite_id: 'retired-suite', scenario_id: `retired-case-${index}`,
      scenario_title: `Retained case ${index}`, overall_score: 50, verdict: 'needs_review',
      transcript: `Retained transcript ${index}`, missing_actions: ['verify_identity'] },
  }));
  await page.addInitScript(() => {
    localStorage.setItem('conversation-evals-demo-user', 'workspace-user');
    localStorage.setItem('conversation-evals-demo-project', 'workspace-test');
  });
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/product/runs') {
      historyQueries.push(url.searchParams);
      return route.fulfill({ json: records });
    }
    if (url.pathname.endsWith('/export')) {
      exports.push(url.searchParams);
      return route.fulfill({ json: { filename: 'history.json', run_count: 6 } });
    }
    if (url.pathname === '/api/benchmarks/suites') return route.fulfill({ json: [{ id: 'default-suite', title: 'Default suite', scenarios: [{ id: 'default-case', title: 'Default case' }] }] });
    if (url.pathname === '/api/product/config') return route.fulfill({ json: { pricing: [], usage_rules: [], auth: {} } });
    if (url.pathname.endsWith('/contract-manifest')) return route.fulfill({ json: {} });
    if (url.pathname.endsWith('/openai/status')) return route.fulfill({ json: { status: 'disconnected' } });
    await route.fulfill({ json: [] });
  });
  await page.goto('/results');
  await expect(page.getByRole('heading', { name: 'Results', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Saved evaluations', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Load run', exact: true })).toHaveCount(6);
  expect(historyQueries.some((query) => !query.has('suite_id') && !query.has('scenario_id'))).toBe(true);
  await page.getByRole('button', { name: 'Load run', exact: true }).last().click();
  await expect(page.getByLabel('Saved evaluation actions')).toBeVisible();
  await expect(page.getByLabel('Report brief')).toContainText('Retained case 5');
  // Loading a record outside the current catalog must not reset its retained report.
  await page.getByRole('button', { name: 'Export history', exact: true }).click();
  await expect.poll(() => exports.length).toBe(1);
  expect(exports[0].has('suite_id')).toBe(false);
  expect(exports[0].has('scenario_id')).toBe(false);
  await expect(page.getByLabel('Report brief')).toContainText('Retained case 5');
  await expect(page.getByRole('button', { name: 'Run evaluation', exact: true })).toHaveCount(0);
});
