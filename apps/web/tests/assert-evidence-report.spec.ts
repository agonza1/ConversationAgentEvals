import { expect, test, type Locator, type Page } from '@playwright/test';
import path from 'node:path';

// Synthetic evidence and every API boundary are mocked. These checks never
// submit a model request, modify a saved run, or contact a voice target.
const suiteId = 'call-center-voice-ai';
const scenarioId = 'new-patient-triage';
const runId = 'exec-evidence-report-fixture';
const conversationId = 'conversation-evidence-report';
const bookingQuote = 'Your appointment is booked for 3 PM.';
const staleQuote = 'Your appointment is pending clinician approval.';
const repeatedQuote = 'Please send the confirmation.';
const rawMarker = 'SYNTHETIC_RAW_OUTPUT_NOT_A_REPORT';

function makeReview(id: string, options: { legacy?: boolean; quote?: string; createdAt?: string; citationWarning?: boolean } = {}) {
  const quote = options.quote ?? bookingQuote;
  const provenance = {
    engine: 'assert', assert_version: '0.3.0', judge_status: 'ok', evidence_level: 'black_box',
    dimensions: { unverified_operational_outcome: true, unsupported_operational_claim: false, policy_violation: false },
    dimension_applicability: { unverified_operational_outcome: true, unsupported_operational_claim: true, policy_violation: true },
    dimension_justifications: {
      unverified_operational_outcome: 'No booking receipt or final-state evidence was supplied.',
      unsupported_operational_claim: 'No supplied operational evidence contradicts the booking claim.',
      policy_violation: 'No policy violation was found in the supplied conversation.',
    },
    node_judgments: [
      { node_name: 'booking_claim', relevant: true, violated: false, confidence: 'high',
        reasoning: 'No contradiction to the booking claim was observed in the supplied evidence.', evidence_turns: [1] },
      { node_name: 'polite_response', relevant: true, violated: false, confidence: 'high',
        reasoning: 'The assistant remained polite.', evidence_turns: [1] },
    ],
  };
  return {
    review_id: id, status: 'pending_confirmation', provider: 'assert-ai',
    created_at: options.createdAt ?? '2026-10-14T13:00:00Z',
    model: 'chatgpt_plan/gpt-6-luna', latency_ms: 2345,
    evidence_citations: options.legacy
      ? [`{"description":"Historical booking claim","quoted_text":"${quote}","fixture":"${rawMarker}","truncated":`]
      : ['Booking claim — the appointment outcome is not independently confirmed.'],
    judge_result: {
      agrees: false, rationale: 'A conversational claim is not a verified booking outcome.',
      next_action: 'Check the booking receipt before treating the outcome as resolved.',
      raw_output: options.legacy ? `{"fixture":"${rawMarker}","verdict":` : `{"fixture":"${rawMarker}"}`,
      proposed_evaluation: { verdict: 'needs_review', summary: 'The appointment outcome remains unverified.',
        corrected_findings: [], remaining_gaps: ['No operational booking receipt was provided.'] },
      provenance: options.legacy ? provenance : {
        ...provenance, transcript_adapter_version: 'cae-assert-evidence-v4',
        evidence_messages: [
          { id: 'event:0', role: 'user', text: 'I need a same-day appointment.' },
          { id: 'event:1', role: 'assistant', text: quote },
          { id: 'event:2', role: 'user', text: repeatedQuote },
          { id: 'event:3', role: 'assistant', text: repeatedQuote },
        ],
        evidence_references: [
          { index: 1, description: 'Booking claim', parts: [{ quote, status: 'matched', message_id: 'event:1' }] },
          { index: 2, description: 'Repeated confirmation', parts: [{ quote: repeatedQuote, status: 'ambiguous',
            detail: 'The quote occurs in more than one canonical transcript message.' }] },
          { index: 3, description: 'Unavailable receipt', parts: [{ quote: 'Appointment receipt number 123.',
            status: 'unresolved', detail: 'No canonical transcript message contains this quote.' }] },
        ],
        evidence_coverage: { transcript: true, tool_trace: false, final_state: false },
        ...(options.citationWarning ? { evidence_details_status: 'complete',
          evidence_details_message: 'ASSERT citation warnings: missing_referenced_citation:3' } : {}),
      },
    },
  };
}
type Review = ReturnType<typeof makeReview>;

