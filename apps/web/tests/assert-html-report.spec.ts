import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import runFixture from '../../api/tests/fixtures/assert-html-report-run.json';

const artifactDir = path.resolve(process.cwd(), 'artifacts/assert-html-report-export');
const report = execFileSync(path.resolve('apps/api/.venv/bin/python'), ['-c', `
import json
from pathlib import Path
from app.services.assert_html_report import render_assert_html_report,validate_saved_review
from app.services.benchmark_service import get_scenario_contract
run=json.loads(Path('apps/api/tests/fixtures/assert-html-report-run.json').read_text())
conv=run['conversations'][0]; review=conv['judge_reviews'][0]
p=validate_saved_review(run,conv,review,get_scenario_contract(run['suite_id'],conv['scenario_id']))
print(render_assert_html_report(run,conv,review,p))
`], { encoding: 'utf8', env: { ...process.env, PYTHONPATH: path.resolve('apps/api') } });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('conversation-evals-demo-user', 'demo-user'));
});

async function showRun(page: import('@playwright/test').Page, run: unknown) {
  await page.route('**/api/execution/runs/exec-assert-html-fixture**', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(run),
  }));
  await page.goto('/runs/exec-assert-html-fixture');
}

test('saved review export downloads and renders a standalone synthetic report offline', async ({ page, context }) => {
  const run = structuredClone(runFixture);
  const review = run.conversations[0].judge_reviews[0];
  const older = structuredClone(review);
  older.review_id = 'judge-review-older';
  older.status = 'superseded';
  run.conversations[0].judge_reviews.unshift(older);
  let judgeCalls = 0;
  await page.route('**/api/**/judge', (route) => { judgeCalls += 1; return route.abort(); });
  let exportedUrl = '';
  await page.route('**/reviews/*/report.html?**', (route) => {
    exportedUrl = route.request().url();
    // Deliberately omit Content-Disposition to cover cross-origin CORS fallback.
    return route.fulfill({ status: 200, contentType: 'text/html', body: report });
  });
  await showRun(page, run);
  await expect(page.getByRole('button', { name: 'Export ASSERT HTML report' })).toBeEnabled();
  await expect(page.getByLabel('Saved ASSERT review')).toHaveValue(review.review_id);
  mkdirSync(artifactDir, { recursive: true });
  await page.screenshot({ path: path.join(artifactDir, 'export-action.png'), fullPage: true });
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export ASSERT HTML report' }).click();
  const download = await downloadPromise;
  const filename = 'assert-exec-assert-html-fixture-conversation-refund-fixture-judge-review-fixture.html';
  expect(download.suggestedFilename()).toBe(filename);
  expect(exportedUrl).toContain(`/reviews/${review.review_id}/report.html?user_id=demo-user`);
  expect(judgeCalls).toBe(0);
  const file = path.join(artifactDir, filename);
  await download.saveAs(file);
  const offline = await context.newPage();
  const remoteRequests: string[] = [];
  offline.on('request', (request) => { if (/^https?:/.test(request.url())) remoteRequests.push(request.url()); });
  await offline.goto(`file://${file}`);
  await expect(offline.getByRole('heading', { name: 'ASSERT semantic report' })).toBeVisible();
  await expect(offline.getByText('Synthetic fixture · no live judging or target call')).toBeVisible();
  await expect(offline.getByText('Rendered by CAE from a saved ASSERT assessment.', { exact: false })).toBeVisible();
  await expect(offline.getByText('pending_confirmation', { exact: true })).toBeVisible();
  await offline.getByText('Tool evidence 1: open_review_case', { exact: true }).click();
  await expect(offline.getByText('"refund_issued": false', { exact: false }).first()).toBeVisible();
  await offline.getByText('Behavior refund-review', { exact: true }).click();
  await offline.getByText('Recorded scale', { exact: true }).click();
  await offline.screenshot({ path: path.join(artifactDir, 'offline-report-desktop.png'), fullPage: true });
  await offline.setViewportSize({ width: 390, height: 844 });
  await offline.screenshot({ path: path.join(artifactDir, 'offline-report-mobile.png'), fullPage: true });
  expect(remoteRequests).toEqual([]);
  await page.getByLabel('Saved ASSERT review').selectOption(older.review_id);
  const olderDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export ASSERT HTML report' }).click();
  await olderDownload;
  expect(exportedUrl).toContain(`/reviews/${older.review_id}/report.html`);
});

test('export unavailable without a saved review and never starts judging', async ({ page }) => {
  const run = structuredClone(runFixture);
  run.conversations[0].judge_reviews = [];
  let judgeCalls = 0;
  await page.route('**/api/**/judge', (route) => { judgeCalls += 1; return route.abort(); });
  await showRun(page, run);
  await expect(page.getByRole('button', { name: 'Export ASSERT HTML report' })).toBeDisabled();
  await expect(page.getByText('Export is available after an ASSERT review is saved.')).toBeVisible();
  expect(judgeCalls).toBe(0);
});

