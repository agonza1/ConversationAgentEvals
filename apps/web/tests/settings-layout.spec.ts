import { expect, test, type Page } from '@playwright/test';

async function mockSettings(page: Page) {
  const writes: string[] = [];
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') writes.push(path);
    if (path.endsWith('/generation-settings')) return route.fulfill({ json: {
      model: null, default_model: 'gpt-6-luna', effective_model: 'gpt-6-luna',
      provider: 'openai_codex', source: 'deployment', available: true,
    } });
    if (path.endsWith('/openai/models')) return route.fulfill({ json: { models: [{ id: 'gpt-6-luna' }, { id: 'gpt-5.6-luna' }] } });
    if (path.endsWith('/openai/status')) return route.fulfill({ json: { status: 'connected', email: 'qa@example.test', plan_type: 'pro' } });
    if (path.endsWith('/chatgpt/status')) return route.fulfill({ json: {
      enabled: true, status: 'connected', sharing: true, pending: false,
      active_profile_id: 'qa', profiles: [{ id: 'qa', label: 'QA account' }],
      judge_model: 'chatgpt_plan/gpt-6-luna', message: 'ChatGPT plan use authorized.',
    } });
    return route.fulfill({ json: {} });
  });
  return writes;
}

test('Settings keeps connection scopes, controls and section links clear on desktop and mobile', async ({ page }) => {
  const writes = await mockSettings(page);
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: 'Agent execution', exact: true })).toBeVisible();
    const nav = page.getByRole('navigation', { name: 'Settings sections' });
    await expect(nav.getByRole('link', { name: 'Test drafting' })).toHaveAttribute('href', '#draft-generation');
    await expect(page.getByText('Separate jobs, separate settings')).toBeVisible();
    await expect(page.getByRole('region', { name: 'OpenAI agent provider' })).toContainText('Connected');
    const judge = page.getByRole('region', { name: 'ChatGPT plan judge settings' });
    await expect(judge).toContainText('Authorized');
    await expect(judge.getByRole('button', { name: 'Save judge model' })).toBeDisabled();
    await expect(judge.getByRole('button', { name: 'Sign out of selected ChatGPT account' })).toBeHidden();
    await judge.getByText('Account options', { exact: true }).click();
    await expect(judge.getByRole('button', { name: 'Sign out of selected ChatGPT account' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  expect(writes).toEqual([]);
});

test('Refreshing draft models preserves an unsaved choice and leaves the active model unchanged', async ({ page }) => {
  const writes = await mockSettings(page);
  await page.goto('/settings');
  const drafts = page.getByRole('region', { name: 'Draft generation settings' });
  await drafts.getByLabel('Draft model choices').selectOption('gpt-5.6-luna');
  await expect(drafts.getByText(/Unsaved selection/)).toBeVisible();
  await drafts.getByRole('button', { name: 'Refresh model list' }).click();
  await expect(drafts.getByRole('button', { name: 'Save draft model' })).toBeEnabled();
  await expect(drafts.getByLabel('Draft model choices')).toHaveValue('gpt-5.6-luna');
  await expect(drafts.locator('.settings-summary')).toContainText('gpt-6-luna');
  expect(writes).toEqual([]);
});

test('Judge status failures show a recovery action rather than an empty settings panel', async ({ page }) => {
  await mockSettings(page);
  let failing = true;
  await page.route('**/api/product/providers/chatgpt/status', (route) => route.fulfill(failing
    ? { status: 503, json: { detail: 'Judge status unavailable.' } }
    : { json: { enabled: false, status: 'disabled', profiles: [], message: 'Local judging is not enabled.' } }));
  await page.goto('/settings');
  const judge = page.getByRole('region', { name: 'ChatGPT plan judge settings' });
  await expect(judge.getByRole('alert')).toHaveText('Judge status unavailable.');
  failing = false;
  await judge.getByRole('button', { name: 'Refresh connection' }).click();
  await expect(judge).toContainText('Local judging is not enabled.');
  await expect(judge.getByRole('alert')).toHaveCount(0);
});

test('OpenAI login reserves a safe tab before a delayed start request', async ({ page, context }) => {
  await mockSettings(page);
  let connected = false;
  await page.route('**/api/product/providers/openai/status', (route) => route.fulfill({ json: {
    status: connected ? 'connected' : 'disconnected', email: connected ? 'qa@example.test' : null,
  } }));
  let releaseStart!: () => void;
  const startGate = new Promise<void>((resolve) => { releaseStart = resolve; });
  let startRequested = false;
  await page.route('**/api/product/providers/openai/oauth/start', async (route) => {
    startRequested = true;
    await startGate;
    await route.fulfill({ json: { authorize_url: `${new URL(page.url()).origin}/oauth-test` } });
  });
  await context.route('**/oauth-test', (route) => route.fulfill({ body: 'Mock login only' }));
  await page.goto('/settings');
  const provider = page.getByRole('region', { name: 'OpenAI agent provider' });
  const popupPromise = context.waitForEvent('page');
  await provider.getByRole('button', { name: 'Connect OpenAI', exact: true }).click();
  const popup = await popupPromise;
  try {
    await expect.poll(() => startRequested).toBe(true);
    expect(popup.url()).toBe('about:blank');
    expect(await popup.evaluate(() => window.opener)).toBeNull();
    await expect(provider.getByRole('button', { name: /Waiting for OpenAI/ })).toBeDisabled();
    connected = true;
    releaseStart();
    await expect(popup).toHaveURL(/\/oauth-test$/);
    await expect(provider).toContainText('Connected as qa@example.test.');
  } finally {
    releaseStart();
    await popup.close();
  }
});

test('Blocked OpenAI login tabs show immediate recovery without starting or polling OAuth', async ({ page }) => {
  const writes = await mockSettings(page);
  let statusReads = 0;
  await page.route('**/api/product/providers/openai/status', (route) => {
    statusReads++;
    return route.fulfill({ json: { status: 'disconnected' } });
  });
  await page.addInitScript(() => { window.open = () => null; });
  await page.goto('/settings');
  const provider = page.getByRole('region', { name: 'OpenAI agent provider' });
  await provider.getByRole('button', { name: 'Connect OpenAI', exact: true }).click();
  await expect(provider.getByRole('alert')).toContainText('Login tab blocked. Allow pop-ups for this site');
  await expect(provider.getByRole('button', { name: 'Connect OpenAI', exact: true })).toBeEnabled();
  expect(writes).toEqual([]);
  expect(statusReads).toBe(1);
});

test('Failed OAuth start closes the reserved tab and permits retry', async ({ page, context }) => {
  await mockSettings(page);
  await page.route('**/api/product/providers/openai/status', (route) => route.fulfill({ json: { status: 'disconnected' } }));
  await page.route('**/api/product/providers/openai/oauth/start', (route) => route.fulfill({
    status: 503, json: { detail: 'Login service unavailable.' },
  }));
  await page.goto('/settings');
  const provider = page.getByRole('region', { name: 'OpenAI agent provider' });
  const popupPromise = context.waitForEvent('page');
  await provider.getByRole('button', { name: 'Connect OpenAI', exact: true }).click();
  const popup = await popupPromise;
  await expect(provider.getByRole('alert')).toHaveText('Login service unavailable.');
  await expect.poll(() => popup.isClosed()).toBe(true);
  await expect(provider.getByRole('button', { name: 'Connect OpenAI', exact: true })).toBeEnabled();
});

test('Leaving Settings cancels a delayed OAuth start and closes its blank tab', async ({ page, context }) => {
  await mockSettings(page);
  await page.route('**/api/product/providers/openai/status', (route) => route.fulfill({ json: { status: 'disconnected' } }));
  let releaseStart!: () => void;
  const gate = new Promise<void>((resolve) => { releaseStart = resolve; });
  let started = false;
  let navigated = false;
  await context.route('**/oauth-test', (route) => {
    navigated = true;
    return route.fulfill({ body: 'Mock login only' });
  });
  await page.route('**/api/product/providers/openai/oauth/start', async (route) => {
    started = true;
    await gate;
    await route.fulfill({ json: { authorize_url: `${new URL(page.url()).origin}/oauth-test` } });
  });
  await page.goto('/settings');
  const popupPromise = context.waitForEvent('page');
  await page.getByRole('button', { name: 'Connect OpenAI', exact: true }).click();
  const popup = await popupPromise;
  try {
    await expect.poll(() => started).toBe(true);
    await page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: 'Overview', exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect.poll(() => popup.isClosed()).toBe(true);
    releaseStart();
    await page.unrouteAll({ behavior: 'wait' });
    expect(navigated).toBe(false);
  } finally {
    releaseStart();
    await page.unrouteAll({ behavior: 'wait' });
    if (!popup.isClosed()) await popup.close();
  }
});
