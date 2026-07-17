import { expect, test } from '@playwright/test';

test('scenarios page can create and view a scenario', async ({ page }) => {
  const prompt =
    'The user is unable to access his account. He recently changed his password, but when he tries to log in, the systems says that the password is incorrect.';
  const expected =
    'The agent gets customer information and any other relevant details, makes a report and tell the user that he will be transfered to another department.';

  await page.goto('/scenarios');

  await expect(page.getByRole('heading', { name: 'Choose what your agent must prove.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Call Center Voice AI' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Billing Address Change' })).toBeVisible();
  await page.getByRole('button', { name: 'Create scenario' }).first().click();

  await page.getByPlaceholder('Account lockout handoff').fill('Account access issue');
  await page.getByPlaceholder('Describe the user’s situation and request…').fill(prompt);
  await page.getByPlaceholder('What the agent should do or say…').fill(expected);

  await page.getByRole('button', { name: 'Create scenario' }).last().click();

  await expect(page.getByRole('heading', { name: 'Account access issue' })).toBeVisible();
  const selectedScenario = page.getByLabel('Selected scenario');
  await expect(selectedScenario.getByText('User persona / starting prompt')).toBeVisible();
  await expect(selectedScenario.getByText(prompt).first()).toBeVisible();
  await expect(selectedScenario.getByText('Expected final state')).toBeVisible();
  await expect(selectedScenario.getByText(expected)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy User persona / starting prompt' })).toBeVisible();
  await expect(page.getByText(/Scenario created in the User Scenarios evaluation suite/i)).toBeVisible();

  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Delete scenario' }).click();
  await expect(page.getByText('Scenario deleted.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Account access issue' })).not.toBeVisible();
});

test('built-in scenarios cannot be deleted', async ({ page }) => {
  await page.goto('/scenarios?suite_id=call-center-voice-ai&scenario_id=billing-address-change');

  await expect(page.getByRole('heading', { name: 'Billing Address Change' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete scenario' })).toHaveCount(0);
});

test('catalog user scenarios remain deletable when the summary omits source', async ({ page }) => {
  await page.route('**/api/benchmarks/suites**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 'user-scenarios',
          title: 'User Scenarios',
          scenarios: [{ id: 'summary-only', title: 'Summary-only user scenario' }],
        },
      ]),
    });
  });
  await page.route('**/api/scenarios/summary-only', async (route) => {
    await route.fulfill({ status: 204, body: '' });
  });

  await page.goto('/scenarios?suite_id=user-scenarios&scenario_id=summary-only');
  await expect(page.getByRole('button', { name: 'Delete scenario' })).toBeVisible();
});
