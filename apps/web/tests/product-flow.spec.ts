import { expect, test } from '@playwright/test';

test('homepage links to focused workflow demos', async ({ page }) => {
  await page.goto('/');

  const simulate = page.getByRole('link', { name: 'Simulate scenario' });
  await expect(simulate).toHaveAttribute('href', '/simulate?demo=angry-caller');
  await expect(page.getByRole('link', { name: 'Score sample evidence' })).toHaveAttribute('href', '/score?demo=sample-evidence');
  await expect(page.getByRole('link', { name: 'Launch agent run' })).toHaveAttribute('href', '/runs?launch=demo');
});

test('dedicated paths expose only their primary workflow', async ({ page }) => {
  await page.goto('/simulate');
  await expect(page.getByRole('heading', { name: 'Simulate a scenario or suite.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Queue simulated suite' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Score evidence' })).toHaveCount(0);
  await expect(page.getByLabel('Saved runs and e2e validation')).toHaveCount(0);
  await expect(page.getByText('Team-gated WebRTC evals')).toHaveCount(0);

  await page.goto('/score');
  await expect(page.getByRole('heading', { name: 'Upload evidence and score it.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Score evidence' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toHaveCount(0);
  await expect(page.getByLabel('Evidence upload')).toBeVisible();
  await expect(page.getByLabel('Upload vCon or transcript file')).toBeVisible();
  await expect(page.getByLabel('Scoring agent')).toBeVisible();
  await expect(page.getByLabel('Model')).toBeVisible();
  await expect(page.getByText('Agent profile')).toHaveCount(0);
  await expect(page.getByText('Scenario rubric')).toHaveCount(0);
  await expect(page.getByLabel('Suite contract manifest')).toHaveCount(0);
  await expect(page.getByText('Required evidence:')).toHaveCount(0);
  await expect(page.getByText('Advanced details')).toBeVisible();
  await page.getByRole('button', { name: 'Simulate evidence upload' }).click();
  await expect(page.getByLabel('Call Center Voice AI simulate options')).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toBeVisible();

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Run an agent' })).toBeVisible();
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Launch agent run' })).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toHaveCount(0);
  // /runs is execute/capture — not the simulate/score contract console
  await expect(page.getByText('Benchmark suite')).toHaveCount(0);
  await expect(page.getByText('Scenario rubric')).toHaveCount(0);
  await expect(page.getByLabel('Suite contract manifest')).toHaveCount(0);
  await expect(page.getByText('Required evidence:')).toHaveCount(0);
});

test('legacy benchmark route keeps the full console for history workflows', async ({ page }) => {
  await page.goto('/benchmarks');
  await expect(page.getByRole('heading', { name: 'Benchmark history and reports.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Score evidence' })).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toBeVisible();
});

test('demo deep links preload the advertised scenario', async ({ page }) => {
  await page.goto('/simulate?demo=angry-caller');
  await expect(page.getByText('Loading benchmark suites...')).toHaveCount(0);
  const simulateForm = page.locator('form').first();
  await expect(simulateForm.locator('select').nth(0)).toHaveValue('call-center-voice-ai');
  await expect(simulateForm.locator('select').nth(1)).toHaveValue('angry-outage-escalation');
  await expect(simulateForm.locator('textarea').first()).not.toHaveValue('');

  await page.goto('/score?demo=sample-evidence');
  await expect(page.getByText('Loading benchmark suites...')).toHaveCount(0);
  const scoreForm = page.locator('form').first();
  await expect(scoreForm.locator('select').nth(0)).toHaveValue('call-center-voice-ai');
  await expect(scoreForm.locator('select').nth(1)).toHaveValue('billing-address-change');
  await expect(scoreForm.locator('textarea').first()).not.toHaveValue('');
});
