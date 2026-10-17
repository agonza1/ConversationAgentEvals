import { expect, test } from '@playwright/test';

test('hosted login gates private screens without starting model calls', async ({ page }) => {
  const writes: string[] = [];
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') writes.push(path);
    if (path === '/api/auth/config') return route.fulfill({ json: { enabled: true, firebase: { apiKey: 'synthetic', authDomain: 'synthetic.firebaseapp.com', projectId: 'synthetic', appId: 'synthetic' } } });
    return route.fulfill({ status: 401, json: { detail: 'Sign in' } });
  });
  await page.goto('/settings');
  await expect(page.getByRole('button', { name: 'Continue with Google' })).toBeVisible();
  await expect(page.getByText('Your tests, agents, results, and API key stay private', { exact: false })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Primary', exact: true })).toBeHidden();
  expect(writes).toEqual([]);
});

test('authenticated settings save and remove only the user key and hide local OAuth controls', async ({ page }) => {
  let saved = false;
  const writes: Array<{ path: string; method: string }> = [];
  await page.route('**/api/**', (route) => {
    expect(route.request().url()).not.toContain('untrusted.example');
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (method !== 'GET') writes.push({ path, method });
    if (path === '/api/auth/config') return route.fulfill({ json: { enabled: true, firebase: {} } });
    if (path === '/api/auth/me') return route.fulfill({ json: { uid: 'alice', email: 'alice@example.test' } });
    if (path === '/api/auth/openai-key') {
      if (method === 'PUT') {
        expect(route.request().postDataJSON()).toEqual({ api_key: 'sk-synthetic-user-test-key' }); saved = true;
      }
      if (method === 'DELETE') saved = false;
      return route.fulfill({ json: { configured: saved, service_fallback: false } });
    }
    if (path.endsWith('/generation-settings')) return route.fulfill({ json: { model: null, default_model: 'gpt-4.1-mini', effective_model: 'gpt-4.1-mini', provider: 'unconfigured', available: false, scope: 'user' } });
    if (path.endsWith('/openai/models')) return route.fulfill({ json: { models: [{ id: 'gpt-4.1-mini' }] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/settings?api_base=https://untrusted.example');
  await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect OpenAI', exact: true })).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Judge with your ChatGPT plan' })).toBeHidden();
  const key = page.getByLabel('OpenAI API key', { exact: true });
  await key.fill('sk-synthetic-user-test-key');
  await page.getByRole('button', { name: 'Save key', exact: true }).click();
  await expect(page.getByText('Your key was saved encrypted. No model call was made.')).toBeVisible();
  await expect(page.getByLabel('Replace API key')).toHaveValue('');
  expect(await page.evaluate(() => window.localStorage.getItem('conversation-evals-demo-user'))).toBe('alice');
  expect(await page.evaluate(() => JSON.stringify(window.localStorage))).not.toContain('sk-synthetic');
  await page.getByRole('button', { name: 'Remove key', exact: true }).click();
  await expect(page.getByText('Your key was removed. AI operations are disabled.')).toBeVisible();
  expect(writes).toEqual([{ path: '/api/auth/openai-key', method: 'PUT' }, { path: '/api/auth/openai-key', method: 'DELETE' }]);
});
