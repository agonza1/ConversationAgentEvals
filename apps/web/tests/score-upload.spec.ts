import { expect, test } from '@playwright/test';
import path from 'node:path';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';

test('eval page uploads vCon and loads sample call-center evidence', async ({ page }) => {
  await page.goto('/eval');
  await expect(page.getByText('Loading benchmark suites...')).toHaveCount(0);

  const dir = mkdtempSync(path.join(tmpdir(), 'score-upload-'));
  const vconPath = path.join(dir, 'sample.vcon.json');
  writeFileSync(
    vconPath,
    JSON.stringify({
      vcon: '0.0.1',
      parties: [{ name: 'Caller' }, { name: 'Agent' }],
      dialog: [
        { party: 0, body: 'I need to change my billing address.' },
        { party: 1, body: 'I can help with that.' },
      ],
    }),
  );

  await page.getByLabel('Upload vCon or transcript file').setInputFiles(vconPath);
  await expect(page.getByText('Loaded vCon from sample.vcon.json.')).toBeVisible();
  await expect(page.locator('textarea').first()).toHaveValue(/Caller: I need to change my billing address/);

  await page.getByRole('button', { name: 'Load sample evidence' }).click();
  const sampleOptions = page.getByLabel('Sample evidence options');
  await expect(sampleOptions).toBeVisible();
  await expect(sampleOptions.locator('select').nth(0)).toHaveValue('call-center-voice-ai');
  await expect(sampleOptions.locator('select').nth(1)).toHaveValue('billing-address-change');
  await sampleOptions.getByRole('button', { name: 'Load selected sample evidence' }).click();
  await expect(page.getByText(/Loaded sample evidence: Billing Address Change.*synthetic/)).toBeVisible();
  await page.getByRole('button', { name: 'Evaluate evidence' }).click();
  await expect(page.getByText('Benchmark report', { exact: true })).toBeVisible();
  await expect(page.getByText('Task completion', { exact: true })).toBeVisible();
  await expect(page.getByLabel('OpenAI judge provider')).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toBeVisible();
  await expect(page.getByText('Benchmark suite')).toHaveCount(0);
  await expect(page.getByText('Scenario', { exact: true })).toHaveCount(0);
});
