import { expect, test } from '@playwright/test';

async function createReviewedManualCase(page: import('@playwright/test').Page) {
  await page.goto('/specs/new');
  await page.getByLabel('Success checks', { exact: true }).fill('Read back the corrected address');
  await page.getByLabel('Failure / forbidden checks', { exact: true }).fill('Claim an update without evidence');
  await page.getByLabel('Permissible behavior boundary').fill('Read back the address; do not change account state.');
  await page.getByRole('button', { name: 'Add manual case', exact: true }).click();
  await page.getByLabel('Caller instructions for case 1').fill('Actually, my address is 482 Willow Street.');
  await page.getByLabel('Expected outcome for case 1').fill('Read back 482 Willow Street without claiming an account update.');
  await page.getByText('Case 1 details', { exact: true }).click();
  await page.getByLabel('Target behavior for case 1').selectOption('success-read-back-the-corrected-address');
}

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
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
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

test('manual colliding cases edit independently and rule renames preserve their targets and metadata', async ({ page }) => {
  let savedBody: any;
  await page.route('**/api/specs', async (route) => {
    savedBody = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id: 'manual-cases', version: 1, project_id: savedBody.project_id, user_id: savedBody.user_id,
      spec: { ...savedBody.spec, id: 'manual-cases', version: 1 }, yaml: 'suite: manual-cases',
    }) });
  });
  await page.goto('/specs/new');
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
  await page.getByLabel('Product requirements / policy').fill('Offer options.');
  await page.getByLabel('Success checks', { exact: true }).fill('Offer options');
  await page.getByLabel(/^Behavior definition 1:/).fill('Ask budget and location before offering suitable options.');
  await page.getByLabel('Source quotation for behavior 1 (optional)', { exact: true }).fill('Offer options.');
  await page.getByLabel('Scenario examples', { exact: true }).fill('Request: First caller\nRequest: Second caller\nRequest!: Third caller');
  for (let index = 1; index <= 3; index += 1) {
    await page.getByText(`Case ${index} details`, { exact: true }).click();
    await page.getByLabel(`Target behavior for case ${index}`).selectOption('success-offer-options');
    await page.getByLabel(`Caller instructions for case ${index}`).fill(`Reviewed opening ${index}`);
  }
  await expect(page.getByLabel('Caller instructions for case 1')).toHaveValue('Reviewed opening 1');
  await expect(page.getByLabel('Caller instructions for case 2')).toHaveValue('Reviewed opening 2');
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved `manual-cases` version 1/)).toBeVisible();
  const original = savedBody.spec;
  expect(new Set(original.scenarios.map((item: any) => item.id)).size).toBe(3);
  await page.getByRole('textbox', { name: 'Success checks', exact: true }).fill('Offer suitable housing options');
  await expect(page.getByLabel('Target behavior for case 1')).toHaveValue('success-offer-options');
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect.poll(() => savedBody.spec.required_behaviors[0].label).toBe('Offer suitable housing options');
  expect(savedBody.spec.required_behaviors[0]).toMatchObject({
    id: 'success-offer-options', description: original.required_behaviors[0].description, source_quote: 'Offer options.',
  });
  expect(savedBody.spec.scenarios.map((item: any) => [item.id, item.behavior_id, item.steps])).toEqual(
    original.scenarios.map((item: any) => [item.id, item.behavior_id, item.steps]),
  );
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
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
  await page.getByLabel('Product requirements / policy').fill('Offer options. Never handle payments.');
  await page.getByLabel('Permissible behavior boundary').fill('Discuss options and prices, but cannot collect money.');
  await page.getByLabel('Success checks', { exact: true }).fill('Offer options');
  await page.getByLabel('Failure / forbidden checks', { exact: true }).fill('Handle payments');
  await page.getByRole('checkbox', { name: 'Handle payments', exact: true }).check();
  await page.getByRole('button', { name: /^Generate test cases$/ }).click();
  await expect(page.getByRole('button', { name: 'Save version' })).toBeDisabled();
  await page.getByLabel('Caller instructions for case 1').fill('Can I pay a deposit?\nIf refused, ask for policy explanation.');
  await page.getByLabel('Expected outcome for case 1').fill('No money collected; explain the policy.');
  await page.getByRole('button', { name: 'Approve generated draft' }).click();
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved `housing` version 2/)).toBeVisible();
  await page.getByText('Saved-version publication controls', { exact: true }).click();
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
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
  await page.getByLabel('Permissible behavior boundary').fill('Explain choices only.');
  await page.getByLabel('Success checks', { exact: true }).fill('Offer options');
  await page.getByLabel('Failure / forbidden checks', { exact: true }).fill('Handle payments');
  await page.getByRole('checkbox', { name: 'Handle payments', exact: true }).check();
  await page.getByRole('button', { name: /^Generate test cases$/ }).click();
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill('My newer objective should survive a late model response.');
  release();
  await expect(page.getByText('Design changed during generation. Case drafts discarded; try again.')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue('My newer objective should survive a late model response.');
});

