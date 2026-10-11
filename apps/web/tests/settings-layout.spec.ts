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
