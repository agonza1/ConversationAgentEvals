import { expect, test } from '@playwright/test';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import fixture from '../../api/tests/fixtures/assert-review-native-run.json';

const artifacts = path.resolve('artifacts/assert-review-frontend');
const runPath = (runId: string) => path.resolve('artifacts/execution-runs', runId);
const runForTest = () => {
  const run = structuredClone(fixture);
  run.execution_run_id = `exec-review-${randomUUID()}`;
  run.conversations[0].execution_run_id = run.execution_run_id;
  return run;
};
const seed = (run: ReturnType<typeof runForTest>) => { mkdirSync(runPath(run.execution_run_id), { recursive: true }); writeFileSync(path.join(runPath(run.execution_run_id), 'run.json'), JSON.stringify(run)); };
test.beforeEach(async ({ page }) => {
  mkdirSync(artifacts, { recursive: true });
  await page.addInitScript(() => localStorage.setItem('conversation-evals-demo-user', 'demo-user'));
});

test('real persisted review renders native nodes/typed ordinals/history after reload, export matches selection', async ({ page }) => {
  const run = runForTest(); seed(run);
  let judgeCalls = 0; let applyCalls = 0;
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().endsWith('/judge')) judgeCalls++;
    if (request.method() === 'POST' && request.url().endsWith('/apply')) applyCalls++;
  });
  try {
    await page.goto(`/runs/${run.execution_run_id}`);
    const panel = page.getByLabel('Saved ASSERT assessment');
    await expect(page.getByLabel('Saved ASSERT review')).toHaveValue('judge-review-native-newest');
    await expect(panel.getByLabel('Review freshness')).toContainText('Current');
    await expect(panel).toContainText('String two recorded');
    await expect(panel.getByText('number: 2 — Numeric two must not match', { exact: true })).not.toBeVisible();
    await expect(panel).toContainText('Not applicable');
    const nodes = panel.getByLabel('Behavior breakdown').locator('summary');
    await expect(nodes.first()).toHaveText('flagged_behavior · Flagged');
    await nodes.first().focus(); await page.keyboard.press('Enter');
    await expect(panel.getByText('Confidence: high', { exact: true })).toBeVisible();
    await expect(panel.getByText('Judge turn 1, Judge turn 3 — unresolved reference', { exact: true })).toBeVisible();
    await expect(panel.getByText('Completion is unsupported <script>alert(1)</script>.')).toBeVisible();
    await expect(panel.locator('a[href*="turn"]')).toHaveCount(0);
    await expect(panel).not.toContainText('/Users/private');
    await expect(panel).not.toContainText('NEVER-EXPOSE');
    await panel.getByText('Rubric for string ordinal', { exact: true }).click();
    await panel.getByLabel('Behavior breakdown').screenshot({ path: path.join(artifacts, 'behavior-breakdown.png') });
    await panel.getByLabel('ASSERT evaluation details').screenshot({ path: path.join(artifacts, 'ordinal-rubrics.png') });
    await page.screenshot({ path: path.join(artifacts, 'native-behaviors-desktop.png'), fullPage: true });
    await page.reload();
    await expect(page.getByLabel('Saved ASSERT review')).toHaveValue('judge-review-native-newest');
    await expect(panel.getByLabel('Review freshness')).toContainText('Current');
    await page.getByLabel('Saved ASSERT review').selectOption('judge-review-native-older');
    await expect(panel).toContainText('Superseded');
    await expect(panel.getByLabel('Review freshness')).toContainText('Current');
    await panel.getByText('Semantic rationale', { exact: true }).click();
    await expect(panel).toContainText('Older recorded assessment.');
    await expect(panel.getByRole('button', { name: 'Apply proposed evaluation' })).toHaveCount(0);
    const reportResponse = page.waitForResponse((response) => response.url().includes('/reviews/judge-review-native-older/report.html'));
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export ASSERT HTML report' }).click();
    expect((await reportResponse).status()).toBe(200); await (await download).saveAs(path.join(artifacts, 'selected-historical.html'));
    await page.getByLabel('Saved ASSERT review').selectOption('judge-review-native-legacy');
    await expect(panel.getByLabel('Review freshness')).toContainText('Cannot verify');
    await expect(panel).toContainText('Recorded time unavailable');
    await page.screenshot({ path: path.join(artifacts, 'legacy-unverifiable.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByLabel('Saved ASSERT review').selectOption('judge-review-native-newest');
    await expect(panel.getByLabel('Review freshness')).toContainText('Current');
    await panel.getByText('flagged_behavior · Flagged', { exact: true }).click();
    await page.screenshot({ path: path.join(artifacts, 'native-behaviors-mobile.png'), fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect(judgeCalls).toBe(0); expect(applyCalls).toBe(0);
    await panel.getByRole('button', { name: 'Apply proposed evaluation' }).click();
    const confirmation = page.getByRole('dialog', { name: 'Apply this LLM adjudication?' });
    await expect(confirmation).toBeVisible(); expect(applyCalls).toBe(0);
    const applied = page.waitForResponse((response) => response.url().includes('/judge-reviews/judge-review-native-newest/apply'));
    await confirmation.getByRole('button', { name: 'Apply adjudication', exact: true }).click();
    expect((await applied).status()).toBe(200);
    await expect(panel).toContainText('Applied');
    expect(judgeCalls).toBe(0); expect(applyCalls).toBe(1);
  } finally { rmSync(runPath(run.execution_run_id), { recursive: true, force: true }); }
});

test('freshness errors and late previous review responses never enable apply or replace current selection', async ({ page }) => {
  const run = runForTest();
  await page.route(`**/api/execution/runs/${run.execution_run_id}**`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(run) }));
  let releaseOld: (() => void) | undefined;
  await page.route('**/reviews/*/status?**', async (route) => {
    const id = route.request().url().includes('/judge-review-native-newest/') ? 'judge-review-native-newest' : 'judge-review-native-older';
    if (id === 'judge-review-native-newest') await new Promise<void>((resolve) => { releaseOld = resolve; });
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ execution_run_id: run.execution_run_id,
      conversation_id: run.conversations[0].conversation_id, review_id: id, status: id.endsWith('older') ? 'stale' : 'current',
      reason_code: 'test', message: id.endsWith('older') ? 'Recorded target inputs changed.' : 'Matching inputs.' }) });
  });
  await page.goto(`/runs/${run.execution_run_id}`);
  const panel = page.getByLabel('Saved ASSERT assessment');
  await expect(panel.getByLabel('Review freshness')).toContainText('Checking freshness');
  await expect(panel.getByRole('button', { name: 'Apply proposed evaluation' })).toBeDisabled();
  await expect.poll(() => Boolean(releaseOld)).toBeTruthy();
  await page.getByLabel('Saved ASSERT review').selectOption('judge-review-native-older');
  await expect(panel.getByLabel('Review freshness')).toContainText('Stale'); releaseOld?.();
  await expect(panel.getByLabel('Review freshness')).toContainText('Stale');
  await page.screenshot({ path: path.join(artifacts, 'stale-selected-review.png'), fullPage: true });
  await page.unroute('**/reviews/*/status?**');
  await page.route('**/reviews/*/status?**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Unavailable' }) }));
  await page.getByLabel('Saved ASSERT review').selectOption('judge-review-native-newest');
  await expect(panel.getByLabel('Review freshness')).toContainText('Cannot verify');
  await expect(panel.getByRole('button', { name: 'Apply proposed evaluation' })).toBeDisabled();
});


