import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

// Every API request is synthetic. Delayed judge routes exercise pending states
// without calling a model, creating an execution, or retaining live evidence.
const suiteId = 'call-center-voice-ai';
const scenarioId = 'interruption-correction-handling';
const runId = 'exec-progress-fixture';
const conversationId = 'progress-conversation';
const report = {
  run_id: 'benchmark-progress-fixture', suite_id: suiteId, scenario_id: scenarioId,
  scenario_title: 'Interruption and Correction Handling', verdict: 'needs_review',
  overall_score: 50, evidence: ['The corrected address needs confirmation.'], recommendations: [],
};
const run = {
  execution_run_id: runId, status: 'needs_review', mode: 'text_callable',
  suite_id: suiteId, scenario_ids: [scenarioId], user_id: 'progress-user',
  project_id: 'progress-project', agent_id: 'progress-target', agent_name: 'Progress fixture target',
  tester_id: 'scenario_simulator', executor_id: 'local_async_runner',
  progress: { phase: 'completed', completed_conversations: 1, total_conversations: 1, percent: 100 },
  conversations: [{
    conversation_id: conversationId, execution_run_id: runId, suite_id: suiteId,
    scenario_id: scenarioId, scenario_title: report.scenario_title, mode: 'text_callable',
    status: 'completed', iteration: 1, verdict: 'needs_review', score: 50,
    turns: [{ turn_index: 1, speaker: 'user', text: 'Actually, the apartment is 5C.' },
      { turn_index: 2, speaker: 'agent', text: 'I heard 5C. Please confirm the address.' }],
    transcript: 'User: Actually, the apartment is 5C.\nAgent: I heard 5C. Please confirm the address.',
    action_trace: [], final_state: { complete: false, termination_reason: 'max_exchanges' },
    evaluation_findings: { missing_actions: ['confirm corrected address'] },
    metrics_summary: { verdict: 'needs_review', score: 50, turn_count: 2, interruption_count: 0 },
  }],
  created_at: '2026-10-12T20:00:00Z', updated_at: '2026-10-12T20:01:00Z',
};
const successfulReview = {
  status: 'ready', engine: 'assert', required_plan: 'starter', credits: 10,
  provider: 'assert-ai', model: 'chatgpt_plan/gpt-6-luna',
  message: 'Synthetic ASSERT review completed.', evidence_citations: [],
  judge_result: { agrees: true, rationale: 'The corrected address needs confirmation.',
    next_action: 'Confirm the corrected address.', proposed_evaluation: {
      verdict: 'needs_review', summary: 'Confirmation is still missing.', corrected_findings: [],
      remaining_gaps: ['No confirmation of the complete address.'],
    } },
};

type Surface = 'Eval' | 'Run analysis';
type Outcome = 'success' | 'error';
const pendingReleases = new WeakMap<Page, () => void>();

async function pendingReview(page: Page, surface: Surface, outcome: Outcome) {
  let release: () => void = () => {};
  const pending = new Promise<void>((resolve) => { release = resolve; });
  pendingReleases.set(page, release);
  let judgeCalls = 0;
  await page.addInitScript(() => {
    localStorage.setItem('conversation-evals-demo-user', 'progress-user');
    localStorage.setItem('conversation-evals-demo-project', 'progress-project');
  });
  await page.route('**/api/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname.startsWith('/api/assert/') && pathname.endsWith('/judge')) {
      judgeCalls += 1;
      await pending;
      return outcome === 'success'
        ? route.fulfill({ json: successfulReview })
        : route.fulfill({ status: 502, json: { detail: 'Synthetic ASSERT review failed.' } });
    }
    if (pathname === '/api/product/config') return route.fulfill({ json: {
      pricing: [], usage_rules: [], auth: { enabled: false, providers: [] },
      llm_judge_status: 'enabled', assert_judge: { ready: true, enabled: true,
        engine: 'assert', model: 'chatgpt_plan/gpt-6-luna', message: 'Synthetic ASSERT ready.' },
    } });
    if (pathname === '/api/assert/readiness') return route.fulfill({ json: {
      ready: true, enabled: true, engine: 'assert', model: 'chatgpt_plan/gpt-6-luna',
      message: 'Synthetic ASSERT ready.',
    } });
    if (pathname === '/api/benchmarks/suites') return route.fulfill({ json: [{
      id: suiteId, title: 'Call Center Voice AI', scenarios: [{
        id: scenarioId, title: report.scenario_title,
        user_goal: 'Correct the address and ask for confirmation.', required_actions: ['confirm corrected address'],
      }],
    }] });
    if (pathname.endsWith('/contract-manifest')) return route.fulfill({ json: {} });
    if (pathname === '/api/benchmarks/run') return route.fulfill({ json: report });
    if (pathname === `/api/execution/runs/${runId}`) return route.fulfill({ json: run });
    if (pathname === '/api/agents') return route.fulfill({ json: { agents: [] } });
    return route.fulfill({ json: [] });
  });
  if (surface === 'Eval') {
    await page.goto(`/eval?suite_id=${suiteId}&scenario_id=${scenarioId}`);
    await expect(page.getByLabel('Evaluation scenario', { exact: true })).toBeEnabled();
    await page.getByLabel('Evidence transcript', { exact: true }).fill(run.conversations[0].transcript);
    await page.getByRole('button', { name: 'Evaluate evidence', exact: true }).click();
  } else {
    await page.goto(`/runs/${runId}`);
    await expect(page.getByRole('heading', { name: 'Review the deterministic verdict' })).toBeVisible();
  }
  const progress = page.getByRole('region', { name: 'ASSERT review in progress' });
  await expect(progress).toHaveCount(0);
  await page.getByRole('button', {
    name: surface === 'Eval' ? 'Request LLM judge' : 'Review with LLM judge', exact: true,
  }).click();
  await expect.poll(() => judgeCalls).toBe(1);
  return { progress, release, judgeCalls: () => judgeCalls };
}