function automaticReport() {
  return { run_id: 'benchmark-evidence-report', suite_id: suiteId, scenario_id: scenarioId,
    scenario_title: 'New Patient Triage', verdict: 'pass', overall_score: 100,
    evidence: ['The supplied transcript satisfied the automatic criteria.'], recommendations: [] };
}

function savedRun(reviews: Review[]) {
  return {
    execution_run_id: runId, status: 'needs_review', mode: 'text_callable', suite_id: suiteId,
    scenario_ids: [scenarioId], user_id: 'evidence-report-user', project_id: 'evidence-report-project',
    agent_id: 'evidence-report-target', agent_name: 'Synthetic appointment agent',
    tester_id: 'scenario_simulator', executor_id: 'local_async_runner',
    progress: { phase: 'completed', completed_conversations: 1, total_conversations: 1, percent: 100 },
    conversations: [{ conversation_id: conversationId, execution_run_id: runId, suite_id: suiteId,
      scenario_id: scenarioId, scenario_title: 'New Patient Triage', mode: 'text_callable',
      status: 'completed', iteration: 1, verdict: 'needs_review', score: 100,
      turns: [{ turn_index: 1, speaker: 'user', text: 'I need a same-day appointment.' },
        { turn_index: 2, speaker: 'agent', text: bookingQuote }],
      transcript: `User: I need a same-day appointment.\nAgent: ${bookingQuote}`,
      action_trace: [], final_state: {}, evaluation_findings: { missing_actions: [] },
      metrics_summary: { verdict: 'needs_review', score: 100, turn_count: 2, interruption_count: 0 },
      judge_reviews: reviews,
    }],
    created_at: '2026-10-14T12:00:00Z', updated_at: '2026-10-14T13:00:00Z',
  };
}

async function mockApi(page: Page, options: { reviews?: Review[]; judgeError?: boolean; citationWarning?: boolean } = {}) {
  const writes: string[] = [];
  const freshnessRequests: string[] = [];
  const current = makeReview('review-current', { citationWarning: options.citationWarning });
  const reviewResponse = { ...current, status: 'ready', engine: 'assert', required_plan: 'starter', credits: 10,
    message: 'Synthetic ASSERT assessment completed.', execution_run_id: runId, conversation_id: conversationId,
    spend_control: { estimated_credits: 10, daily_credit_limit: 100, daily_credit_spent: 10 } };
  await page.addInitScript(() => {
    localStorage.setItem('conversation-evals-demo-user', 'evidence-report-user');
    localStorage.setItem('conversation-evals-demo-project', 'evidence-report-project');
  });
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (!['GET', 'HEAD'].includes(route.request().method())) writes.push(pathname);
    if (pathname.startsWith('/api/assert/') && pathname.endsWith('/judge')) {
      return options.judgeError
        ? route.fulfill({ status: 502, json: { detail: 'Synthetic ASSERT review could not complete.' } })
        : route.fulfill({ json: reviewResponse });
    }
    if (pathname.includes('/reviews/') && pathname.endsWith('/status')) {
      const reviewId = pathname.split('/reviews/')[1].split('/')[0];
      freshnessRequests.push(reviewId);
      return route.fulfill({ json: { execution_run_id: runId, conversation_id: conversationId, review_id: reviewId,
        status: reviewId === 'review-stale' ? 'stale' : reviewId === 'review-legacy' ? 'cannot_verify' : 'current',
        reason_code: 'synthetic_fixture', message: reviewId === 'review-stale'
          ? 'The retained review does not match the current evidence.' : reviewId === 'review-legacy'
            ? 'This historical review lacks a verifiable input fingerprint.' : 'Saved judging inputs match.' } });
    }
    if (pathname === `/api/execution/runs/${runId}`) return route.fulfill({ json: savedRun(options.reviews ?? []) });
    if (pathname === '/api/product/config') return route.fulfill({ json: {
      pricing: [], usage_rules: [], auth: { enabled: false, providers: [] }, llm_judge_status: 'enabled',
      assert_judge: { ready: true, enabled: true, engine: 'assert', model: 'chatgpt_plan/gpt-6-luna', message: 'Synthetic ASSERT ready.' },
    } });
    if (pathname === '/api/assert/readiness') return route.fulfill({ json: {
      ready: true, enabled: true, engine: 'assert', model: 'chatgpt_plan/gpt-6-luna', message: 'Synthetic ASSERT ready.',
    } });
    if (pathname === '/api/benchmarks/suites') return route.fulfill({ json: [{ id: suiteId,
      title: 'Call Center Voice AI', scenarios: [{ id: scenarioId, title: 'New Patient Triage',
        user_goal: 'Request an appointment without treating an unverified claim as a booking.' }] }] });
    if (pathname === '/api/benchmarks/run') return route.fulfill({ json: automaticReport() });
    if (pathname.endsWith('/contract-manifest')) return route.fulfill({ json: {} });
    if (pathname === '/api/agents') return route.fulfill({ json: { agents: [] } });
    return route.fulfill({ json: [] });
  });
  return { writes, freshnessRequests, judgeCalls: () => writes.filter((url) => url.endsWith('/judge')).length,
    applyCalls: () => writes.filter((url) => url.endsWith('/apply')).length };
}

