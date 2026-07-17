import { expect, test } from '@playwright/test';

test('homepage links to focused workflow demos', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('link', { name: 'Browse evaluation scenarios' })).toHaveAttribute('href', '/scenarios');
  await expect(page.getByRole('link', { name: 'Eval sample evidence' })).toHaveAttribute('href', '/eval?demo=sample-evidence');
  await expect(page.getByRole('link', { name: 'Launch agent run' })).toHaveAttribute('href', '/runs?launch=demo');
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Benchmarks' })).toHaveCount(0);
});

test('homepage navigation preserves an API base override', async ({ page }) => {
  await page.goto('/?api_base=http%3A%2F%2Fapi.example.test');

  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Run agent' })).toHaveAttribute(
    'href',
    '/runs?api_base=http%3A%2F%2Fapi.example.test',
  );
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
  await expect(page.locator('form').first().locator('textarea').first()).toHaveValue('');
  await expect(page.getByRole('button', { name: 'Evaluate evidence' })).toBeDisabled();
  await expect(page.getByText(/This evidence is synthetic/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Load sample evidence' }).click();
  await expect(page.getByLabel('Call Center Voice AI sample evidence options')).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toHaveCount(0);

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Run an agent' })).toBeVisible();
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Launch agent run' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Mock agent run' })).toBeVisible();
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
  await expect(page.getByLabel('Selected scenario').getByRole('link', { name: 'Eval sample evidence' })).toHaveAttribute('href', /sample=1/);

  await page.goto('/eval?demo=sample-evidence');
  await expect(page.getByText('Loading benchmark suites...')).toHaveCount(0);
  const evalForm = page.locator('form').first();
  await expect(evalForm.locator('select').nth(0)).toHaveValue('call-center-voice-ai');
  await expect(evalForm.locator('select').nth(1)).toHaveValue('billing-address-change');
  await expect(evalForm.locator('textarea').first()).not.toHaveValue('');
});

test('mobile scenario selection moves focus to the selected detail', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/scenarios');

  await page.getByRole('button', { name: /Suspicious Card Charge/ }).click();
  await expect(page.getByLabel('Selected scenario').getByRole('heading', { name: 'Suspicious Card Charge' })).toBeInViewport();
  await expect(page.getByRole('link', { name: 'Back to scenario catalog' })).toBeVisible();

  const navHeight = await page.getByRole('navigation', { name: 'Primary' }).evaluate((element) => element.getBoundingClientRect().height);
  expect(navHeight).toBeLessThan(100);
});

test('empty eval history does not request unavailable regression summaries', async ({ page }) => {
  let regressionSummaryRequests = 0;
  await page.route('**/api/product/runs?*', async (route) => route.fulfill({ json: [] }));
  await page.route('**/api/product/projects/*/regression-summary?*', async (route) => {
    regressionSummaryRequests += 1;
    await route.fulfill({ json: {} });
  });

  await page.goto('/eval');
  await expect(page.getByRole('heading', { name: 'Evaluate conversation evidence.' })).toBeVisible();
  await page.waitForLoadState('networkidle');
  expect(regressionSummaryRequests).toBe(0);
});
