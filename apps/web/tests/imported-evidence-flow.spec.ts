import { expect, test, type Page, type APIRequestContext } from '@playwright/test';

test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'wait' }); });

async function setup(page: Page, request: APIRequestContext, full = false) {
  const sample = await request.get('/api/benchmarks/evidence/sample-vcon?suite_id=call-center-voice-ai&scenario_id=billing-address-change');
  expect(sample.ok()).toBeTruthy();
  const vcon = await sample.json();
  if (!full) {
    vcon.attachments = [];
    vcon.analysis = [{ type: 'report', body: { score: 100, action_trace: [{ status: 'success' }], final_state: { complete: true } } }];
  }
  let executions = 0, judges = 0;
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/api\/execution\/runs$/.test(req.url())) executions++;
    if (req.method() === 'POST' && /\/judge(?:\?|$)/.test(req.url())) judges++;
  });
  await page.route('**/api/product/config', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), llm_judge_status: 'enabled',
      assert_judge: { engine: 'assert', ready: true, model: 'small-fixture-model', message: 'Mock judge ready.' } } });
  });
  await page.goto('/eval');
  await expect(page.getByLabel('Evaluation scenario', { exact: true })).toBeEnabled();
  await page.getByLabel('Upload vCon or transcript file').setInputFiles({ name: 'external-conversation.vcon', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(vcon)) });
  await expect(page.getByLabel('Evidence transcript')).not.toHaveValue('');
  return { executions: () => executions, judges: () => judges };
}

async function evaluate(page: Page) {
  const response = page.waitForResponse((res) => res.url().endsWith('/api/benchmarks/run') && res.request().method() === 'POST');
  await page.getByRole('button', { name: 'Evaluate evidence', exact: true }).click();
  const result = await response; expect(result.ok()).toBeTruthy();
  await expect(page.getByLabel('Evaluation evidence summary')).toBeVisible();
  return result.json();
}

test('an imported transcript-only vCon scores 100 without claiming verified completion or running a target', async ({ page, request }) => {
  const calls = await setup(page, request);
  const flow = page.getByLabel('Imported evidence evaluation flow');
  await expect(flow).toContainText('No CAE test run or new call is required.');
  await expect(flow.locator('[aria-current="step"]')).toContainText('Check against a scenario');
  const report = await evaluate(page);
  expect(report.overall_score).toBe(100); expect(report.verdict).toBe('needs_review');
  expect(report.evaluation_scope.input_source).toBe('vcon');
  expect(report.evaluation_scope.artifact_presence).toEqual({ transcript: true, action_trace: false, final_state: false });
  const summary = page.getByLabel('Evaluation evidence summary');
  await expect(summary.getByRole('heading', { name: 'Outcome unverified' })).toBeVisible();
  await expect(summary).toContainText('Imported vCon · Transcript only');
  await expect(summary).toContainText('No completed review recorded');
  await expect(summary).not.toContainText('100');
  await expect(summary.locator('dl > div').filter({ hasText: 'Tool / action trace' })).toContainText('Not supplied');
  await expect(summary.locator('dl > div').filter({ hasText: 'Resulting state' })).toContainText('Not supplied');
  await expect(page.getByText('Ready for release review', { exact: true })).toHaveCount(0);
  const diagnostics = page.getByLabel('Evaluation diagnostics');
  expect(await diagnostics.getAttribute('open')).toBeNull();
  await diagnostics.locator('summary').click();
  await expect(diagnostics).toContainText('Diagnostic score: 100');
  await expect(diagnostics).toContainText('Matched check: confirm address update');
  await expect(diagnostics).not.toContainText('Observed:');
  await expect(page.getByLabel('Report brief')).toContainText('Evidence status: Outcome unverified');
  await expect(flow.locator('[aria-current="step"]')).toContainText('Request LLM review');
  expect(calls.executions()).toBe(0); expect(calls.judges()).toBe(0);
});

test('changing the scenario retains the imported conversation and source instead of loading another sample', async ({ page, request }) => {
  const calls = await setup(page, request);
  const original = await page.getByLabel('Evidence transcript').inputValue();
  await evaluate(page);
  await page.getByLabel('Evaluation scenario', { exact: true }).selectOption('interruption-correction-handling');
  await expect(page.getByLabel('Evidence transcript')).toHaveValue(original);
  await expect(page.getByLabel('Include structured evidence in Evaluate')).toBeChecked();
  await expect(page.getByLabel('Evaluation evidence summary')).toHaveCount(0);
  const report = await evaluate(page);
  expect(report.scenario_id).toBe('interruption-correction-handling');
  expect(report.evaluation_scope.input_source).toBe('vcon');
  expect(calls.executions()).toBe(0); expect(calls.judges()).toBe(0);
});

test('a sample deep link cannot overwrite pasted evidence when the operator changes criteria', async ({ page, request }) => {
  await setup(page, request);
  await page.goto('/eval?suite_id=call-center-voice-ai&scenario_id=billing-address-change&sample=1');
  await expect(page.getByLabel('Evaluation scenario', { exact: true })).toBeEnabled();
  const transcript = 'Caller: I need assistance with an unusual request.\nAgent: Please explain the issue.';
  await page.getByLabel('Evidence transcript').fill(transcript);
  await page.getByLabel('Evaluation scenario', { exact: true }).selectOption('angry-outage-escalation');
  await expect(page.getByLabel('Evidence transcript')).toHaveValue(transcript);
  const report = await evaluate(page);
  expect(report.evaluation_scope.input_source).toBe('transcript');
  expect(report.evaluation_scope.artifact_presence.action_trace).toBe(false);
});

