import { expect, test } from '@playwright/test';

test('scenarios page can create and view a scenario', async ({ page }) => {
  const prompt =
    'The user is unable to access his account. He recently changed his password, but when he tries to log in, the systems says that the password is incorrect.';
  const expected =
    'The agent gets customer information and any other relevant details, makes a report and tell the user that he will be transfered to another department.';

  await page.goto('/scenarios');

  await expect(page.getByRole('heading', { name: 'Create and review agent scenarios.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Runner' })).toBeVisible();
  await page.getByRole('button', { name: 'Create scenario' }).first().click();

  await expect(page.getByLabel('Type Scenario')).toBeVisible();
  await page.getByPlaceholder('Account lockout handoff').fill('Account access issue');
  await page.getByPlaceholder('Describe the user’s situation and request…').fill(prompt);
  await page.getByPlaceholder('What the agent should do or say…').fill(expected);

  await page.getByRole('button', { name: 'Create scenario' }).last().click();

  await expect(page.getByRole('heading', { name: 'Account access issue' })).toBeVisible();
  await expect(page.getByText('Simulated User Prompt')).toBeVisible();
  await expect(page.getByText(prompt)).toBeVisible();
  await expect(page.getByText('Expected Output')).toBeVisible();
  await expect(page.getByText(expected)).toBeVisible();
  await expect(page.getByText('Description')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy Simulated User Prompt' })).toBeVisible();
  await expect(page.getByText(/selectable from the User Scenarios suite/i)).toBeVisible();
});
