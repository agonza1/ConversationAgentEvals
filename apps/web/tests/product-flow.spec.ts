import { expect, test } from '@playwright/test';

test('homepage links to the two evaluation workflows', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('link', { name: 'Browse test sets', exact: true }).first()).toHaveAttribute('href', '/tests');
  await expect(page.getByRole('link', { name: 'Analyze existing evidence' })).toHaveAttribute('href', '/eval');
  await expect(page.getByRole('link', { name: 'Start an agent test', exact: true })).toHaveAttribute('href', '/runs');
  await expect(
    page.getByRole('img', { name: 'Run an agent or import transcript or vCon evidence, then evaluate and report' }),
  ).toBeVisible();
  const diagram = page.locator('.home-flow-visual img');
  await diagram.scrollIntoViewIfNeeded();
  await expect.poll(() => diagram.evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Benchmarks' })).toHaveCount(0);
});

test('homepage keeps workflows usable on desktop and small screens without starting a run', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', (request) => {
    if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.url());
  });
  for (const width of [1440, 768, 641, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Proven outcomes.');
    await expect(page.locator('.home-path')).toHaveCount(2);
    for (const name of ['Start an agent test', 'Analyze existing evidence']) {
      const link = page.getByRole('link', { name, exact: true });
      await expect(link).toBeVisible();
      await link.focus();
      await expect(link).toBeFocused();
      expect(await link.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe('solid');
    }
    await expect(page.getByRole('navigation', { name: 'Workspace shortcuts' }).getByRole('link')).toHaveCount(4);
    await expect(page.getByText('Missing proof stays unverified', { exact: false })).toBeVisible();
    await expect(page.getByText('Benchmark report', { exact: true })).toHaveCount(0);
    for (const anchor of ['product', 'coverage', 'benchmarks']) {
      await expect(page.locator(`#${anchor}`)).toHaveCount(1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  expect(writes).toEqual([]);
});

test('homepage navigation preserves an API base override', async ({ page }) => {
  await page.goto('/?api_base=http%3A%2F%2Fapi.example.test');

  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Results' })).toHaveAttribute(
    'href',
    '/results?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect(page.getByRole('link', { name: 'Browse test sets', exact: true }).first()).toHaveAttribute(
    'href',
    '/tests?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect(page.getByRole('link', { name: 'Start an agent test', exact: true })).toHaveAttribute(
    'href',
    '/runs?api_base=http%3A%2F%2Fapi.example.test',
  );
  const shortcuts = page.getByRole('navigation', { name: 'Workspace shortcuts' });
  for (const [name, path] of [['Manage agents', '/targets'], ['View results', '/results'], ['Open settings', '/settings']]) {
    await expect(shortcuts.getByRole('link', { name, exact: true })).toHaveAttribute('href', `${path}?api_base=http%3A%2F%2Fapi.example.test`);
  }
  await expect(page.getByRole('link', { name: 'Analyze existing evidence', exact: true })).toHaveAttribute(
    'href', '/eval?api_base=http%3A%2F%2Fapi.example.test',
  );
});

test('dedicated paths expose only their primary workflow', async ({ page }) => {
  await page.goto('/simulate');
  await expect(page).toHaveURL(/\/scenarios$/);
  await expect(page.getByRole('heading', { name: 'Choose what your agent must prove.' })).toBeVisible();

  await page.goto('/eval');
  await expect(page.getByRole('heading', { name: 'Analyze a conversation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Evaluate evidence' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toHaveCount(0);
  await expect(page.getByLabel('Evidence upload')).toBeVisible();
  await expect(page.getByLabel('Upload vCon or transcript file')).toBeVisible();
  await expect(page.getByLabel('Evaluation agent')).toHaveCount(0);
  await expect(page.getByLabel('Model', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Agent profile')).toHaveCount(0);
  await expect(page.getByText('Failure baseline')).toHaveCount(0);
  await expect(page.getByText('Scenario rubric')).toHaveCount(0);
  await expect(page.getByLabel('Suite contract manifest')).toHaveCount(0);
  await expect(page.getByText('Required evidence:')).toHaveCount(0);
  await expect(page.getByText('Advanced details')).toHaveCount(0);
  await expect(page.getByLabel('Evaluation contract')).toBeVisible();
  await expect(page.getByLabel('Evaluation suite')).toBeVisible();
  await expect(page.getByLabel('Evaluation scenario', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Create new scenario' })).toHaveAttribute('href', '/scenarios?create=1');
  await expect(page.getByText('What this scenario checks')).toBeVisible();
  await expect(page.getByText('Attribute this score')).toBeVisible();
  await expect(page.getByLabel('Attributed agent', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Attributed agent target')).toBeHidden();
  await page.getByText('Attribute this score').click();
  await expect(page.getByLabel('Attributed agent target')).toBeVisible();
  await expect(page.getByLabel('Attributed agent target')).toHaveValue('');
  await expect(page.getByLabel('Attributed model')).toBeVisible();
  await expect(page.getByText(/do not change how evidence is scored/i)).toBeVisible();
  await expect(page.locator('form').first().locator('textarea').first()).toHaveValue('');
  await expect(page.getByRole('button', { name: 'Evaluate evidence' })).toBeDisabled();
  await expect(page.getByText(/This evidence is synthetic/)).toHaveCount(0);
  await expect(page.getByLabel('LLM judge controls')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Request LLM judge' })).toBeDisabled();
  await page.getByRole('button', { name: 'Load sample evidence' }).click();
  const sampleOptions = page.getByLabel('Sample evidence options');
  await expect(sampleOptions).toBeVisible();
  await expect(sampleOptions.getByRole('button', { name: 'Load sample transcript only' })).toBeVisible();
  await expect(sampleOptions.getByRole('button', { name: 'Load full sample (measure Task/Final)' })).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toHaveCount(0);

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Test an agent' })).toBeVisible();
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run evaluation' })).toBeVisible();
  await expect(page.getByLabel('Execution tester')).toContainText('Scenario user (AI)');
  await expect(page.getByLabel('Execution runner')).toContainText(/local async runner/i);
  await expect(page.getByLabel('Saved runs and e2e validation')).toHaveCount(0);
  // /runs is execute/capture — not the evidence-eval contract console
  await expect(page.getByText('Benchmark suite')).toHaveCount(0);
  await expect(page.getByText('Scenario rubric')).toHaveCount(0);
  await expect(page.getByLabel('Suite contract manifest')).toHaveCount(0);
  await expect(page.getByText('Required evidence:')).toHaveCount(0);
});

test('legacy benchmark route keeps advanced simulation and history workflows', async ({ page }) => {
  await page.goto('/benchmarks');
  await expect(page.getByRole('heading', { name: 'Benchmark console' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate scenario' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Evaluate evidence' })).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toBeVisible();
});

test('scenario and eval deep links preload the advertised scenario', async ({ page }) => {
  await page.goto('/scenarios?suite_id=call-center-voice-ai&scenario_id=billing-address-change');
  await expect(page.getByRole('heading', { name: 'Billing Address Change' })).toBeVisible();
  await expect(page.getByLabel('Selected scenario').getByRole('link', { name: 'Test an agent' })).toHaveAttribute('href', /suite_id=call-center-voice-ai&scenario_id=billing-address-change/);
  await expect(page.getByLabel('Selected scenario').getByRole('link', { name: 'Analyze a conversation' })).toHaveAttribute('href', /sample=1/);

  await page.goto('/eval?demo=sample-evidence');
  await expect(page.getByText('Loading benchmark suites...')).toHaveCount(0);
  const evalForm = page.locator('form').first();
  await expect(evalForm.getByLabel('Evaluation contract')).toBeVisible();
  await expect(evalForm.getByLabel('Evaluation scenario', { exact: true })).toBeVisible();
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
  await expect(page.getByRole('heading', { name: 'Analyze a conversation' })).toBeVisible();
  await page.waitForLoadState('networkidle');
  expect(regressionSummaryRequests).toBe(0);
});
