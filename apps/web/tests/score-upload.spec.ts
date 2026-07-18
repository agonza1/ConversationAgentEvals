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
  await expect(page.getByText(/Loaded vCon from sample\.vcon\.json/)).toBeVisible();
  await expect(page.getByText(/Check “Include structured evidence” if you also want the vCon artifact evaluated/)).toBeVisible();
  await expect(page.locator('textarea').first()).toHaveValue(/Caller: I need to change my billing address/);
  await expect(page.getByLabel('Include structured evidence in Evaluate')).not.toBeChecked();

  await page.getByRole('button', { name: 'Load sample evidence' }).click();
  const sampleOptions = page.getByLabel('Sample evidence options');
  await expect(sampleOptions).toBeVisible();
  await expect(sampleOptions.getByRole('button', { name: 'Load sample transcript only' })).toBeVisible();
  await sampleOptions.getByRole('button', { name: 'Load sample transcript only' }).click();
  await expect(page.getByText(/Loaded sample transcript: Billing Address Change/)).toBeVisible();
  await page.getByRole('button', { name: 'Evaluate evidence' }).click();
  await expect(page.getByText('Benchmark report', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Transcript scoring note')).toContainText('Task completion and Final state are not shown');
  await expect(page.getByLabel('Task completion score')).toHaveCount(0);
  await expect(page.getByLabel('Final state score')).toHaveCount(0);
  await expect(page.getByLabel('LLM judge controls')).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation')).toBeVisible();
  await expect(page.getByLabel('Evaluation contract')).toBeVisible();
  await expect(page.getByText('Benchmark suite')).toHaveCount(0);

  await page.getByRole('button', { name: 'Load sample evidence' }).click();
  await page.getByRole('button', { name: 'Load full sample (measure Task/Final)' }).click();
  await expect(page.getByText(/Loaded full sample evidence: Billing Address Change/)).toBeVisible();
  await page.getByRole('button', { name: 'Evaluate evidence' }).click();
  await expect(page.getByLabel('Task completion score')).toBeVisible();
  await expect(page.getByLabel('Final state score')).toBeVisible();
  await expect(page.getByLabel('Task completion score')).not.toContainText('n/a');
  await expect(page.getByLabel('Final state score')).not.toContainText('n/a');

  await page.getByLabel('Evidence transcript').fill('hello this transcript has none of the required call-center actions');
  await page.getByRole('button', { name: 'Evaluate evidence' }).click();
  await expect(page.getByText('Benchmark report', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: /needs_review/i })).toBeVisible();
  const requiredActionsTile = page.getByLabel('Required actions score');
  await expect(requiredActionsTile).toBeVisible();
  await expect(requiredActionsTile).toContainText('0');

  await page.getByLabel('Evidence transcript').fill('');
  await expect(page.getByRole('button', { name: 'Evaluate evidence' })).toBeDisabled();
});