test('a separate successful judge review does not manufacture tool evidence or promote the verdict', async ({ page, request }) => {
  const calls = await setup(page, request);
  await page.route('**/api/assert/benchmarks/*/judge', (route) => route.fulfill({ json: {
    status: 'ready', provider: 'assert-ai', engine: 'assert', required_plan: 'starter', credits: 10, evidence_citations: [],
    review_id: 'review-fixture', execution_run_id: 'import-fixture', message: 'Fixture semantic review completed.',
    judge_result: { agrees: true, rationale: 'Conversation content matches; no backend receipt is supplied.' },
  } }));
  await evaluate(page);
  await page.getByRole('button', { name: 'Request LLM judge', exact: true }).click();
  await expect(page.getByLabel('Evaluation evidence summary')).toContainText('Review recorded separately');
  await expect(page.getByRole('heading', { name: 'Outcome unverified', exact: true })).toBeVisible();
  await expect(page.getByLabel('Evaluation evidence summary')).toContainText('Evaluator: needs review');
  expect(calls.executions()).toBe(0); expect(calls.judges()).toBe(1);
});

test('a late judge response cannot attach to edited evidence', async ({ page, request }) => {
  await setup(page, request);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/assert/benchmarks/*/judge', async (route) => {
    await gate; await route.fulfill({ json: { status: 'ready', review_id: 'old-review', evidence_citations: [], message: 'STALE REVIEW MUST NOT APPEAR', judge_result: { agrees: true } } }).catch(() => undefined);
  });
  await evaluate(page);
  await page.getByRole('button', { name: 'Request LLM judge', exact: true }).click();
  await expect(page.getByLabel('Evaluation evidence summary')).toContainText('Review in progress');
  await page.getByLabel('Evidence transcript').fill('Caller: Please help.\nAgent: What do you need?');
  await expect(page.getByLabel('Evaluation evidence summary')).toHaveCount(0);
  release();
  await evaluate(page);
  await expect(page.getByLabel('Evaluation evidence summary')).toContainText('No completed review recorded');
  await expect(page.getByText('STALE REVIEW MUST NOT APPEAR')).toHaveCount(0);
});

test('structured synthetic sample exposes its limitations, and opting out removes imported provenance', async ({ page, request }) => {
  await setup(page, request, true);
  const report = await evaluate(page);
  expect(report.evaluation_scope.synthetic).toBe(true);
  await expect(page.getByLabel('Evaluation evidence summary')).toContainText('Synthetic evidence — not a verified real-world outcome');
  await expect(page.getByLabel('Evaluation evidence summary')).toContainText('not independently authenticated');
  await page.getByText('Structured and channel evidence (optional)', { exact: true }).click();
  await page.getByLabel('Include structured evidence in Evaluate').uncheck();
  await expect(page.getByLabel('Evaluation evidence summary')).toHaveCount(0);
  const next = await evaluate(page);
  expect(next.evaluation_scope.input_source).toBe('transcript');
  expect(next.evaluation_scope.artifact_presence.action_trace).toBe(false);
  await expect(page.getByLabel('Evaluation evidence summary')).toContainText('Outcome unverified');
  await expect(page.getByLabel('Evaluation evidence summary')).not.toContainText('Imported vCon');
});

for (const width of [1280, 390, 320]) test(`evidence-first result and flow stay readable and keyboard-operable at ${width}px`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await setup(page, request); await evaluate(page);
  await expect(page.getByRole('heading', { name: 'Outcome unverified', exact: true })).toBeVisible();
  const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')].flatMap((element) => {
    const box = element.getBoundingClientRect();
    return box.right > window.innerWidth + 1 && box.width > 0 ? [{ tag: element.tagName, className: element.className, text: element.textContent?.slice(0, 65), right: box.right, width: box.width, parent: element.parentElement?.getAttribute('aria-label'), columns: getComputedStyle(element).gridTemplateColumns }] : [];
  }).sort((a, b) => b.right - a.right).slice(0, 18));
  expect(await page.evaluate(() => document.documentElement.scrollWidth), JSON.stringify(overflow)).toBeLessThanOrEqual(width);
  const control = page.getByLabel('Evaluation diagnostics').locator('summary');
  await control.focus(); await page.keyboard.press('Enter');
  await expect(page.getByLabel('Evaluation diagnostics')).toHaveAttribute('open', '');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  await page.getByText('Structured and channel evidence (optional)', { exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  if (process.env.CAE_EVIDENCE_SCREENSHOTS) {
    await page.getByLabel('Evaluation evidence summary').screenshot({ path: `${process.env.CAE_EVIDENCE_SCREENSHOTS}/evidence-summary-${width}.png` });
    await page.getByLabel('Imported evidence evaluation flow').screenshot({ path: `${process.env.CAE_EVIDENCE_SCREENSHOTS}/evidence-flow-${width}.png` });
  }
});
