import { expect, test } from '@playwright/test';

test('free-to-paid eval journey works end to end', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Run deterministic checks now. Connect OpenAI for the local LLM judge.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Try a sample scenario check' })).toBeVisible();
  await expect(page.getByText('Auth', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Sign up to save' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Create demo project' })).toHaveCount(0);
  await expect(page.getByLabel('Choose: Done')).toBeVisible();
  await expect(page.getByLabel('Simulate: Ready')).toBeVisible();
  await expect(page.getByLabel('Save: Next')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reload starter data' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Simulate sample' })).toBeVisible();
  await expect(page.getByLabel('Pricing and upgrade gates')).toHaveCount(0);
  await expect(page.getByLabel('Demo plan')).toHaveValue('free');

  await page.getByRole('button', { name: 'Simulate sample' }).click();
  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
  await expect(page.getByText('Benchmark report').last()).toBeVisible();
  const actionPlan = page.getByLabel('Operator action plan');
  await expect(actionPlan).toBeVisible();
  await expect(actionPlan.getByRole('heading', { name: 'Keep moving through uncovered scenarios' })).toBeVisible();
  await expect(actionPlan.getByText('3 suite scenarios still need fresh coverage before release review.')).toBeVisible();
  await expect(actionPlan.getByText('Baseline run for this project.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Contract evidence' })).toBeVisible();
  await expect(page.getByText('Suite manifest')).toBeVisible();
  await expect(page.getByText('Scenario contract', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Simulate: Done')).toBeVisible();

  const reportDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download report JSON' }).click();
  const reportDownloadFile = await reportDownload;
  expect(reportDownloadFile.suggestedFilename()).toMatch(/agentbench-.*-report\.json/);
  await expect(page.getByText('Exported current benchmark report JSON.')).toBeVisible();

  await expect(page.getByRole('button', { name: 'Connect OpenAI' })).toBeVisible();
  await page.getByRole('button', { name: 'Request LLM judge' }).click();
  await expect(page.getByRole('button', { name: 'Connect OpenAI' })).toBeVisible();

  await page.getByRole('button', { name: 'Save run' }).click();
  await expect(page.getByText(/Saved run/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /1 saved for Billing Address Change/ })).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation').getByText('Baseline run for this project.')).toBeVisible();
  await expect(page.getByText(/vCon ready: \d+ dialog turns, \d+ analysis records/)).toBeVisible();
  await expect(page.getByText(/Audit export ready: transcript, action trace, final state/)).toBeVisible();
  await expect(page.getByText('Project audit trail')).toBeVisible();
  await expect(page.getByText('run.saved')).toBeVisible();
  await expect(page.getByLabel('Save: Done')).toBeVisible();
  await expect(page.getByText('Selected scenario: baseline')).toBeVisible();
  await expect(page.getByText('1 focused runs')).toBeVisible();

  const auditDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export audit artifacts' }).click();
  const auditDownloadFile = await auditDownload;
  expect(auditDownloadFile.suggestedFilename()).toMatch(/agentbench-.*-audit-artifacts\.json/);
  await expect(page.getByText(/Exported audit artifacts to agentbench-.*-audit-artifacts\.json/)).toBeVisible();

  await page.getByRole('button', { name: 'Load run' }).click();
  await expect(page.getByText(/Loaded saved run/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();

  await page.getByRole('button', { name: 'Retry run' }).click();
  await expect(page.getByText(/Retried saved run/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
});

test('failure baseline surfaces actionable benchmark report issues', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();

  await page.getByLabel('Failure baseline').check();
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByRole('heading', { name: 'needs_review' })).toBeVisible();
  await expect(page.getByText('Benchmark report').last()).toBeVisible();
  const actionPlan = page.getByLabel('Operator action plan');
  await expect(actionPlan).toBeVisible();
  await expect(actionPlan.getByRole('heading', { name: 'Needs operator review' })).toBeVisible();
  await expect(actionPlan.getByText('task completion')).toBeVisible();
  await expect(actionPlan.getByText('Add explicit tool/action execution for: explain next invoice impact')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Failure categories' })).toBeVisible();
  await expect(page.getByText('required_action_execution', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Missing actions' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Forbidden actions observed' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Suggested fixes' })).toBeVisible();

  await page.getByRole('button', { name: 'Save run' }).click();
  await expect(page.getByText('Failure mix')).toBeVisible();
  await expect(page.getByLabel('Saved runs and e2e validation').getByText('required_action_execution')).toBeVisible();
});