async function evaluate(page: Page) {
  await page.goto(`/eval?suite_id=${suiteId}&scenario_id=${scenarioId}`);
  await expect(page.getByLabel('Evaluation scenario', { exact: true })).toBeEnabled();
  await page.getByLabel('Evidence transcript', { exact: true })
    .fill(`User: I need a same-day appointment.\nAgent: ${bookingQuote}`);
  await page.getByRole('button', { name: 'Evaluate evidence', exact: true }).click();
  await expect(page.getByLabel('Report brief')).toContainText('Score: 100');
}

const evidenceReport = (page: Page) => page.getByLabel('ASSERT evidence report', { exact: true });
const details = (report: Locator, title: string) => report.locator('summary')
  .filter({ hasText: title }).locator('..');

async function checkCoverage(report: Locator) {
  const coverage = report.getByLabel('Evidence coverage', { exact: true });
  await expect(coverage).toContainText(/Transcript\s*Available/);
  await expect(coverage).toContainText(/Tool trace\s*Not captured/);
  await expect(coverage).toContainText(/Final state\s*Not captured/);
  await expect(report).toContainText('Available evidence is not proof that a business action succeeded.');
}

async function jumpWithKeyboard(page: Page, report: Locator, quote = bookingQuote) {
  const transcript = details(report, 'Transcript reviewed');
  await expect(transcript).toHaveJSProperty('open', false);
  const jump = report.getByRole('button', { name: 'View transcript turn', exact: true });
  await expect(jump).toHaveCount(1); // Ambiguous and unresolved quotes never get an invented destination.
  await jump.focus();
  await page.keyboard.press('Enter');
  await expect(transcript).toHaveJSProperty('open', true);
  await expect.poll(() => page.evaluate(() => document.activeElement?.textContent)).toContain(quote);
  expect(await page.evaluate(() => document.activeElement?.tagName)).toBe('LI');
}

test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'wait' }); });

test('Eval explains an unverified outcome separately from a 100-point automatic pass', async ({ page }) => {
  const api = await mockApi(page, { citationWarning: true });
  await evaluate(page);
  expect(api.judgeCalls()).toBe(0);
  await page.getByRole('button', { name: 'Request LLM judge', exact: true }).click();
  const report = evidenceReport(page);
  await expect(report.getByRole('heading', { name: 'Outcome unverified', exact: true })).toBeVisible();
  await expect(report.getByText('Automatic', { exact: true }).locator('..')).toContainText(/pass/i);
  await expect(report.getByText('ASSERT proposal', { exact: true }).locator('..')).toContainText(/needs review/i);
  await expect(report).toContainText('Not applied');
  await expect(report).toContainText(/Criteria score\s*—\s*not a resolution percentage\./);
  await checkCoverage(report);
  await expect(report.getByLabel('Citation warnings', { exact: true })).toBeVisible();
  await expect(report.getByLabel('Citation warnings', { exact: true })).toContainText('missing_referenced_citation:3');
  await expect(report.getByText('Matched', { exact: true })).toBeVisible();
  await expect(report.getByText('Ambiguous', { exact: true })).toBeVisible();
  await expect(report.getByText('Unresolved', { exact: true })).toBeVisible();
  await expect(details(report, 'Technical details')).toHaveJSProperty('open', false);
  await expect(details(report, 'All checks')).toHaveJSProperty('open', false);
  for (const model of await report.getByText('chatgpt_plan/gpt-6-luna', { exact: true }).all()) {
    await expect(model).toBeHidden();
  }
  await expect(report.getByText(rawMarker, { exact: false })).toBeHidden();
  await jumpWithKeyboard(page, report);
  await expect(page.getByLabel('Report brief')).toContainText('Verdict: pass');
  expect(api.judgeCalls()).toBe(1);
  expect(api.applyCalls()).toBe(0);
  expect(api.writes).toHaveLength(2);
  if (process.env.CAE_ASSERT_EVIDENCE_SCREENSHOTS) {
    await report.screenshot({ path: path.join(process.env.CAE_ASSERT_EVIDENCE_SCREENSHOTS, 'assert-evidence-desktop.png') });
  }
});

