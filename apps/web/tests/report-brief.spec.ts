import { expect, test } from '@playwright/test';

test('benchmark report includes a share-ready brief', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();

  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Report brief' })).toBeVisible();

  const brief = page.getByLabel('Report brief');
  await expect(brief).toContainText('Scenario:');
  await expect(brief).toContainText('Verdict:');
  await expect(brief).toContainText('Score:');
  await expect(brief).toContainText('Missing actions:');
  await expect(brief).toContainText('Suggested fixes:');

  await page.getByRole('button', { name: 'Copy brief' }).click();
  await expect(page.getByText('Copied report brief.')).toBeVisible();
});

test('benchmark report brief includes deterministic failure fields', async ({ page }) => {
  await page.route('**/api/benchmarks/simulate', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_id: 'call-center',
        scenario_id: 'deterministic-failure',
        scenario_title: 'Deterministic failure',
        transcript: 'Agent: I diagnosed your condition.',
        action_trace: [{ action: 'diagnose condition' }],
        final_state: {},
        benchmark_report: {
          run_id: 'deterministic-run',
          scenario_id: 'deterministic-failure',
          scenario_title: 'Deterministic failure',
          verdict: 'needs_review',
          overall_score: 55,
          scenario_contract_sha256: 'abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
          missing_actions: ['confirm identity'],
          forbidden_action_hits: [
            { action: 'diagnose condition', evidence: 'Agent: I diagnosed your condition.' },
          ],
          recommendations: ['Remove forbidden behavior: diagnose condition'],
          evidence: ['Agent: I diagnosed your condition.'],
          voice_interaction_summary: {
            turn_count: 3,
            interruption_signal_count: 1,
            correction_signal_count: 2,
            handoff_signal_count: 0,
            action_trace_event_count: 1,
          },
          vcon_export: {
            source_format: 'transcript',
            appended_analysis_type: 'agentic_benchmark_eval',
            dialog: [
              { party: 0, originator: 'Agent', body: 'I diagnosed your condition.' },
            ],
            analysis: [{ type: 'agentic_benchmark_eval' }],
          },
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  const brief = page.getByLabel('Report brief');
  await expect(brief).toContainText('Forbidden actions observed: diagnose condition');
  await expect(brief).toContainText('Scenario contract: abcdef123456');
  await expect(brief).toContainText('Suggested fixes: Remove forbidden behavior: diagnose condition');
  await expect(page.getByText('Voice interaction evidence')).toBeVisible();
  await expect(page.getByText('Voice turn signals captured')).toBeVisible();
  await expect(page.getByLabel('Corrections: 2')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'vCon export' })).toBeVisible();
  await expect(page.getByLabel('Dialog turns: 1')).toBeVisible();
  await expect(page.getByLabel('Contract: abcdef123456')).toBeVisible();
  await expect(page.getByLabel('Analysis: agentic_benchmark_eval')).toBeVisible();
});

test('benchmark report shows copy failure when fallback copy is rejected', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: () => false,
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByRole('heading', { name: 'Report brief' })).toBeVisible();

  await page.getByRole('button', { name: 'Copy brief' }).click();
  await expect(page.getByText('Could not copy report brief.')).toBeVisible();
});

test('benchmark report falls back when async clipboard copy is rejected', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: () => Promise.reject(new Error('blocked')),
      },
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: () => true,
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByRole('heading', { name: 'Report brief' })).toBeVisible();

  await page.getByRole('button', { name: 'Copy brief' }).click();
  await expect(page.getByText('Copied report brief.')).toBeVisible();
});