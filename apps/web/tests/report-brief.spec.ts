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

test('benchmark runner submits structured voice call evidence', async ({ page }) => {
  await page.route('**/api/benchmarks/run', async (route) => {
    const payload = route.request().postDataJSON();
    expect(payload.call).toEqual(expect.objectContaining({
      metrics: expect.objectContaining({ durationMs: 92000 }),
      media: expect.objectContaining({ recordingUrl: 'https://storage.example.test/calls/demo.wav' }),
    }));

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'voice-call-run',
        suite_id: payload.suite_id,
        scenario_id: payload.scenario_id,
        scenario_title: 'Voice call evidence',
        verdict: 'pass',
        overall_score: 88,
        evidence: ['Agent escalated to a representative.'],
        recommendations: [],
        voice_interaction_summary: {
          turn_count: 2,
          interruption_signal_count: 0,
          correction_signal_count: 0,
          handoff_signal_count: 1,
          action_trace_event_count: 0,
          duration_ms: 92000,
          media: {
            recording_url: 'https://storage.example.test/calls/demo.wav',
            mime_type: 'audio/wav',
          },
        },
        vcon_export: {
          source_format: 'call',
          appended_analysis_type: 'agentic_benchmark_eval',
          dialog: [{ party: 0, originator: 'Caller', body: 'I need a human.' }],
          analysis: [{ type: 'agentic_benchmark_eval' }],
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByText('Evidence payload').click();
  await page.getByLabel('Transcript').fill('Caller: I need a human. Agent: I escalated to a representative.');
  await page.getByLabel('Voice call evidence').fill(JSON.stringify({
    turns: [
      { speaker: 'Caller', body: 'I need a human.' },
      { speaker: 'Agent', body: 'I escalated to a representative.' },
    ],
    metrics: { durationMs: 92000 },
    media: { recordingUrl: 'https://storage.example.test/calls/demo.wav', mimeType: 'audio/wav' },
  }));
  await page.getByRole('button', { name: 'Run benchmark' }).click();

  await expect(page.getByText('Voice interaction evidence')).toBeVisible();
  await expect(page.getByLabel('Duration: 92000 ms')).toBeVisible();
  await expect(page.getByLabel('Recording: https://storage.example.test/calls/demo.wav')).toBeVisible();
  await expect(page.getByLabel('Source: call')).toBeVisible();
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

test('benchmark runner shows suite simulation summary', async ({ page }) => {
  await page.route('**/api/benchmarks/suites/*/simulate', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_run_id: 'suite-run-1',
        suite_id: 'call-center-voice-ai',
        suite_name: 'Call Center Voice AI',
        scenario_count: 2,
        pass_count: 1,
        needs_review_count: 1,
        average_score: 78,
        verdict: 'needs_review',
        vcon_export: {
          source_format: 'benchmark_suite',
          appended_analysis_type: 'agentic_benchmark_suite_eval',
          dialog: [],
          analysis: [{ type: 'agentic_benchmark_suite_eval', suite_run_id: 'suite-run-1' }],
        },
        scenario_runs: [
          {
            suite_id: 'call-center-voice-ai',
            scenario_id: 'membership-renewal-save',
            scenario_title: 'Membership renewal save',
            transcript: 'Agent: I can help review your renewal.',
            action_trace: [{ action: 'lookup_account' }],
            final_state: { retained: true },
            benchmark_report: {
              run_id: 'suite-scenario-1',
              suite_id: 'call-center-voice-ai',
              scenario_id: 'membership-renewal-save',
              scenario_title: 'Membership renewal save',
              verdict: 'pass',
              overall_score: 88,
              task_completion_score: 90,
              required_action_score: 90,
              forbidden_action_score: 100,
              final_state_score: 80,
              evidence: ['Agent: I can help review your renewal.'],
              recommendations: [],
              vcon_export: {
                source_format: 'transcript',
                appended_analysis_type: 'agentic_benchmark_eval',
                dialog: [{ party: 0, originator: 'Agent', body: 'I can help review your renewal.' }],
                analysis: [{ type: 'agentic_benchmark_eval', run_id: 'suite-scenario-1' }],
              },
            },
          },
          {
            suite_id: 'call-center-voice-ai',
            scenario_id: 'billing-escalation',
            scenario_title: 'Billing escalation',
            transcript: 'Agent: I skipped the identity check.',
            action_trace: [],
            final_state: {},
            benchmark_report: {
              run_id: 'suite-scenario-2',
              suite_id: 'call-center-voice-ai',
              scenario_id: 'billing-escalation',
              scenario_title: 'Billing escalation',
              verdict: 'needs_review',
              overall_score: 68,
              evidence: ['Agent: I skipped the identity check.'],
              missing_actions: ['confirm identity'],
              vcon_export: {
                source_format: 'transcript',
                appended_analysis_type: 'agentic_benchmark_eval',
                dialog: [{ party: 0, originator: 'Agent', body: 'I skipped the identity check.' }],
                analysis: [{ type: 'agentic_benchmark_eval', run_id: 'suite-scenario-2' }],
              },
            },
          },
        ],
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate suite' }).click();

  const suiteSummary = page.getByLabel('Suite simulation summary');
  await expect(suiteSummary).toBeVisible();
  await expect(suiteSummary.getByRole('heading', { name: 'Call Center Voice AI' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Membership renewal save pass/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Billing escalation needs_review/ })).toBeVisible();

  const suiteBrief = page.getByLabel('Suite brief');
  await expect(suiteBrief).toContainText('Suite: Call Center Voice AI');
  await expect(suiteBrief).toContainText('Needs review: 1');
  await expect(suiteBrief).toContainText('Review scenarios: Billing escalation');

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export suite vCon bundle' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('agentbench-call-center-voice-ai-suite-run-1-vcon-bundle.json');
  await expect(page.getByText('Exported 3 vCon-compatible suite records.')).toBeVisible();

  await page.getByRole('button', { name: 'Copy suite brief' }).click();
  await expect(page.getByText('Copied suite brief.')).toBeVisible();

  await page.getByRole('button', { name: 'Save suite runs' }).click();
  await expect(page.getByText('Sign up first to save suite runs and project history.')).toBeVisible();
  await page.getByRole('button', { name: 'Sign up to save' }).click();
  await page.getByRole('button', { name: 'Save suite runs' }).click();
  await expect(page.getByText('Saved 2 suite runs to call-center-demo.')).toBeVisible();

  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
});

test('benchmark runner queues retained suite runs in the background', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  await page.route('**/api/benchmarks/suite-runs?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.route('**/api/benchmarks/suites/*/simulate-async', async (route) => {
    const request = route.request();
    expect(request.method()).toBe('POST');
    expect(request.postDataJSON()).toEqual(expect.objectContaining({
      user_id: 'demo-user',
      project_id: 'qa-project',
      agent_profile: 'mock text agent',
    }));

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_run_id: 'suite-queued-1',
        suite_id: 'call-center-voice-ai',
        status: 'queued',
        scenario_count: 2,
        pass_count: 0,
        needs_review_count: 0,
        average_score: 0,
        updated_at: '2026-05-29T16:00:00+00:00',
        suite_report: { suite_name: 'Call Center Voice AI' },
        retention: { retained_until: '2026-08-27T16:00:00+00:00' },
        artifacts: { scenario_summaries: [], vcon_export: { available: false } },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Queue suite run' }).click();

  await expect(page.getByText('Queued suite run suite-queued-1 for qa-project.')).toBeVisible();
  const suiteHistory = page.getByLabel('Suite run history');
  await expect(suiteHistory.getByRole('heading', { name: /1 suite runs/ })).toBeVisible();
  await expect(suiteHistory.locator('article').getByText('queued')).toBeVisible();
  await expect(suiteHistory.getByText('Scenario artifacts appear when the suite run completes.')).toBeVisible();
});

test('benchmark runner shows retained suite run history', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  await page.route('**/api/benchmarks/suite-runs?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          suite_run_id: 'suite-history-1',
          suite_id: 'call-center-voice-ai',
          status: 'completed',
          scenario_count: 2,
          pass_count: 1,
          needs_review_count: 1,
          average_score: 82,
          reliability_metrics: {
            framework: 'eva_bench_inspired_v1',
            pass_at_1: 0.5,
            pass_at_k: 0.5,
            pass_all_k: 0.5,
            accuracy_score: 0.82,
            experience_signal_coverage: 1,
            average_turn_count: 4,
            perturbation_tags: ['accent', 'noise'],
            perturbation_coverage: [
              { tag: 'accent', scenario_count: 2, pass_count: 1, pass_rate: 0.5 },
              { tag: 'noise', scenario_count: 2, pass_count: 1, pass_rate: 0.5 },
            ],
          },
          updated_at: '2026-05-29T15:00:00+00:00',
          suite_report: {
            suite_run_id: 'suite-history-1',
            suite_id: 'call-center-voice-ai',
            suite_name: 'Call Center Voice AI',
            provider: 'simulated',
            scenario_count: 2,
            pass_count: 1,
            needs_review_count: 1,
            average_score: 82,
            verdict: 'completed',
            run_metadata: {
              agent_version: 'suite-v2',
              prompt_version: 'prompt-v5',
              model_name: 'voice-agent-sim',
              notes: 'Loaded from retained suite history.',
            },
            scenario_runs: [
              {
                scenario_id: 'membership-renewal-save',
                provider: 'simulated',
                transcript: 'Agent retained the renewing member after validating intent.',
                action_trace: [{ name: 'apply_retention_offer', result: 'success' }],
                final_state: { retained: true },
                benchmark_report: {
                  run_id: 'scenario-run-1',
                  scenario_id: 'membership-renewal-save',
                  scenario_title: 'Membership Renewal Save',
                  status: 'pass',
                  verdict: 'pass',
                  overall_score: 91,
                  transcript: 'Agent retained the renewing member after validating intent.',
                  action_trace: [{ name: 'apply_retention_offer', result: 'success' }],
                  final_state: { retained: true },
                  run_metadata: {
                    agent_version: 'suite-v2',
                    prompt_version: 'prompt-v5',
                    model_name: 'voice-agent-sim',
                    notes: 'Loaded from retained suite history.',
                  },
                },
              },
            ],
          },
          retention: { retained_until: '2026-08-27T15:00:00+00:00' },
          artifacts: {
            vcon_export: { available: true, dialog_turns: 4, analysis_count: 1, source_format: 'benchmark_suite' },
            scenario_summaries: [
              { scenario_id: 'membership-renewal-save', run_id: 'scenario-run-1', status: 'pass', overall_score: 91 },
              { scenario_id: 'billing-escalation', run_id: 'scenario-run-2', status: 'needs_review', overall_score: 73 },
            ],
          },
        },
      ]),
    });
  });

  await page.route('**/api/benchmarks/suite-runs/suite-history-1/vcon-bundle?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'suite-history-1',
        suite_run_id: 'suite-history-1',
        suite_id: 'call-center-voice-ai',
        suite_name: 'Call Center Voice AI',
        user_id: 'demo-user',
        project_id: 'qa-project',
        filename: 'agentbench-call-center-voice-ai-suite-history-1-vcon-bundle.json',
        record_count: 3,
        records: [{ source_format: 'benchmark_suite' }, { source_format: 'transcript' }, { source_format: 'transcript' }],
        exported_at: '2026-05-29T15:00:00+00:00',
      }),
    });
  });

  await page.route('**/api/benchmarks/suite-runs/export?*', async (route) => {
    expect(new URL(route.request().url()).searchParams.get('status')).toBe('completed');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'suite-history-demo-user-qa-project-call-center-voice-ai',
        user_id: 'demo-user',
        project_id: 'qa-project',
        suite_id: 'call-center-voice-ai',
        filename: 'agentbench-qa-project-call-center-voice-ai-suite-run-history.json',
        suite_run_count: 1,
        summary: {
          latest_suite_run_id: 'suite-history-1',
          latest_status: 'completed',
          latest_average_score: 82,
          status_counts: { completed: 1 },
          total_scenarios: 2,
          total_passes: 1,
          total_needs_review: 1,
        },
        vcon_export_summary: {
          available_records: 3,
          missing_records: 0,
          total_runs: 1,
          dialog_turns: 4,
          analysis_records: 1,
        },
        suite_runs: [{ suite_run_id: 'suite-history-1', status: 'completed' }],
        exported_at: '2026-05-29T15:00:00+00:00',
      }),
    });
  });

  await page.goto('/benchmarks');

  const suiteHistory = page.getByLabel('Suite run history');
  await expect(suiteHistory).toBeVisible();
  await expect(suiteHistory.getByRole('heading', { name: /1 suite runs/ })).toBeVisible();
  await expect(suiteHistory.locator('strong').filter({ hasText: 'Call Center Voice AI' })).toBeVisible();
  await expect(suiteHistory.locator('article').getByText('completed')).toBeVisible();
  await expect(suiteHistory.getByText('4 dialog turns, 1 analysis records')).toBeVisible();
  await expect(suiteHistory.getByText('EVA-style reliability: 82% accuracy, 100% experience coverage, 4 avg turns.')).toBeVisible();
  await expect(suiteHistory.getByText('Robustness tags: accent, noise.')).toBeVisible();
  await expect(suiteHistory.getByText(/billing-escalation: needs_review \/ 73/)).toBeVisible();
  await suiteHistory.getByLabel('Filter suite runs by status').selectOption('completed');

  const historyDownloadPromise = page.waitForEvent('download');
  await suiteHistory.getByRole('button', { name: 'Export suite history' }).click();
  const historyDownload = await historyDownloadPromise;
  expect(historyDownload.suggestedFilename()).toBe('agentbench-qa-project-call-center-voice-ai-suite-run-history.json');
  await expect(page.getByText('Exported 1 suite runs to agentbench-qa-project-call-center-voice-ai-suite-run-history.json. 3/1 vCon-ready runs with 4 dialog turns and 1 analysis records.')).toBeVisible();

  await suiteHistory.getByRole('button', { name: 'Load suite run' }).click();
  await expect(page.getByText('Loaded retained suite run suite-history-1.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'pass' })).toBeVisible();
  await expect(page.locator('textarea').first()).toHaveValue('Agent retained the renewing member after validating intent.');
  await expect(page.getByLabel('Suite simulation summary')).toBeVisible();
  await expect(page.getByPlaceholder('tightened escalation policy')).toHaveValue('Loaded from retained suite history.');

  const downloadPromise = page.waitForEvent('download');
  await suiteHistory.getByRole('button', { name: 'Export retained vCon bundle' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('agentbench-call-center-voice-ai-suite-history-1-vcon-bundle.json');
  await expect(page.getByText('Exported 3 retained suite vCon records')).toBeVisible();
});