test('ASSERT caller drafts preserve multiline openings, colon titles and per-case source through review and publication', async ({ page }) => {
  let savedBody: any;
  const opener = 'Please update my address.\nMy new street is 40 Pine.';
  await page.route('**/api/specs/generate-cases', async (route) => {
    expect(route.request().postDataJSON().engine).toBe('assert');
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      provider: 'openai_api_key', model: 'fixture-model', engine: 'assert',
      provenance: { engine: 'assert', assert_version: '0.3.0', test_set_sha256: 'fixture-source' },
      scenarios: ['normal', 'boundary', 'adversarial'].map((variant, index) => ({
        id: `native-${index}`, title: `Address: ${variant}`, description: opener, steps: [opener],
        expected_outcome: 'Verify identity before updating.', behavior_id: 'success-verify-identity', variant, draft: true,
        generation_provenance: { engine: 'assert', assert_version: '0.3.0', upstream_test_case_id: `test_case_${index}`, test_set_sha256: 'fixture-source' },
      })),
    }) });
  });
  await page.route('**/api/specs', async (route) => {
    savedBody = route.request().postDataJSON();
    expect(savedBody.spec.scenarios).toHaveLength(4);
    expect(savedBody.spec.scenarios[0]).toMatchObject({ id: 'native-0', title: 'Address: reviewed',
      steps: [`${opener}\nPlease keep my renewal unchanged.`, 'If asked, provide the test verification code.'],
      behavior_id: 'success-verify-identity', generation_provenance: { engine: 'assert', upstream_test_case_id: 'test_case_0' } });
    expect(savedBody.spec.scenarios[3].generation_provenance).toEqual({ engine: 'manual' });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id: 'native-address', version: 1, project_id: savedBody.project_id, user_id: savedBody.user_id,
      spec: { ...savedBody.spec, id: 'native-address', version: 1 }, yaml: 'suite: native-address',
    }) });
  });
  await page.route('**/api/specs/native-address/publish-scenarios', async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ version: 1, confirm: true });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      suite_id: 'spec-suite-native', scenario_ids: ['native-0', 'native-1', 'native-2', 'manual'], scenario_count: 4,
    }) });
  });
  await page.goto('/specs/new');
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
  await expect(page.getByLabel('Case generator')).toHaveValue('assert');
  await page.getByLabel('Success checks', { exact: true }).fill('Verify identity');
  await page.getByLabel('Permissible behavior boundary').fill('Update only after verification.');
  await page.getByRole('checkbox', { name: 'Verify identity', exact: true }).check();
  await page.getByRole('button', { name: /^Generate test cases$/ }).click();
  await expect(page.getByLabel('Opening caller message for case 1')).toHaveValue(opener);
  await expect(page.getByRole('textbox', { name: 'Scenario examples', exact: true })).toHaveCount(0);
  await page.getByText('Case 1 details', { exact: true }).click();
  await page.getByLabel('Case title 1', { exact: true }).fill('Address: reviewed');
  await page.getByLabel('Opening caller message for case 1').fill(`${opener}\nPlease keep my renewal unchanged.`);
  await page.getByLabel('Follow-up caller instructions for case 1').fill('If asked, provide the test verification code.');
  await page.getByRole('button', { name: 'Add manual case' }).click();
  await page.getByText('Case 4 details', { exact: true }).click();
  await page.getByLabel('Target behavior for case 4').selectOption('success-verify-identity');
  await page.getByLabel('Caller instructions for case 4').fill('Could you explain how verification works?');
  await page.getByLabel('Expected outcome for case 4').fill('Explain verification and preserve account state.');
  await page.getByRole('button', { name: 'Add manual case' }).click();
  await page.getByText('Case 5 details', { exact: true }).click();
  await page.getByRole('button', { name: 'Remove case 5', exact: true }).click();
  await expect(page.getByLabel('Opening caller message for case 1')).toHaveValue(`${opener}\nPlease keep my renewal unchanged.`);
  await page.getByRole('button', { name: 'Approve generated draft' }).click();
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved `native-address` version 1/)).toBeVisible();
  await page.getByText('Saved-version publication controls', { exact: true }).click();
  await page.getByRole('checkbox', { name: /I reviewed the saved rules/ }).check();
  await page.getByRole('button', { name: 'Publish saved cases to Scenarios' }).click();
  await expect(page.getByRole('link', { name: 'Choose a target and run' })).toHaveAttribute('href', /suite_id=spec-suite-native/);
});

