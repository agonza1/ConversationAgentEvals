import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  for (const [path, payload] of [
    ['templates', { templates: [] }],
    ['assert-library/behaviors', { behaviors: [] }],
    ['assert-library/judges', { judges: [] }],
    ['assert-library/scenarios', { scenarios: [] }],
  ] as const) {
    await page.route(`**/api/specs/${path}`, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) }));
  }
  await page.route('**/api/specs/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ valid: true, errors: [], warnings: [], yaml: 'suite: housing', assert_validated: true }) }));
});

test('manual duplicate and slug-colliding labels retain distinct editable rule identities', async ({ page }) => {
  let savedBody: any;
  await page.route('**/api/specs', async (route) => {
    savedBody = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id: 'duplicate-rules', version: 1, project_id: savedBody.project_id, user_id: savedBody.user_id,
      spec: { ...savedBody.spec, id: 'duplicate-rules', version: 1 }, yaml: 'suite: duplicate-rules',
    }) });
  });
  await page.goto('/specs/new');
  await page.getByLabel('Success checks', { exact: true }).fill('Check policy\nCheck policy\nCheck policy!\nCheck policy-2');
  await page.getByLabel(/^Behavior definition 1:/).fill('Check the budget policy.');
  await page.getByLabel(/^Behavior definition 2:/).fill('Check identity before changing an account.');
  await expect(page.getByLabel(/^Behavior definition 1:/)).toHaveValue('Check the budget policy.');
  await expect(page.getByLabel(/^Behavior definition 2:/)).toHaveValue('Check identity before changing an account.');
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved `duplicate-rules` version 1/)).toBeVisible();
  const original = savedBody.spec.required_behaviors;
  expect(new Set(original.map((rule: any) => rule.id)).size).toBe(4);
  expect(original[0].description).toBe('Check the budget policy.');
  expect(original[1].description).toBe('Check identity before changing an account.');

  await page.getByRole('textbox', { name: 'Success checks', exact: true }).fill('Check policy!\nCheck policy\nCheck policy\nCheck policy-2\nCheck policy');
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect.poll(() => savedBody.spec.required_behaviors.length).toBe(5);
  const revised = savedBody.spec.required_behaviors;
  expect(new Set(revised.map((rule: any) => rule.id)).size).toBe(5);
  expect(revised[0].id).toBe(original[2].id);
  expect(revised[1].id).toBe(original[0].id);
  expect(revised[2].id).toBe(original[1].id);
  expect(revised[2].description).toBe('Check identity before changing an account.');
});

test('review, generate cases, edit, save, and publish an exact version; unsaved edits block publication', async ({ page }) => {
  let savedBody: any;
  let publishCount = 0;
  await page.route('**/api/specs/generate-cases', async (route) => {
    const body = route.request().postDataJSON();
    expect(body.spec.permissible_behavior).toContain('Discuss options');
    expect(body.behavior_ids).toEqual(['failure-handle-payments']);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      provider: 'fake-codex', model: 'fake-model', engine: 'cae_configured_llm', scenarios: ['normal', 'boundary', 'adversarial'].map((variant) => ({
        id: variant, title: `${variant} payment case`, description: 'A caller asks about paying.',
        steps: ['Can I pay a deposit?'], expected_outcome: 'Explain policy, never collect money.',
        behavior_id: 'failure-handle-payments', variant, draft: true,
      })),
    }) });
  });
  await page.route('**/api/specs', async (route) => {
    savedBody = route.request().postDataJSON();
    expect(savedBody.spec.generated_content_status).toBe('approved');
    expect(savedBody.spec.scenarios[0].steps).toHaveLength(2);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id: 'housing', version: 2, project_id: savedBody.project_id, user_id: savedBody.user_id,
      spec: { ...savedBody.spec, id: 'housing', version: 2 }, yaml: 'suite: housing',
    }) });
  });
  await page.route('**/api/specs/housing/publish-scenarios', async (route) => {
    publishCount += 1;
    expect(route.request().postDataJSON()).toMatchObject({ version: 2, confirm: true });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ suite_id: 'spec-suite-test', version: 2, scenario_ids: ['normal', 'boundary', 'adversarial'], scenario_count: 3 }) });
  });
  await page.goto('/specs/new');
  await page.getByLabel('Product requirements / policy').fill('Offer options. Never handle payments.');
  await page.getByLabel('Permissible behavior boundary').fill('Discuss options and prices, but cannot collect money.');
  await page.getByLabel('Success checks', { exact: true }).fill('Offer options');
  await page.getByLabel('Failure / forbidden checks', { exact: true }).fill('Handle payments');
  await page.getByLabel('Behaviors to cover').selectOption('failure-handle-payments');
  await page.getByRole('button', { name: 'Generate runnable case drafts' }).click();
  await expect(page.getByRole('button', { name: 'Save version' })).toBeDisabled();
  await page.getByLabel('Caller instructions for case 1').fill('Can I pay a deposit?\nIf refused, ask for policy explanation.');
  await page.getByLabel('Expected outcome for case 1').fill('No money collected; explain the policy.');
  await page.getByRole('button', { name: 'Approve generated draft' }).click();
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved `housing` version 2/)).toBeVisible();
  await page.getByRole('checkbox', { name: /I reviewed the saved rules/ }).check();
  const publish = page.getByRole('button', { name: 'Publish saved cases to Scenarios' });
  await expect(publish).toBeEnabled();
  await page.getByLabel('Product requirements / policy').fill('Offer options. Never handle payments. New policy.');
  await expect(publish).toBeDisabled();
  expect(publishCount).toBe(0);
  await page.getByLabel('Product requirements / policy').fill('Offer options. Never handle payments.');
  await publish.click();
  await expect(page.getByRole('link', { name: 'View runnable scenarios' })).toHaveAttribute('href', /suite_id=spec-suite-test/);
  const runHref = await page.getByRole('link', { name: 'Choose a target and run' }).getAttribute('href');
  expect(new URL(runHref!, 'http://localhost').searchParams.get('suite_id')).toBe('spec-suite-test');
  expect(new URL(runHref!, 'http://localhost').searchParams.get('scenario_id')).toBe('normal');
  expect(publishCount).toBe(1);
});

