import { expect, test } from '@playwright/test';

test('free-to-paid eval journey works end to end', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Run deterministic checks now. Save and judge after signup.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Get from sample scenario to saved QA history.' })).toBeVisible();
  await expect(page.getByLabel('Pick a scenario: Done')).toBeVisible();
  await expect(page.getByLabel('Run evidence check: Ready')).toBeVisible();
  await expect(page.getByLabel('Save repeatable history: Next')).toBeVisible();
  await expect(page.getByRole('button', { name: /Starter/ })).toContainText('$19/month');
  await expect(page.getByRole('button', { name: /Team/ })).toContainText('$99/month');

  await page.getByRole('button', { name: 'Simulate scenario' }).click();
  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
  await expect(page.getByText('Benchmark report').last()).toBeVisible();
  await expect(page.getByLabel('Run evidence check: Done')).toBeVisible();

  const reportDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download report JSON' }).click();
  const reportDownloadFile = await reportDownload;
  expect(reportDownloadFile.suggestedFilename()).toMatch(/agentbench-.*-report\.json/);
  await expect(page.getByText('Exported current benchmark report JSON.')).toBeVisible();

  await page.getByRole('button', { name: 'Request LLM judge' }).click();
  await expect(page.getByText(/available on Starter and above/)).toBeVisible();

  await page.getByRole('button', { name: /Starter/ }).click();
  await page.getByRole('button', { name: 'Request LLM judge' }).click();
  await expect(page.getByText(/LLM judge request accepted/)).toBeVisible();
  await expect(page.getByText('10 credits estimated; 200 of 200 daily credits available; vertex not configured.')).toBeVisible();

  await page.getByRole('button', { name: 'Save run' }).click();
  await expect(page.getByText('Sign up first to save projects and run history.')).toBeVisible();

  await page.getByRole('button', { name: 'Sign up to save' }).click();
  await expect(page.getByText(/Signed in with local Firebase-ready demo identity/)).toBeVisible();

  await page.getByRole('button', { name: 'Save run' }).click();
  await expect(page.getByText(/Saved run/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /1 saved for Billing Address Change/ })).toBeVisible();
  await expect(page.getByText('Baseline run for this project.')).toBeVisible();
  await expect(page.getByText(/vCon ready: \d+ dialog turns, \d+ analysis records/)).toBeVisible();
  await expect(page.getByLabel('Save repeatable history: Done')).toBeVisible();
  await expect(page.getByText('Selected scenario: baseline')).toBeVisible();
  await expect(page.getByText('1 focused runs')).toBeVisible();

  await page.getByRole('button', { name: 'Load run' }).click();
  await expect(page.getByText(/Loaded saved run/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();

  await page.getByRole('button', { name: 'Retry run' }).click();
  await expect(page.getByText(/Retried saved run/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();

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
  await expect(page.getByText('required_action_execution', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Missing actions' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Forbidden actions observed' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Suggested fixes' })).toBeVisible();
});
