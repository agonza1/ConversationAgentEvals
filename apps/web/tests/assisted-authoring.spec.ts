import { expect, test, type Page } from '@playwright/test';

function spec(scope = 'case') {
  return {
    title: 'Latest requested time', role: 'appointment agent', objective: 'Confirm the latest requested time without inventing a booking.',
    permissible_behavior: 'Clarify and repeat the request; booking capability is not assumed.', requirements: 'Use the latest time.',
    generated_content_status: 'draft', required_behaviors: [{ id: 'time', label: 'Confirm latest time', description: 'Repeat the final time.', draft: true }],
    forbidden_behaviors: [{ id: 'claim', label: 'Invent booking success', description: 'Do not claim an unconfirmed booking.', draft: true }],
    scenarios: (scope === 'case' ? ['normal'] : ['normal', 'boundary', 'adversarial']).map((variant) => ({
      id: variant, title: `${variant} time correction`, description: 'Caller corrects their time.', persona: 'A caller',
      steps: ['Please use 3 PM, not the earlier time.', 'If asked, confirm afternoon.'], expected_outcome: 'Confirm 3 PM without claiming a booking.',
      behavior_id: 'time', variant, draft: true,
    })), scenario_seeds: [], evidence_requirements: ['transcript', 'final_state_or_action_trace'],
    judges: [{ id: 'judge', name: 'Semantic judge', kind: 'semantic', rubric: 'Judge the evidence.', weight: 1, provider: 'configured-default' }],
  };
}
function result(scope = 'case') { return { spec: spec(scope), questions: [], assumptions: ['Booking capability is not assumed.'], provider: 'mock', model: 'small-test-model' }; }

async function setup(page: Page) {
  const writes: string[] = [];
  const requests: any[] = [];
  let created: any = null;
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (method !== 'GET' && !path.endsWith('/preview')) writes.push(path);
    if (path.endsWith('/api/specs/assist')) {
      const payload = route.request().postDataJSON(); requests.push(payload);
      return route.fulfill({ json: result(payload.scope) });
    }
    if (path.endsWith('/api/benchmarks/suites')) return route.fulfill({ json: [
      { id: 'built-in', title: 'Existing tests', scenarios: [{ id: 'base', title: 'Existing address case', simulated_user_prompt: 'My address changed.', required_actions: ['Repeat the address'], forbidden_actions: ['Invent a change'], expected_output: 'Confirm the address.' }] },
      ...(created ? [{ id: 'user-scenarios', title: 'User Scenarios', scenarios: [created] }] : []),
    ] });
    if (path.endsWith('/api/scenarios') && method === 'POST') {
      created = { ...route.request().postDataJSON(), id: 'new-case', suite_id: 'user-scenarios' };
      return route.fulfill({ json: created });
    }
    if (path.endsWith('/api/specs') && method === 'POST') {
      const payload = route.request().postDataJSON();
      return route.fulfill({ json: { ...payload, id: 'new-set', version: 1, spec: { ...payload.spec, id: 'new-set', version: 1 } } });
    }
    if (path.endsWith('/publish-scenarios')) return route.fulfill({ json: { suite_id: 'spec-suite-new', scenario_ids: ['normal', 'boundary', 'adversarial'], version: 1 } });
    if (path.endsWith('/preview')) return route.fulfill({ json: { valid: true, errors: [], warnings: [], yaml: 'draft', normalized: route.request().postDataJSON().spec } });
    if (path.endsWith('/templates')) return route.fulfill({ json: { templates: [] } });
    if (path.endsWith('/assert-library/behaviors')) return route.fulfill({ json: { behaviors: [] } });
    if (path.endsWith('/assert-library/judges')) return route.fulfill({ json: { judges: [] } });
    if (path.endsWith('/assert-library/scenarios')) return route.fulfill({ json: { scenarios: [] } });
    return route.fulfill({ json: [] });
  });
  return { writes, requests, created: () => created };
}