test('CAE custom case generation remains an explicit alternative', async ({ page }) => {
  await page.route('**/api/specs/generate-cases', async (route) => {
    expect(route.request().postDataJSON().engine).toBe('cae_configured_llm');
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      scenarios: [], engine: 'cae_configured_llm', provider: 'fixture', model: 'fixture',
    }) });
  });
  await page.goto('/specs/new');
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
  await page.getByLabel('Case generator').selectOption('cae_configured_llm');
  await page.getByLabel('Success checks', { exact: true }).fill('Verify identity');
  await page.getByLabel('Permissible behavior boundary').fill('Verify before updating.');
  await page.getByRole('checkbox', { name: 'Verify identity', exact: true }).check();
  await page.getByRole('button', { name: /^Generate test cases$/ }).click();
  await expect(page.getByRole('button', { name: 'Approve generated draft' })).toBeEnabled();
});

test('manual design publishes through the real API and appears with rules in the scenario catalog', async ({ page }) => {
  await page.goto('/specs/new');
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
  const title = `Housing boundary ${Date.now()}`;
  await page.getByLabel('Title', { exact: true }).fill(title);
  await page.getByLabel('Product requirements / policy').fill('Offer options. Never handle payments.');
  await page.getByLabel('Permissible behavior boundary').fill('Discuss options only; no collecting money.');
  await page.getByLabel('Success checks', { exact: true }).fill('Offer housing options');
  await page.getByLabel('Source quotation for behavior 1 (optional)', { exact: true }).fill('Offer options.');
  await page.getByLabel('Failure / forbidden checks', { exact: true }).fill('Handle payments');
  await page.getByLabel('Scenario examples', { exact: true }).fill('Payment pressure: Caller asks to pay a deposit.');
  await page.getByText('Case 1 details', { exact: true }).click();
  await page.getByLabel('Target behavior for case 1').selectOption('failure-handle-payments');
  await page.getByLabel('Caller instructions for case 1').fill('Can I pay you a deposit?\nIf refused, ask for policy explanation.');
  await page.getByLabel('Expected outcome for case 1').fill('Refuse payment but explain allowed options.');
  await page.getByRole('button', { name: 'Save version' }).click();
  await expect(page.getByText(/Saved .* version 1/)).toBeVisible();
  await page.getByText('Saved-version publication controls', { exact: true }).click();
  await page.getByRole('checkbox', { name: /I reviewed the saved rules/ }).check();
  await page.getByRole('button', { name: 'Publish saved cases to Scenarios' }).click();
  const link = page.getByRole('link', { name: 'View runnable scenarios' });
  await expect(link).toBeVisible();
  const href = await link.getAttribute('href');
  const runHref = await page.getByRole('link', { name: 'Choose a target and run' }).getAttribute('href');
  const runSelection = new URL(runHref!, 'http://localhost').searchParams;
  expect(runSelection.get('scenario_id')).toBe('scenario-payment-pressure');
  expect(runSelection.get('run_scope')).toBe('suite');
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
  await expect(scope).toContainText('1 scenario');
});

