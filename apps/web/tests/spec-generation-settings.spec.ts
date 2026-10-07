import { expect, test } from '@playwright/test';

test('console model selection persists, supports custom IDs and resets to default', async ({ page }) => {
  let model: string | null = null;
  await page.route('**/api/specs/generation-settings', async (route) => {
    if (route.request().method() === 'PATCH') model = route.request().postDataJSON().model;
    await route.fulfill({ json: { model, default_model: 'gpt-6-luna', effective_model: model || 'gpt-6-luna', provider: 'openai_codex', available: true, source: model ? 'console' : 'deployment' } });
  });
  await page.route('**/api/product/providers/openai/models', (route) => route.fulfill({ json: { models: [{ id: 'gpt-6-luna' }, { id: 'gpt-5.6-luna' }] } }));
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'Draft generation settings' });
  await panel.getByLabel('Draft model choices').selectOption('gpt-5.6-luna');
  await panel.getByRole('button', { name: 'Save draft model' }).click();
  await expect(panel.getByRole('status')).toContainText('gpt-5.6-luna');
  await page.reload();
  await expect(panel.getByLabel('Draft model choices')).toHaveValue('gpt-5.6-luna');
  await panel.getByLabel('Draft model choices').selectOption('__custom__');
  await panel.getByLabel('Custom draft model ID').fill('gpt-custom-text');
  await panel.getByRole('button', { name: 'Save draft model' }).click();
  await expect(panel.getByRole('status')).toContainText('gpt-custom-text');
  await panel.getByLabel('Draft model choices').selectOption('');
  await panel.getByRole('button', { name: 'Save draft model' }).click();
  await expect(panel.getByRole('status')).toContainText('gpt-6-luna');
  expect(model).toBeNull();
});

test('console reports failed saves without claiming a new active model', async ({ page }) => {
  await page.route('**/api/specs/generation-settings', async (route) => {
    if (route.request().method() === 'PATCH') return route.fulfill({ status: 503, json: { detail: 'Could not persist draft-generation settings.' } });
    return route.fulfill({ json: { model: null, default_model: 'gpt-6-luna', effective_model: 'gpt-6-luna', provider: 'openai_codex', available: true, source: 'deployment' } });
  });
  await page.route('**/api/product/providers/openai/models', (route) => route.fulfill({ json: { models: [{ id: 'gpt-5.6-luna' }] } }));
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'Draft generation settings' });
  await panel.getByLabel('Draft model choices').selectOption('gpt-5.6-luna');
  await panel.getByRole('button', { name: 'Save draft model' }).click();
  await expect(panel.getByRole('alert')).toContainText('Could not persist');
  await expect(panel).toContainText('Active: gpt-6-luna');
  await expect(panel.getByRole('status')).toHaveCount(0);
});

test('console does not claim an active provider without credentials', async ({ page }) => {
  await page.route('**/api/specs/generation-settings', (route) => route.fulfill({ json: { model: null, default_model: 'gpt-4.1-mini', effective_model: 'gpt-4.1-mini', provider: 'unconfigured', available: false, source: 'deployment' } }));
  await page.route('**/api/product/providers/openai/models', (route) => route.fulfill({ status: 401, json: { detail: 'Connect OpenAI' } }));
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'Draft generation settings' });
  await expect(panel).toContainText('No generation provider is connected.');
  await expect(panel).not.toContainText('Active:');
  await panel.getByRole('button', { name: 'Save draft model' }).click();
  await expect(panel.getByRole('status')).toContainText('Connect Codex or configure an OpenAI API key');
  await expect(panel.getByRole('status')).not.toContainText('Next draft will use');
});