test('export waits for completion and exposes a stale review error with retry', async ({ page }) => {
  const run = structuredClone(runFixture);
  run.status = 'running';
  await showRun(page, run);
  await expect(page.getByRole('button', { name: 'Export ASSERT HTML report' })).toBeDisabled();
  await expect(page.getByText('Export is available after the run completes.')).toBeVisible();
  run.status = 'needs_review';
  await page.reload();
  let respond: (() => void) | undefined;
  await page.route('**/reviews/*/report.html?**', async (route) => {
    await new Promise<void>((resolve) => { respond = resolve; });
    await route.fulfill({ status: 409, contentType: 'application/json',
      body: JSON.stringify({ detail: 'The saved ASSERT review is stale: conversation evidence changed. Run a new review.' }) });
  });
  let downloads = 0;
  page.on('download', () => { downloads += 1; });
  await page.getByRole('button', { name: 'Export ASSERT HTML report' }).click();
  await expect(page.getByRole('button', { name: 'Exporting ASSERT report…' })).toBeDisabled();
  await expect.poll(() => Boolean(respond)).toBeTruthy();
  respond?.();
  await expect(page.locator('.assert-report-export [role="alert"]')).toHaveText(/saved ASSERT review is stale/);
  await expect(page.getByRole('button', { name: 'Export ASSERT HTML report' })).toBeEnabled();
  expect(downloads).toBe(0);
});


test('real saved API result downloads through the browser without an export mock', async ({ page, request, context }) => {
  // Test-only filesystem fixture seed: standard persisted-run loader, no debug route,
  // provider call or client-supplied report to the export endpoint.
  const run = structuredClone(runFixture);
  run.execution_run_id = `exec-assert-api-${randomUUID()}`;
  run.conversations[0].execution_run_id = run.execution_run_id;
  const seededDir = path.resolve('artifacts/execution-runs', run.execution_run_id);
  mkdirSync(seededDir, { recursive: true });
  writeFileSync(path.join(seededDir, 'run.json'), JSON.stringify(run));
  const apiBase = process.env.PLAYWRIGHT_API_BASE_URL
    || `http://127.0.0.1:${process.env.PLAYWRIGHT_API_PORT || process.env.API_PORT || '8425'}`;
  const conversation = run.conversations[0];
  const reviewId = conversation.judge_reviews[0].review_id;
  const endpoint = `${apiBase}/api/assert/runs/${run.execution_run_id}`
    + `/conversations/${conversation.conversation_id}/reviews/${reviewId}/report.html`;
  try {
    const forbidden = await request.get(`${endpoint}?user_id=another-user`);
    expect(forbidden.status()).toBe(404);
    const response = await request.get(`${endpoint}?user_id=demo-user`);
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('text/html');
    expect(await response.text()).toContain(run.execution_run_id);
    let judgeCalls = 0;
    page.on('request', (req) => { if (req.method() === 'POST' && req.url().endsWith('/judge')) judgeCalls += 1; });
    await page.goto(`/runs/${run.execution_run_id}`);
    await expect(page.getByLabel('Saved ASSERT review')).toHaveValue(reviewId);
    const actualReportResponse = page.waitForResponse((res) => new URL(res.url()).pathname === new URL(endpoint).pathname);
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export ASSERT HTML report' }).click();
    expect((await actualReportResponse).status()).toBe(200);
    const downloaded = await downloadPromise;
    expect(downloaded.suggestedFilename()).toContain(run.execution_run_id);
    const file = path.join(artifactDir, 'real-api-synthetic-report.html');
    mkdirSync(artifactDir, { recursive: true });
    await downloaded.saveAs(file);
    const offline = await context.newPage();
    const remoteRequests: string[] = [];
    offline.on('request', (req) => { if (/^https?:/.test(req.url())) remoteRequests.push(req.url()); });
    await offline.goto(`file://${file}`);
    await expect(offline.getByText(run.execution_run_id, { exact: true })).toBeVisible();
    await offline.getByText('Tool evidence 1: open_review_case', { exact: true }).click();
    await expect(offline.getByText('"refund_issued": false', { exact: false }).first()).toBeVisible();
    await offline.screenshot({ path: path.join(artifactDir, 'real-api-offline-report.png'), fullPage: true });
    expect(remoteRequests).toEqual([]);
    expect(judgeCalls).toBe(0);
  } finally {
    rmSync(seededDir, { recursive: true, force: true });
  }
});