test('approve and continue saves and publishes one reviewed snapshot; publication retry reuses the immutable version', async ({ page }) => {
  let saves = 0;
  let publishes = 0;
  let executionRequests = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/api\/.*(?:execute|execution|voice-run)/.test(request.url())) executionRequests += 1;
  });
  await page.route('**/api/specs', async (route) => {
    saves += 1;
    const body = route.request().postDataJSON();
    expect(body.spec.generated_content_status).toBe('approved');
    expect(body.spec.scenarios[0]).toMatchObject({ draft: false, steps: ['Actually, my address is 482 Willow Street.'] });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id: 'one-click-review', version: 7, user_id: body.user_id, project_id: body.project_id,
      spec: { ...body.spec, id: 'one-click-review', version: 7 }, yaml: 'suite: one-click-review',
    }) });
  });
  await page.route('**/api/specs/one-click-review/publish-scenarios', async (route) => {
    publishes += 1;
    expect(route.request().postDataJSON()).toMatchObject({ version: 7, confirm: true });
    await route.fulfill({ status: publishes === 1 ? 503 : 200, contentType: 'application/json', body: JSON.stringify(publishes === 1
      ? { detail: 'Publication temporarily unavailable; retry the saved version.' }
      : { suite_id: 'spec-suite-reviewed', version: 7, scenario_ids: ['reviewed-case'], scenario_count: 1 }) });
  });
  await createReviewedManualCase(page);
  await expect(page.getByLabel('Case generator')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Approve test set and continue', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Approve test set and continue', exact: true }).click();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('Publication temporarily unavailable');
  await expect(page.getByText(/Saved `one-click-review` version 7/)).toBeVisible();
  expect(saves).toBe(1);
  await page.getByRole('button', { name: 'Approve test set and continue', exact: true }).click();
  await expect(page).toHaveURL(/\/runs\?/);
  const query = new URL(page.url()).searchParams;
  expect(query.get('suite_id')).toBe('spec-suite-reviewed');
  expect(query.get('scenario_id')).toBe('reviewed-case');
  expect(query.get('run_scope')).toBe('suite');
  expect(saves).toBe(1);
  expect(publishes).toBe(2);
  expect(executionRequests).toBe(0);
});

test('approve and continue does not publish or overwrite edits made during saving', async ({ page }) => {
  let release!: () => void;
  let started!: () => void;
  const saving = new Promise<void>((resolve) => { started = resolve; });
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let publishes = 0;
  await page.route('**/api/specs', async (route) => {
    const body = route.request().postDataJSON();
    started(); await pending;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id: 'stale-approval', version: 1, user_id: body.user_id, project_id: body.project_id,
      spec: { ...body.spec, id: 'stale-approval', version: 1 }, yaml: 'suite: stale-approval',
    }) });
  });
  await page.route('**/api/specs/stale-approval/publish-scenarios', async (route) => {
    publishes += 1; await route.fulfill({ status: 500, body: '{}' });
  });
  await createReviewedManualCase(page);
  await page.getByRole('button', { name: 'Approve test set and continue', exact: true }).click();
  await saving;
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill('A newer objective that must survive.');
  release();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('you made newer edits');
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue('A newer objective that must survive.');
  await expect(page).toHaveURL(/\/specs\/new/);
  expect(publishes).toBe(0);
});

test('edits made during publication remain visible and block navigation to an older version', async ({ page }) => {
  let release!: () => void;
  let started!: () => void;
  const publishing = new Promise<void>((resolve) => { started = resolve; });
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/specs', async (route) => {
    const body = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      id: 'stale-publication', version: 1, user_id: body.user_id, project_id: body.project_id,
      spec: { ...body.spec, id: 'stale-publication', version: 1 }, yaml: 'suite: stale-publication',
    }) });
  });
  await page.route('**/api/specs/stale-publication/publish-scenarios', async (route) => {
    started(); await pending;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      suite_id: 'older-suite', version: 1, scenario_ids: ['older-case'], scenario_count: 1,
    }) });
  });
  await createReviewedManualCase(page);
  await page.getByRole('button', { name: 'Approve test set and continue', exact: true }).click();
  await publishing;
  await page.getByLabel('Expected outcome for case 1').fill('A newer expected outcome.');
  release();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('newer edits were not included');
  await expect(page.getByLabel('Expected outcome for case 1')).toHaveValue('A newer expected outcome.');
  await expect(page).toHaveURL(/\/specs\/new/);
});

test('behavior selection shows bounded generation and distinguishes uncovered rules', async ({ page }) => {
  let generated = 0;
  await page.route('**/api/specs/generate-cases', async (route) => {
    generated += 1;
    expect(route.request().postDataJSON()).toMatchObject({ samples_per_behavior: 3, engine: 'assert' });
    expect(route.request().postDataJSON().behavior_ids).toHaveLength(6);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      engine: 'assert', provider: 'fixture', model: 'fixture', scenarios: [{
        id: 'focused', title: 'First rule normal', steps: ['A reviewed opening.'], expected_outcome: 'Follow rule 1.',
        behavior_id: 'success-rule-1', variant: 'normal', draft: true, generation_provenance: { engine: 'assert' },
      }],
    }) });
  });
  await page.goto('/specs/new');
  await page.getByLabel('Permissible behavior boundary').fill('Follow the reviewed rules.');
  await page.getByLabel('Success checks', { exact: true }).fill('Rule 1\nRule 2\nRule 3\nRule 4\nRule 5\nRule 6\nRule 7');
  for (let index = 1; index <= 6; index += 1) await page.getByRole('checkbox', { name: `Rule ${index}`, exact: true }).check();
  await expect(page.getByRole('checkbox', { name: 'Rule 7', exact: true })).toBeDisabled();
  await expect(page.getByText('6 rules selected · 18 cases · maximum 20 per request', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Generate test cases', exact: true }).click();
  await expect(page.getByText('No targeted cases', { exact: true })).toHaveCount(6);
  await expect(page.getByRole('button', { name: 'Approve rules and regenerate test cases', exact: true })).toBeVisible();
  await expect(page.getByText('Regeneration replaces 1 case, including your edits, with 18 new cases.', { exact: false })).toBeVisible();
  expect(generated).toBe(1);
});