test('late generated cases cannot overwrite edits made while the model is responding', async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/specs/generate-cases', async (route) => {
    await pending;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ scenarios: [], provider: 'fake', model: 'fake', engine: 'cae_configured_llm' }) });
  });
  await page.goto('/specs/new');
  await page.getByLabel('Permissible behavior boundary').fill('Explain choices only.');
  await page.getByLabel('Success checks', { exact: true }).fill('Offer options');
  await page.getByLabel('Failure / forbidden checks', { exact: true }).fill('Handle payments');
  await page.getByLabel('Behaviors to cover').selectOption('failure-handle-payments');
  await page.getByRole('button', { name: 'Generate runnable case drafts' }).click();
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill('My newer objective should survive a late model response.');
  release();
  await expect(page.getByText('Design changed during generation. Case drafts discarded; try again.')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue('My newer objective should survive a late model response.');
});

test('manual design publishes through the real API and appears with rules in the scenario catalog', async ({ page }) => {
  await page.goto('/specs/new');
  const title = `Housing boundary ${Date.now()}`;
  await page.getByLabel('Title', { exact: true }).fill(title);
  await page.getByLabel('Product requirements / policy').fill('Offer options. Never handle payments.');
  await page.getByLabel('Permissible behavior boundary').fill('Discuss options only; no collecting money.');
  await page.getByLabel('Success checks', { exact: true }).fill('Offer housing options');
  await page.getByLabel('Source quotation for behavior 1 (optional)', { exact: true }).fill('Offer options.');
  await page.getByLabel('Failure / forbidden checks', { exact: true }).fill('Handle payments');
  await page.getByLabel('Scenario examples', { exact: true }).fill('Payment pressure: Caller asks to pay a deposit.');
  await page.getByLabel('Target behavior for case 1').selectOption('failure-handle-payments');
  await page.getByLabel('Caller instructions for case 1').fill('Can I pay you a deposit?\nIf refused, ask for policy explanation.');
  await page.getByLabel('Expected outcome for case 1').fill('Refuse payment but explain allowed options.');
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved .* version 1/)).toBeVisible();
  await page.getByRole('checkbox', { name: /I reviewed the saved rules/ }).check();
  await page.getByRole('button', { name: 'Publish saved cases to Scenarios' }).click();
  const link = page.getByRole('link', { name: 'View runnable scenarios' });
  await expect(link).toBeVisible();
  const href = await link.getAttribute('href');
  const runHref = await page.getByRole('link', { name: 'Choose a target and run' }).getAttribute('href');
  const runSelection = new URL(runHref!, 'http://localhost').searchParams;
  expect(runSelection.get('scenario_id')).toBe('scenario-payment-pressure');
  await page.goto(href!);
  const detail = page.getByLabel('Selected scenario');
  await expect(detail.getByRole('heading', { name: 'Payment pressure', exact: true })).toBeVisible();
  await expect(detail.getByText('• Handle payments [failure-handle-payments]', { exact: true })).toBeVisible();
  await expect(detail.getByText('Evaluation design version', { exact: true })).toBeVisible();
  await expect(detail.getByText(/If refused, ask for policy explanation/)).toBeVisible();
  await expect(detail.getByText(/This case exercises behavior failure-handle-payments only/)).toBeVisible();
  await expect(detail.getByText('n/a — not this case’s focus.', { exact: true })).toBeVisible();
  await expect(detail.getByText('Complete policy context', { exact: true })).toBeVisible();
  await expect(detail.getByText(/required: Offer housing options \[success-offer-housing-options\]/)).toBeVisible();
  await page.goto(runHref!);
  const scope = page.getByLabel('Selected run scope', { exact: true });
  await expect(scope).toContainText(title);
  await expect(scope).toContainText('Payment pressure');
});