test('late freshness from a previous conversation cannot replace selected conversation state', async ({ page }) => {
  const run = runForTest();
  const second = structuredClone(run.conversations[0]); second.conversation_id = 'conversation-switched'; second.scenario_title = 'Second fixture conversation';
  run.conversations.push(second);
  await page.route(`**/api/execution/runs/${run.execution_run_id}**`, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(run) }));
  let releaseFirst: (() => void) | undefined;
  await page.route('**/reviews/*/status?**', async (route) => {
    const switched = route.request().url().includes('/conversations/conversation-switched/');
    if (!switched) await new Promise<void>((resolve) => { releaseFirst = resolve; });
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ execution_run_id: run.execution_run_id,
      conversation_id: switched ? second.conversation_id : run.conversations[0].conversation_id,
      review_id: 'judge-review-native-newest', status: switched ? 'stale' : 'current', reason_code: 'test', message: 'Synthetic fixture freshness.' }) });
  });
  await page.goto(`/runs/${run.execution_run_id}`);
  const panel = page.getByLabel('Saved ASSERT assessment');
  await expect(panel.getByLabel('Review freshness')).toContainText('Checking freshness');
  await expect.poll(() => Boolean(releaseFirst)).toBeTruthy();
  await page.getByLabel('Conversation', { exact: true }).selectOption(second.conversation_id);
  await expect(panel.getByLabel('Review freshness')).toContainText('Stale'); releaseFirst?.();
  await expect(panel.getByLabel('Review freshness')).toContainText('Stale');
  await expect(panel.getByRole('button', { name: 'Apply proposed evaluation' })).toBeDisabled();
});


test('undated persisted history defaults and exports to the newest appended review', async ({ page }) => {
  const run = runForTest();
  const reviews = run.conversations[0].judge_reviews;
  const older = reviews.find((review) => review.review_id === 'judge-review-native-older');
  const newest = reviews.find((review) => review.review_id === 'judge-review-native-newest');
  if (!older || !newest) throw new Error('Missing saved review fixtures');
  older.created_at = 'invalid-date'; newest.created_at = 'invalid-date';
  run.conversations[0].judge_reviews = [older, newest]; seed(run);
  try {
    await page.goto(`/runs/${run.execution_run_id}`);
    await expect(page.getByLabel('Saved ASSERT review')).toHaveValue(newest.review_id);
    const panel = page.getByLabel('Saved ASSERT assessment');
    await expect(panel.getByLabel('Review freshness')).toContainText('Current');
    await expect(panel).toContainText('Recorded time unavailable');
    await page.reload();
    await expect(page.getByLabel('Saved ASSERT review')).toHaveValue(newest.review_id);
    const report = page.waitForResponse((response) => response.url().includes(`/reviews/${newest.review_id}/report.html`));
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export ASSERT HTML report' }).click();
    expect((await report).status()).toBe(200);
    await (await download).saveAs(path.join(artifacts, 'undated-newest.html'));
  } finally { rmSync(runPath(run.execution_run_id), { recursive: true, force: true }); }
});