test('both entry points need one short request, and typing/starters never invoke generation', async ({ page }) => {
  const state = await setup(page);
  for (const url of ['/specs/new', '/scenarios?create=1']) {
    await page.goto(url);
    const builder = page.getByRole('region', { name: 'Assisted test builder' });
    await expect(builder.getByRole('textbox')).toHaveCount(1);
    await expect(builder.getByRole('button', { name: /Draft my/ })).toBeDisabled();
    await builder.getByRole('button', { name: 'Appointment changes', exact: true }).click();
    await expect(builder.getByRole('textbox')).toHaveValue(/requested appointment time twice/);
    await builder.getByRole('textbox').fill('Check that callers can correct the requested time.');
    expect(state.writes).toEqual([]);
    await builder.getByRole('button', { name: /Draft my/ }).click();
    await expect(builder.getByRole('heading', { name: 'Latest requested time' })).toBeVisible();
    await expect(builder.getByText('Suggested assumptions—please check')).toBeVisible();
    expect(state.writes.every((path) => path.endsWith('/specs/assist'))).toBe(true);
    // Reset per entry point, no save/publication/call has occurred.
    state.writes.length = 0;
  }
  expect(state.requests.map((request) => request.scope)).toEqual(['set', 'case']);
});

test('quick case explicitly saves exactly what was reviewed, including evidence and caller instructions', async ({ page }) => {
  const state = await setup(page);
  await page.goto('/scenarios?create=1&api_base=http%3A%2F%2Fapi.example.test');
  await page.getByRole('button', { name: 'Billing address', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my case' }).click();
  await page.getByText('Edit this case', { exact: true }).click();
  await page.getByRole('textbox', { name: 'Caller instructions', exact: true }).fill('Use 4 PM, not 3 PM.\nConfirm afternoon.');
  await page.getByRole('textbox', { name: 'Expected outcome', exact: true }).fill('Confirm 4 PM without inventing a booking.');
  expect(state.writes).toHaveLength(1);
  await page.getByRole('button', { name: 'Approve and create case' }).click();
  await expect(page.getByRole('region', { name: 'Selected scenario' }).getByRole('heading', { name: 'normal time correction' })).toBeVisible();
  expect(state.created()).toMatchObject({ simulated_user_prompt: 'A caller\nUse 4 PM, not 3 PM.\nConfirm afternoon.', expected_output: 'Confirm 4 PM without inventing a booking.',
    required_actions: ['Repeat the final time.'], forbidden_actions: ['Do not claim an unconfirmed booking.'], evidence_requirements: ['transcript', 'final_state_or_action_trace'] });
  expect(state.writes).toEqual(['/api/specs/assist', '/api/scenarios']);
  await expect(page.getByRole('link', { name: 'Test an agent', exact: true })).toHaveAttribute('href', /api_base=/);
});

test('test set approval saves an approved snapshot, publishes it once and only opens agent selection', async ({ page }) => {
  const state = await setup(page);
  let saved: any;
  await page.route('**/api/specs', async (route) => {
    saved = route.request().postDataJSON(); state.writes.push('/api/specs');
    await route.fulfill({ json: { ...saved, id: 'new-set', version: 1, spec: { ...saved.spec, id: 'new-set', version: 1 } } });
  });
  await page.goto('/specs/new?api_base=http%3A%2F%2Fapi.example.test');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my test set' }).click();
  expect(state.writes).toEqual(['/api/specs/assist']);
  await page.getByRole('button', { name: 'Approve test set and choose agent' }).click();
  await expect(page).toHaveURL(/\/runs\?.*suite_id=spec-suite-new/);
  expect(saved.spec.generated_content_status).toBe('approved');
  expect(saved.spec.scenarios.every((item: any) => !item.draft)).toBe(true);
  expect(state.writes).toEqual(['/api/specs/assist', '/api/specs', '/api/specs/new-set/publish-scenarios']);
  expect(new URL(page.url()).searchParams.get('api_base')).toBe('http://api.example.test');
});

test('consequential questions are clickable, block approval and carry answers into the next explicit request', async ({ page }) => {
  const state = await setup(page);
  let count = 0;
  await page.route('**/api/specs/assist', async (route) => {
    const payload = route.request().postDataJSON(); state.requests.push(payload); count++;
    await route.fulfill({ json: count === 1 ? { spec: null, assumptions: [], questions: [{ id: 'permission', question: 'Can the agent book appointments?', options: ['Collect requests only', 'Make bookings'] }] } : result() });
  });
  await page.goto('/scenarios?create=1');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my case' }).click();
  await expect(page.getByRole('button', { name: 'Approve and create case' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Use answers and draft' })).toBeDisabled();
  await page.getByRole('button', { name: 'Collect requests only', exact: true }).click();
  expect(count).toBe(1);
  await page.getByRole('button', { name: 'Use answers and draft' }).click();
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toBeVisible();
  expect(state.requests[1].answers).toEqual({ permission: 'Collect requests only' });
});

test('a failed publication retries the same approved version without a duplicate save', async ({ page }) => {
  const state = await setup(page);
  let attempts = 0;
  await page.route('**/publish-scenarios', (route) => {
    state.writes.push('/api/specs/new-set/publish-scenarios');
    return ++attempts === 1 ? route.fulfill({ status: 503, json: { detail: 'Temporary publication failure' } })
      : route.fulfill({ json: { suite_id: 'spec-suite-new', scenario_ids: ['normal'], version: 1 } });
  });
  await page.goto('/specs/new');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my test set' }).click();
  await page.getByRole('button', { name: 'Approve test set and choose agent' }).click();
  await expect(page.getByRole('region', { name: 'Assisted test builder' }).getByRole('alert')).toContainText('Temporary publication failure');
  await page.getByRole('button', { name: 'Approve test set and choose agent' }).click();
  await expect(page).toHaveURL(/\/runs\?/);
  expect(state.writes.filter((path) => path === '/api/specs')).toHaveLength(1);
  expect(attempts).toBe(2);
});

test('refinement includes manual edits, blocks approval of an outdated draft, and supports change review and undo', async ({ page }) => {
  const state = await setup(page);
  let count = 0;
  await page.route('**/api/specs/assist', async (route) => {
    const payload = route.request().postDataJSON(); state.requests.push(payload);
    const response = result(); if (++count > 1) response.spec.scenarios[0].expected_outcome = 'Confirm the corrected time after an interruption.';
    await route.fulfill({ json: response });
  });
  await page.goto('/scenarios?create=1');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my case' }).click();
  await page.getByText('Edit this case', { exact: true }).click();
  await page.getByRole('textbox', { name: 'Expected outcome', exact: true }).fill('My reviewed outcome must survive.');
  await page.getByRole('button', { name: 'Add interruptions', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Approve and create case' })).toBeDisabled();
  await page.getByRole('button', { name: 'Update draft' }).click();
  await expect(page.getByText(/Review changes ·/)).toBeVisible();
  expect(state.requests[1].current_spec.scenarios[0].expected_outcome).toBe('My reviewed outcome must survive.');
  await page.getByText(/Review changes ·/).click();
  await page.getByRole('button', { name: 'Undo last update' }).click();
  await expect(page.getByText('My reviewed outcome must survive.', { exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
});

test('variation reuses explicitly shown existing context with zero typing and does not save or run on draft', async ({ page }) => {
  const state = await setup(page);
  await page.goto('/scenarios');
  await page.getByRole('button', { name: 'Make a variation', exact: true }).click();
  await expect(page.getByText('Using context: Existing address case')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'What would you like to test?' })).toHaveValue(/Make a variation/);
  expect(state.writes).toEqual([]);
  await page.getByRole('button', { name: 'Draft my case' }).click();
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toBeVisible();
  expect(state.requests[0].context.text).toContain('Forbidden: Invent a change');
  expect(state.writes).toEqual(['/api/specs/assist']);
});

test('failed generation preserves the draft and manual fallback needs no generation connection', async ({ page }) => {
  await setup(page);
  await page.goto('/scenarios?create=1');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my case' }).click();
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toBeVisible();
  await page.route('**/api/specs/assist', (route) => route.fulfill({ status: 503, json: { detail: 'No generation connection.' } }));
  await page.getByRole('button', { name: 'Add interruptions', exact: true }).click();
  await page.getByRole('button', { name: 'Update draft' }).click();
  await expect(page.getByRole('region', { name: 'Assisted test builder' }).getByRole('alert')).toContainText('No generation connection.');
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toBeVisible();
  await page.getByRole('button', { name: 'Use detailed editor' }).click();
  await expect(page.getByLabel('User prompt / persona', { exact: true })).toHaveValue(/Please use 3 PM/);
  await expect(page.getByRole('textbox', { name: 'Expected outcome', exact: true })).toHaveValue('Confirm 3 PM without claiming a booking.');
});

test('cancelled late generation cannot replace the active draft or write anything', async ({ page }) => {
  const state = await setup(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/specs/assist', async (route) => { await pending; await route.fulfill({ json: result() }).catch(() => undefined); });
  await page.goto('/scenarios?create=1');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my case' }).click();
  await expect(page.getByRole('status')).toContainText('No call is starting');
  await page.getByRole('button', { name: 'Cancel drafting' }).click();
  await page.getByRole('textbox', { name: 'What would you like to test?' }).fill('My new request after cancelling the previous generation.');
  release();
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toHaveCount(0);
  await expect(page.getByRole('textbox')).toHaveValue('My new request after cancelling the previous generation.');
  expect(state.writes).toEqual([]);
});

test('mobile draft stays within viewport and keyboard controls remain usable', async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/specs/new');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my test set' }).click();
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const button = page.getByRole('button', { name: 'Approve test set and choose agent' });
  await button.focus(); await expect(button).toBeFocused();
  if (process.env.CAE_AUTHORING_SCREENSHOTS) await page.screenshot({ path: `${process.env.CAE_AUTHORING_SCREENSHOTS}/authoring-mobile.png`, fullPage: true });
});

test('leaving during assisted saving cannot publish or navigate away from the new page', async ({ page }) => {
  const state = await setup(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let saves = 0;
  await page.route('**/api/specs', async (route) => {
    const body = route.request().postDataJSON(); saves++;
    await pending;
    await route.fulfill({ json: { ...body, id: 'new-set', version: 1, spec: { ...body.spec, id: 'new-set', version: 1 } } }).catch(() => undefined);
  });
  await page.goto('/specs/new');
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my test set' }).click();
  await page.getByRole('button', { name: 'Approve test set and choose agent' }).click();
  await expect.poll(() => saves).toBe(1);
  await page.getByRole('navigation').getByRole('link', { name: 'Tests', exact: true }).click();
  release();
  await expect(page).toHaveURL(/\/tests$/);
  expect(state.writes.filter((path) => path.endsWith('/publish-scenarios'))).toHaveLength(0);
});

test('fresh quick-case creation clears previous assisted policy instead of silently reusing it', async ({ page }) => {
  await setup(page);
  await page.goto('/scenarios');
  await page.getByRole('button', { name: 'Make a variation', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my case' }).click();
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toBeVisible();
  await page.getByRole('button', { name: 'Use detailed editor' }).click();
  await expect(page.getByText('Retained draft policy and evidence requirements')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Add quick case', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'What would you like to test?' })).toBeEmpty();
  await expect(page.getByText(/Using context:/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Use detailed editor' }).click();
  await expect(page.getByLabel('User prompt / persona', { exact: true })).toBeEmpty();
  await expect(page.getByText('Retained draft policy and evidence requirements')).toHaveCount(0);
});

test('desktop creation and draft overview are compact and accessible', async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/specs/new');
  if (process.env.CAE_AUTHORING_SCREENSHOTS) await page.screenshot({ path: `${process.env.CAE_AUTHORING_SCREENSHOTS}/authoring-start.png`, fullPage: true });
  await page.getByRole('button', { name: 'Appointment changes', exact: true }).click();
  await page.getByRole('button', { name: 'Draft my test set' }).click();
  await expect(page.getByRole('heading', { name: 'Latest requested time' })).toBeFocused();
  if (process.env.CAE_AUTHORING_SCREENSHOTS) await page.getByRole('region', { name: 'Review proposed test' }).screenshot({ path: `${process.env.CAE_AUTHORING_SCREENSHOTS}/authoring-draft.png` });
});
