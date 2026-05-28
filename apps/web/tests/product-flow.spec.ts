import { expect, test } from '@playwright/test';

test('free-to-paid eval journey works end to end', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Run deterministic checks now. Save and judge after signup.' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Starter/ })).toContainText('$19/month');
  await expect(page.getByRole('button', { name: /Team/ })).toContainText('$99/month');

  await page.getByRole('button', { name: 'Simulate scenario' }).click();
  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
  await expect(page.getByText('Benchmark report').last()).toBeVisible();

  await page.getByRole('button', { name: 'Request LLM judge' }).click();
  await expect(page.getByText(/Upgrade required:/)).toContainText('Starter');

  await page.getByRole('button', { name: /Starter/ }).click();
  await page.getByRole('button', { name: 'Request LLM judge' }).click();
  await expect(page.getByText(/Judge gate ready:/)).toContainText('LLM judge request accepted');
  await expect(page.getByText('10 credits estimated; 200 of 200 daily credits available; vertex not configured.')).toBeVisible();

  await page.getByRole('button', { name: 'Save run' }).click();
  await expect(page.getByText('Sign up first to save projects and run history.')).toBeVisible();

  await page.getByRole('button', { name: 'Sign up to save' }).click();
  await expect(page.getByText(/Signed in with local Firebase-ready demo identity/)).toBeVisible();

  await page.getByRole('button', { name: 'Save run' }).click();
  await expect(page.getByText(/Saved run/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /1 saved for call-center-demo/ })).toBeVisible();
  await expect(page.getByText('Baseline run for this project.')).toBeVisible();

  await expect(page.getByRole('heading', { name: 'Team-gated WebRTC evals' })).toBeVisible();
});

test('failure baseline surfaces actionable benchmark report issues', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();

  await page.getByLabel('Failure baseline').check();
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByRole('heading', { name: 'needs_review' })).toBeVisible();
  await expect(page.getByText('Benchmark report').last()).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Failure categories' })).toBeVisible();
  await expect(page.getByText('required_action_execution')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Missing actions' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Forbidden actions observed' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Suggested fixes' })).toBeVisible();
});
