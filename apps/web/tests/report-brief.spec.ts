import { expect, test } from '@playwright/test';

test('benchmark report includes a share-ready brief', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();

  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Report brief' })).toBeVisible();

  const brief = page.getByLabel('Report brief');
  await expect(brief).toContainText('Scenario:');
  await expect(brief).toContainText('Verdict:');
  await expect(brief).toContainText('Score:');
  await expect(brief).toContainText('Missing actions:');
  await expect(brief).toContainText('Suggested fixes:');

  await page.getByRole('button', { name: 'Copy brief' }).click();
  await expect(page.getByText('Copied report brief.')).toBeVisible();
});
