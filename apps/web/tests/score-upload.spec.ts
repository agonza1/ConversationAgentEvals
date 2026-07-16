import { expect, test } from '@playwright/test';
import path from 'node:path';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';

test('score page uploads vCon and simulates call-center evidence', async ({ page }) => {
  await page.goto('/score');
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

  await page.getByRole('button', { name: 'Simulate evidence upload' }).click();
  const simulateOptions = page.getByLabel('Call Center Voice AI simulate options');
  await expect(simulateOptions).toBeVisible();
  await simulateOptions.getByRole('button', { name: 'Billing Address Change' }).click();
  await expect(page.getByText(/Loaded simulated Call Center Voice AI evidence: Billing Address Change/)).toBeVisible();
  await expect(page.locator('form').first().locator('select').nth(0)).toHaveValue('call-center-voice-ai');
  await expect(page.locator('form').first().locator('select').nth(1)).toHaveValue('billing-address-change');
});
