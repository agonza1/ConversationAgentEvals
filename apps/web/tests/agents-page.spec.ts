import { expect, test } from '@playwright/test';

test('agents page shows persona cards and try-it-out deep links', async ({ page }) => {
  await page.goto('/agents');

  await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
  await expect(page.locator('.agents-page-header').getByRole('button', { name: 'Add a new Agent' })).toBeVisible();

  const mockCard = page.getByRole('article').filter({ hasText: 'Mock text agent' });
  await expect(mockCard.locator('.agents-badge-channel')).toHaveText('Text chat');
  await expect(mockCard.getByText('Built-in')).toBeVisible();
  await expect(mockCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=mock-text-agent',
  );

  const voiceCard = page.getByRole('article').filter({ hasText: 'ACC voice fixture agent' });
  await expect(voiceCard.locator('.agents-badge-channel')).toHaveText('Inbound Voice');
  await expect(voiceCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=acc-voice-fixture-agent',
  );
});

test('agents try-it-out preselects agent on runs page', async ({ page }) => {
  await page.goto('/runs?launch=demo&agent_id=acc-voice-fixture-agent');
  await expect(page.getByText('Loading benchmark suites...')).toHaveCount(0);
  await expect(page.getByLabel('Execution agent')).toHaveValue('acc-voice-fixture-agent');
});

test('add agent modal creates a registry entry', async ({ page }) => {
  await page.goto('/agents');
  await page.locator('.agents-page-header').getByRole('button', { name: 'Add a new Agent' }).click();
  await expect(page.getByRole('heading', { name: 'Add a new Agent' })).toBeVisible();

  const uniqueName = `Playwright agent ${Date.now()}`;
  await page.getByPlaceholder('Support bot v2').fill(uniqueName);
  await page.getByRole('button', { name: 'Create agent' }).click();

  await expect(page.getByRole('article').filter({ hasText: uniqueName })).toBeVisible();
});
