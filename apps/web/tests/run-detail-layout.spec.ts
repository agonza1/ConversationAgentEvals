import { expect, test } from '@playwright/test';
import path from 'node:path';

const conversation = (id: string, title: string, caller: string) => ({
  conversation_id: id, scenario_id: id, scenario_title: title, status: 'completed', mode: 'text_callable',
  turns: [{ turn_index: 1, speaker: 'caller', text: caller }, { turn_index: 2, speaker: 'agent', text: 'Your appointment is confirmed for Tuesday.' }],
  verdict: 'needs_review', score: 75, final_state: {}, action_trace: [],
  evaluation_findings: { missing_evidence: ['Confirmation was not independently verified.'] },
});
const fixture = {
  execution_run_id: 'exec-layout', status: 'completed', mode: 'text_callable', agent_name: 'Appointment assistant',
  user_id: 'demo-user', project_id: 'call-center-demo', suite_id: 'appointments', scenario_ids: ['booking', 'reschedule'],
  tester_id: 'scenario_simulator', executor_id: 'local_async_runner',
  provenance: { honesty_label: 'Built-in sample · synthetic test evidence', target_kind: 'mock_agent', evidence_source: 'sample' },
  progress: { phase: 'completed', completed_conversations: 2, total_conversations: 2, percent: 100 },
  created_at: '2026-10-14T12:00:00Z', updated_at: '2026-10-14T12:01:00Z',
  conversations: [conversation('booking', 'Book an appointment', 'Please book a Tuesday appointment.'), conversation('reschedule', 'Reschedule an appointment', 'Can I move my visit to Tuesday?')],
};

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`run workspace selects conversations and metrics without overflow at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route('**/api/**', route => route.fulfill({ json: route.request().url().includes('/runs/exec-layout') ? fixture : {} }));
    await page.goto('/runs/exec-layout?api_base=http://127.0.0.1:8425');
    await expect(page.getByRole('heading', { name: 'Appointment assistant' })).toBeVisible();
    const setup = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: 'Run setup' }) });
    await expect(setup).not.toHaveAttribute('open', '');
    await expect(page.getByText('Built-in sample · synthetic test evidence')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Previous conversation' })).toBeDisabled();
    await expect(page.getByLabel('Transcript', { exact: true })).toContainText('Please book a Tuesday appointment.');
    await page.getByRole('button', { name: 'Next conversation' }).click();
    await expect(page.getByLabel('Conversation', { exact: true })).toHaveValue('reschedule');
    await expect(page.getByLabel('Transcript', { exact: true })).toContainText('Can I move my visit to Tuesday?');
    await expect(page.getByLabel('Transcript', { exact: true })).not.toContainText('Please book a Tuesday appointment.');
    await expect(page.getByRole('button', { name: 'Next conversation' })).toBeDisabled();
    await page.getByRole('button', { name: /^Latency/ }).click();
    await expect(page.getByRole('button', { name: /^Latency/ })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('button', { name: /Verified Resolution Rate/ })).toHaveAttribute('aria-pressed', 'false');
    await page.getByRole('button', { name: /Verified Resolution Rate/ }).click();
    await expect(page.getByLabel('Resolution verification status')).toContainText('Needs Review');
    await page.getByRole('button', { name: 'Previous conversation' }).click();
    await expect(page.getByLabel('Conversation', { exact: true })).toHaveValue('booking');
    await expect(page.getByRole('link', { name: /All results/ })).toHaveAttribute('href', /api_base=/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: path.resolve('../../artifacts/run-detail', `${viewport.width}.png`), fullPage: true });
  });
}

test('run error offers retry and empty active runs explain what happens next', async ({ page }) => {
  let fail = true;
  await page.route('**/api/**', route => route.fulfill(fail ? { status: 503, json: { detail: 'Temporarily unavailable' } } : { json: { ...fixture, status: 'running', conversations: [], progress: { completed_conversations: 0, total_conversations: 2 } } }));
  await page.goto('/runs/exec-layout');
  await expect(page.getByRole('alert').filter({ hasText: 'Could not load this run' })).toContainText('Temporarily unavailable');
  fail = false;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByLabel('Run progress')).toContainText('0 of 2');
  await expect(page.getByText('Conversations will appear here as the run progresses.')).toBeVisible();
  await expect(page.getByLabel('Conversation', { exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Next conversation' })).toBeDisabled();
});
