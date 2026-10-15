import { expect, test } from '@playwright/test';

test('quick case separates tester instructions from the exact spoken opening', async ({ page }) => {
  const directions = 'Synthetic caller asks about headphones. Do not place an order.';
  const opener = 'Hi, what wireless headphones do you have? I only need information today.';
  let scenario: Record<string, unknown> | null = null;
  await page.route('**/api/benchmarks/suites**', async (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify([
      { id: 'user-scenarios', name: 'User Scenarios', scenarios: scenario ? [scenario] : [] },
    ]),
  }));
  await page.route('**/api/scenarios', async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    expect(payload.simulated_user_prompt).toBe(directions);
    expect(payload.spoken_opener).toBe(opener);
    scenario = { ...payload, id: 'steven-info', suite_id: 'user-scenarios' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(scenario) });
  });
  await page.goto('/scenarios?create=1');
  await page.getByLabel('Title (optional)').fill('Steven information only');
  await page.getByLabel('User prompt / persona').fill(directions);
  await page.getByLabel('Spoken opening line').fill(opener);
  await page.getByLabel('Expected outcome').fill('Answer from the catalog; no transaction.');
  await page.getByLabel('Required behaviors (one per line)').fill('Use catalog evidence');
  await page.getByRole('button', { name: 'Create scenario', exact: true }).click();
  const detail = page.getByLabel('Selected scenario');
  await expect(detail.getByRole('heading', { name: 'Steven information only' })).toBeVisible();
  await expect(detail.getByText(opener, { exact: true })).toBeVisible();
  await expect(detail.getByRole('heading', { name: 'Spoken opening line' })).toBeVisible();
  expect(scenario).not.toBeNull();
});

test('legacy quick case clearly reports that an explicit opening is absent', async ({ page }) => {
  await page.route('**/api/benchmarks/suites**', async (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify([
      { id: 'user-scenarios', name: 'User Scenarios', scenarios: [
        { id: 'legacy', title: 'Legacy case', simulated_user_prompt: 'Synthetic caller asks about headphones.' },
      ] },
    ]),
  }));
  await page.goto('/scenarios?suite_id=user-scenarios&scenario_id=legacy');
  await expect(page.getByLabel('Selected scenario').getByText(
    'Not explicitly set. A direct caller request or neutral greeting is used; persona instructions are not spoken.',
  )).toBeVisible();
});
