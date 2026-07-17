import { expect, test } from '@playwright/test';

test('homepage links to focused workflow demos', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('link', { name: 'Browse evaluation scenarios' })).toHaveAttribute('href', '/scenarios');
  await expect(page.getByRole('link', { name: 'Eval sample evidence' })).toHaveAttribute('href', '/eval?demo=sample-evidence');
  await expect(page.getByRole('link', { name: 'Launch agent run' })).toHaveAttribute('href', '/runs?launch=demo');
});

test('dedicated paths expose only their primary workflow', async ({ page }) => {
  await page.goto('/simulate');
  await expect(page).toHaveURL(/\/scenarios$/);
  await expect(page.getByRole('heading', { name: 'Choose what your agent must prove.' })).toBeVisible();

  await page.goto('/eval');
  await expect(page.getByRole('heading', { name: 'Evaluate conversation evidence.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Evaluate evidence' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toHaveCount(0);
  await expect(page.getByLabel('Evidence upload')).toBeVisible();
  await expect(page.getByLabel('Upload vCon or transcript file')).toBeVisible();
  await expect(page.getByLabel('Evaluation agent')).toBeVisible();
  await expect(page.getByLabel('Model', { exact: true })).toBeVisible();
  await expect(page.getByText('Agent profile')).toHaveCount(0);
  await expect(page.getByText('Scenario rubric')).toHaveCount(0);
  await expect(page.getByLabel('Suite contract manifest')).toHaveCount(0);
  await expect(page.getByText('Required evidence:')).toHaveCount(0);
  await expect(page.getByText('Advanced details')).toBeVisible();
  await page.getByRole('button', { name: 'Load sample evidence' }).click();
  await expect(page.getByLabel('Call Center Voice AI sample evidence options')).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toBeVisible();

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Run an agent' })).toBeVisible();
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Launch agent run' })).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toHaveCount(0);
  // /runs is execute/capture — not the evidence-eval contract console
  await expect(page.getByText('Benchmark suite')).toHaveCount(0);
  await expect(page.getByText('Scenario rubric')).toHaveCount(0);
  await expect(page.getByLabel('Suite contract manifest')).toHaveCount(0);
  await expect(page.getByText('Required evidence:')).toHaveCount(0);
});

test('legacy benchmark route keeps the full console for history workflows', async ({ page }) => {
  await page.goto('/benchmarks');
  await expect(page.getByRole('heading', { name: 'Benchmark history and reports.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Evaluate evidence' })).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toBeVisible();
});

test('scenario and eval deep links preload the advertised scenario', async ({ page }) => {
  await page.goto('/scenarios?suite_id=call-center-voice-ai&scenario_id=billing-address-change');
  await expect(page.getByRole('heading', { name: 'Billing Address Change' })).toBeVisible();
  await expect(page.getByLabel('Selected scenario').getByRole('link', { name: 'Run agent' })).toHaveAttribute('href', /suite_id=call-center-voice-ai&scenario_id=billing-address-change/);

  await page.goto('/eval?demo=sample-evidence');
  await expect(page.getByText('Loading benchmark suites...')).toHaveCount(0);
  const evalForm = page.locator('form').first();
  await expect(evalForm.locator('select').nth(0)).toHaveValue('call-center-voice-ai');
  await expect(evalForm.locator('select').nth(1)).toHaveValue('billing-address-change');
  await expect(evalForm.locator('textarea').first()).not.toHaveValue('');
});