test.afterEach(async ({ page }) => {
  // Drain even when setup itself throws before returning its release handle.
  pendingReleases.get(page)?.();
  pendingReleases.delete(page);
  await page.unrouteAll({ behavior: 'wait' });
});

for (const surface of ['Eval', 'Run analysis'] as const) {
  for (const outcome of ['success', 'error'] as const) {
    test(`${surface}: ASSERT cartoon only appears during a pending ${outcome} request`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      const { progress, release, judgeCalls } = await pendingReview(page, surface, outcome);
      try {
        await expect(progress).toBeVisible();
        await expect(progress).toContainText('Checking the conversation evidence. Your automatic result stays unchanged.');
        await expect(progress.locator('svg')).toHaveAttribute('aria-hidden', 'true');
        await expect(progress).not.toContainText(/\d+\s*%/);
        await expect(progress.getByRole('progressbar')).toHaveCount(0);
        expect(await progress.evaluate((element) => element.getAnimations({ subtree: true })
          .filter((animation) => animation.playState === 'running').length)).toBeGreaterThan(0);
        const scanner = progress.locator('[aria-hidden="true"]').last().locator('span');
        const initialTransform = await scanner.evaluate(element => getComputedStyle(element).transform);
        await expect.poll(() => scanner.evaluate(element => getComputedStyle(element).transform)).not.toBe(initialTransform);
        await expect(progress.getByRole('timer')).toContainText(/0:0[1-9] elapsed/);
        await expect(page.getByRole('button', { name: surface === 'Eval'
          ? 'Requesting ASSERT review…' : 'Reviewing evidence…', exact: true })).toBeDisabled();
        if (surface === 'Eval' && outcome === 'success' && process.env.CAE_ASSERT_PROGRESS_SCREENSHOTS) {
          await progress.screenshot({ path: path.join(process.env.CAE_ASSERT_PROGRESS_SCREENSHOTS, 'assert-review-desktop.png') });
          for (let frame = 0; frame < 16; frame += 1) {
            await progress.screenshot({ path: path.join(process.env.CAE_ASSERT_PROGRESS_SCREENSHOTS, `assert-review-frame-${frame}.png`), animations: 'allow' });
            await page.waitForTimeout(80);
          }
        }
        expect(judgeCalls()).toBe(1);
      } finally { release(); }
      await expect(progress).toHaveCount(0);
      if (outcome === 'success') {
        await expect(page.getByLabel('LLM judge result')).toContainText('Synthetic ASSERT review completed.');
      } else if (surface === 'Eval') {
        await expect(page.getByLabel('LLM judge result')).toContainText('Synthetic ASSERT review failed.');
      } else {
        await expect(page.getByRole('alert').filter({ hasText: 'Synthetic ASSERT review failed.' })).toBeVisible();
      }
      await expect(page.getByLabel(surface === 'Eval' ? 'Report brief' : 'Resolution evidence details'))
        .toContainText(surface === 'Eval' ? 'Score: 50' : '50/100');
      expect(judgeCalls()).toBe(1);
    });
  }

  test(`${surface}: reduced-motion review keeps live timing without animation and fits a narrow viewport`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const { progress, release } = await pendingReview(page, surface, 'success');
    try {
      await expect(progress).toBeVisible();
      expect(await progress.evaluate((element) => element.getAnimations({ subtree: true })
        .filter((animation) => animation.playState === 'running').length)).toBe(0);
      await expect(progress.getByRole('timer')).toContainText(/0:0[1-9] elapsed/);
      await expect(progress.getByRole('status')).not.toContainText('elapsed');
      const box = await progress.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
      expect(await progress.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
      if (surface === 'Eval' && process.env.CAE_ASSERT_PROGRESS_SCREENSHOTS) {
        await progress.screenshot({ path: path.join(process.env.CAE_ASSERT_PROGRESS_SCREENSHOTS, 'assert-review-mobile.png') });
      }
    } finally { release(); }
    await expect(progress).toHaveCount(0);
  });
}