test('Eval judge failure keeps the automatic report and never fabricates an ASSERT assessment', async ({ page }) => {
  const api = await mockApi(page, { judgeError: true });
  await evaluate(page);
  expect(api.judgeCalls()).toBe(0);
  await page.getByRole('button', { name: 'Request LLM judge', exact: true }).click();
  await expect(page.getByLabel('LLM judge result')).toContainText('Synthetic ASSERT review could not complete.');
  await expect(evidenceReport(page)).toHaveCount(0);
  await expect(page.getByLabel('Report brief')).toContainText('Score: 100');
  await expect(page.getByLabel('Report brief')).toContainText('Verdict: pass');
  expect(api.judgeCalls()).toBe(1);
  expect(api.applyCalls()).toBe(0);
  expect(api.writes).toHaveLength(2);
});

test('saved Run report distinguishes a 100-point needs-review outcome and supports keyboard evidence jumps', async ({ page }) => {
  const api = await mockApi(page, { reviews: [makeReview('review-current')] });
  await page.goto(`/runs/${runId}`);
  const report = evidenceReport(page);
  await expect(report.getByRole('heading', { name: 'Outcome unverified', exact: true })).toBeVisible();
  await expect(page.getByLabel('Resolution evidence details')).toContainText('100/100');
  await expect(report.getByText('Automatic', { exact: true }).locator('..')).toContainText(/needs review/i);
  await expect(report).toContainText('Not applied');
  await checkCoverage(report);
  await jumpWithKeyboard(page, report);
  await expect(page.getByLabel('Review freshness')).toContainText('Current');
  await expect(page.getByRole('button', { name: 'Apply proposed evaluation', exact: true })).toBeEnabled();
  expect(api.writes).toHaveLength(0);
});

test('an inconsistent saved pass proposal never hides recorded policy flags behind a clear headline', async ({ page }) => {
  const review = makeReview('review-current');
  review.judge_result.proposed_evaluation.verdict = 'pass';
  review.judge_result.proposed_evaluation.summary = 'Synthetic historical pass proposal retained unchanged.';
  review.judge_result.provenance.dimensions.unverified_operational_outcome = false;
  review.judge_result.provenance.dimensions.policy_violation = true;
  review.judge_result.provenance.dimension_justifications.policy_violation = 'A policy issue was explicitly recorded in this saved assessment.';
  const api = await mockApi(page, { reviews: [review] });
  await page.goto(`/runs/${runId}`);
  const report = evidenceReport(page);
  await expect(report.getByRole('heading', { name: 'Issues flagged', exact: true })).toBeVisible();
  await expect(report.getByText('ASSERT proposal', { exact: true }).locator('..')).toContainText('Pass');
  await expect(report).not.toHaveAttribute('data-outcome', 'clear');
  await expect(report.getByRole('heading', { name: 'No semantic issues flagged', exact: true })).toHaveCount(0);
  await expect(report.getByLabel('What needs attention')).toContainText('A policy issue was explicitly recorded');
  expect(api.judgeCalls()).toBe(0);
  expect(api.applyCalls()).toBe(0);
  expect(api.writes).toHaveLength(0);
});

