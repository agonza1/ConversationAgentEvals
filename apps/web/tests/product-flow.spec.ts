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

  await page.goto('/score');
  await expect(page.getByRole('heading', { name: 'Score transcript and execution evidence.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Score evidence' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toHaveCount(0);

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Run an agent' })).toBeVisible();
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Launch agent run' })).toBeVisible();
});

test('legacy benchmark route redirects to simulate', async ({ page }) => {
  await page.goto('/benchmarks');
  await expect(page).toHaveURL(/\/simulate$/);
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