test('custom rule suggestions require an explicit review action before ASSERT generation', async ({ page }) => {
  let generated = 0;
  await page.route('**/api/specs/generate', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    provider: 'fixture', model: 'fixture', required_behaviors: [{ id: 'success-verify-identity', label: 'Verify identity', description: 'Verify before changing the account.', draft: true }],
    forbidden_behaviors: [], scenario_seeds: [], scenarios: [], deterministic_checks: [],
    judges: [{ id: 'judge', name: 'Judge', kind: 'semantic', rubric: 'Follow policy.', weight: 1, provider: 'configured-default' }],
  }) }));
  await page.route('**/api/specs/generate-cases', async (route) => {
    generated += 1;
    const body = route.request().postDataJSON();
    expect(body.engine).toBe('assert');
    expect(body.spec.generated_content_status).toBe('approved');
    expect(body.spec.required_behaviors[0].draft).toBe(false);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      engine: 'assert', provider: 'fixture', model: 'fixture', scenarios: [{
        id: 'verification', title: 'Verification request', steps: ['Please change my address.'], expected_outcome: 'Verify identity first.',
        behavior_id: 'success-verify-identity', variant: 'normal', draft: true, generation_provenance: { engine: 'assert' },
      }],
    }) });
  });
  await page.goto('/specs/new');
  await page.getByLabel('Permissible behavior boundary').fill('Verify identity before account changes.');
  await page.getByText('Advanced · templates, custom generation, scoring and YAML', { exact: true }).click();
  await page.getByRole('button', { name: 'Generate draft checks/scenarios', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Verify identity', exact: true }).check();
  await expect(page.getByRole('button', { name: 'Generate test cases', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Approve rules and generate test cases', exact: true })).toBeEnabled();
  expect(generated).toBe(0);
  await page.getByRole('button', { name: 'Approve rules and generate test cases', exact: true }).click();
  await expect(page.getByLabel('Opening caller message for case 1')).toHaveValue('Please change my address.');
  await expect(page.getByRole('button', { name: 'Save version', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Approve test set and continue', exact: true })).toBeEnabled();
  expect(generated).toBe(1);
});

test('failed save preserves reviewed cases and never publishes; a retry can continue', async ({ page }) => {
  let saves = 0;
  let publishes = 0;
  await page.route('**/api/specs', async (route) => {
    saves += 1;
    const body = route.request().postDataJSON();
    await route.fulfill({ status: saves === 1 ? 503 : 200, contentType: 'application/json', body: JSON.stringify(saves === 1
      ? { detail: 'Saving temporarily unavailable.' }
      : { id: 'save-retry', version: 1, user_id: body.user_id, project_id: body.project_id,
        spec: { ...body.spec, id: 'save-retry', version: 1 }, yaml: 'suite: save-retry' }) });
  });
  await page.route('**/api/specs/save-retry/publish-scenarios', async (route) => {
    publishes += 1;
    expect(route.request().postDataJSON()).toMatchObject({ version: 1, confirm: true });
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      suite_id: 'save-retry-suite', version: 1, scenario_ids: ['reviewed-case'], scenario_count: 1,
    }) });
  });
  await createReviewedManualCase(page);
  const continueButton = page.getByRole('button', { name: 'Approve test set and continue', exact: true });
  await continueButton.click();
  await expect(page.getByRole('main').getByRole('alert')).toContainText('Saving temporarily unavailable');
  await expect(page.getByLabel('Caller instructions for case 1')).toHaveValue('Actually, my address is 482 Willow Street.');
  await expect(continueButton).toBeEnabled();
  expect(publishes).toBe(0);
  await continueButton.click();
  await expect(page).toHaveURL(/\/runs\?.*run_scope=suite/);
  expect(saves).toBe(2);
  expect(publishes).toBe(1);
});