test('saved selection replaces the entire evidence mapping and preserves freshness/application gates', async ({ page }) => {
  const api = await mockApi(page, { reviews: [
    makeReview('review-current'),
    makeReview('review-stale', { quote: staleQuote, createdAt: '2026-10-13T13:00:00Z' }),
    makeReview('review-legacy', { legacy: true, createdAt: '2026-10-12T13:00:00Z' }),
  ] });
  await page.goto(`/runs/${runId}`);
  const report = evidenceReport(page);
  const apply = page.getByRole('button', { name: 'Apply proposed evaluation', exact: true });
  await expect(page.getByLabel('Saved ASSERT review')).toHaveValue('review-current');
  await expect(apply).toBeEnabled();
  await jumpWithKeyboard(page, report);
  await page.getByLabel('Saved ASSERT review').selectOption('review-stale');
  await expect(page.getByLabel('Review freshness')).toContainText('Stale');
  await expect(report).toContainText(staleQuote);
  await expect(report).not.toContainText(bookingQuote);
  await expect(apply).toBeDisabled();
  await page.getByLabel('Saved ASSERT review').selectOption('review-legacy');
  await expect(page.getByLabel('Review freshness')).toContainText('Cannot verify');
  await expect(report.getByLabel('Historical evidence mapping', { exact: true })).toBeVisible();
  await expect(report.getByRole('button', { name: 'View transcript turn', exact: true })).toHaveCount(0);
  await expect(apply).toBeDisabled();
  await page.getByLabel('Saved ASSERT review').selectOption('review-current');
  await expect(page.getByLabel('Review freshness')).toContainText('Current');
  await expect(report).toContainText(bookingQuote);
  await expect(report).not.toContainText(staleQuote);
  await expect(apply).toBeEnabled();
  expect(api.freshnessRequests).toEqual(expect.arrayContaining(['review-current', 'review-stale', 'review-legacy']));
  expect(api.writes).toHaveLength(0);
});

test('historical truncated JSON stays out of the default report and never gains fabricated quote links', async ({ page }) => {
  const api = await mockApi(page, { reviews: [makeReview('review-legacy', { legacy: true })] });
  await page.goto(`/runs/${runId}`);
  const report = evidenceReport(page);
  await expect(report.getByLabel('Historical evidence mapping', { exact: true })).toBeVisible();
  await expect(report).toContainText('Historical booking claim');
  await expect(report).toContainText(bookingQuote);
  await expect(report.getByText(rawMarker, { exact: false })).toBeHidden();
  await expect(details(report, 'Technical details')).toHaveJSProperty('open', false);
  await expect(report.getByRole('button', { name: 'View transcript turn', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Apply proposed evaluation', exact: true })).toBeDisabled();
  expect(api.judgeCalls()).toBe(0);
  expect(api.applyCalls()).toBe(0);
  expect(api.writes).toHaveLength(0);
});

for (const surface of ['Eval', 'saved Run'] as const) {
  test(`${surface} evidence report remains usable at 390px with expanded details`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const api = await mockApi(page, { reviews: surface === 'saved Run' ? [makeReview('review-current')] : [] });
    if (surface === 'Eval') {
      await evaluate(page);
      await page.getByRole('button', { name: 'Request LLM judge', exact: true }).click();
    } else await page.goto(`/runs/${runId}`);
    const report = evidenceReport(page);
    await expect(report.getByRole('heading', { name: 'Outcome unverified', exact: true })).toBeVisible();
    await jumpWithKeyboard(page, report);
    await report.locator('summary').filter({ hasText: 'Technical details' }).click();
    await expect(details(report, 'Technical details')).toHaveJSProperty('open', true);
    await expect(report.getByText('chatgpt_plan/gpt-6-luna', { exact: true }).first()).toBeVisible();
    await report.locator('summary').filter({ hasText: 'All checks' }).click();
    await expect(details(report, 'All checks')).toHaveJSProperty('open', true);
    const box = await report.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    expect(await report.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(api.judgeCalls()).toBe(surface === 'Eval' ? 1 : 0);
    expect(api.applyCalls()).toBe(0);
    if (process.env.CAE_ASSERT_EVIDENCE_SCREENSHOTS && surface === 'saved Run') {
      await report.screenshot({ path: path.join(process.env.CAE_ASSERT_EVIDENCE_SCREENSHOTS, 'assert-evidence-mobile.png') });
    }
  });
}
