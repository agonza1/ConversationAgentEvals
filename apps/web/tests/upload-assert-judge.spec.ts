import { expect, test } from '@playwright/test';

// A readiness refresh can still be in flight after the last assertion.
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'wait' }); });

for (const source of ['transcript', 'vcon'] as const) {
  test(`uploaded ${source}: Evaluate stays deterministic and LLM Judge uses ASSERT`, async ({ page, request }) => {
    const userId = `upload-assert-${source}-${Date.now()}`;
    await page.addInitScript((id) => {
      localStorage.setItem('conversation-evals-demo-user', id);
      localStorage.setItem('conversation-evals-demo-project', 'upload-project');
    }, userId);
    // Readiness and the paid output are mocked; file intake, Evaluate and
    // benchmark persistence use the real API. Backend integration tests cover
    // the same paid boundary through the actual ASSERT adapter and score parser.
    await page.route('**/api/product/config', async route => {
      const response = await route.fetch();
      const config = await response.json();
      await route.fulfill({json: {...config, llm_judge_status: 'enabled', assert_judge: {
        engine: 'assert', ready: true, model: 'openai/gpt-4.1-mini', message: 'ASSERT test configuration ready.'
      }}});
    });
    let legacyCalls = 0;
    let agentExecutions = 0;
    page.on('request', req => {
      if (req.method() === 'POST' && req.url().includes('/api/product/judge')) legacyCalls++;
      if (req.method() === 'POST' && /\/api\/execution\/runs$/.test(req.url())) agentExecutions++;
    });
    const judgments: Array<{url: string; body: Record<string, unknown>}> = [];
    await page.route('**/api/assert/benchmarks/*/judge', async route => {
      judgments.push({url: route.request().url(), body: route.request().postDataJSON()});
      await route.fulfill({json: {
        status: 'ready', provider: 'assert-ai', engine: 'assert', required_plan: 'starter', credits: 10,
        model: 'openai/gpt-4.1-mini', message: 'Synthetic ASSERT review of retained evidence.', evidence_citations: [],
        review_id: `review-${judgments.length}`, execution_run_id: 'import-browser-fixture', conversation_id: 'import-browser-conversation',
        judge_result: {agrees: true, rationale: 'Recorded evidence needs semantic review.',
          next_action: 'Inspect the evidence.', proposed_evaluation: {verdict: 'needs_review',
            summary: 'Synthetic example', corrected_findings: [], remaining_gaps: []}}
      }});
    });
    await page.goto('/eval');
    await expect(page.getByRole('heading', {name: 'Evaluate conversation evidence.'})).toBeVisible();
    await expect(page.getByLabel('Evaluation scenario', { exact: true })).toBeEnabled();
    let evidence: Buffer;
    if (source === 'vcon') {
      const sample = await request.get('/api/benchmarks/evidence/sample-vcon?suite_id=call-center-voice-ai&scenario_id=refund-policy-boundary');
      expect(sample.ok()).toBeTruthy();
      evidence = Buffer.from(await sample.text());
    } else {
      evidence = Buffer.from('User: Please review this charge.\nAgent: I can explain the refund policy; no refund is promised.');
    }
    await page.locator('input[type="file"]').first().setInputFiles({
      name: source === 'vcon' ? 'synthetic.vcon' : 'synthetic.txt',
      mimeType: source === 'vcon' ? 'application/json' : 'text/plain', buffer: evidence,
    });
    const evaluated = page.waitForResponse(response => response.url().endsWith('/api/benchmarks/run') && response.request().method() === 'POST');
    await page.getByRole('button', {name: 'Evaluate evidence', exact: true}).click();
    const response = await evaluated;
    expect(response.ok()).toBeTruthy();
    const report = await response.json();
    expect(report.verdict).toBe('needs_review');
    expect(report.evaluation_contract_snapshot.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(judgments).toHaveLength(0); // Evaluate never implicitly incurs LLM cost.
    await page.getByRole('button', {name: 'Request LLM judge', exact: true}).click();
    await expect(page.getByLabel('LLM judge result')).toContainText('Synthetic ASSERT review');
    expect(judgments[0].url).toContain(`/api/assert/benchmarks/${report.run_id}/judge`);
    expect(judgments[0].body).toEqual({user_id: userId});
    await expect(page.getByRole('link', {name: 'View saved ASSERT review, apply or export'})).toHaveAttribute('href', /\/runs\/import-browser-fixture/);
    await page.getByRole('button', {name: 'Run a new ASSERT review', exact: true}).click();
    await expect.poll(() => judgments.length).toBe(2);
    expect(judgments[1].body.request_id).toEqual(expect.any(String));
    expect(judgments[1].body).not.toHaveProperty('report');
    expect(judgments[1].body).not.toHaveProperty('transcript');
    expect(legacyCalls).toBe(0);
    expect(agentExecutions).toBe(0);
  });
}
