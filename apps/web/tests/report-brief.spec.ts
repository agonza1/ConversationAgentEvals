import { expect, test } from '@playwright/test';

test('benchmark report includes a share-ready brief', async ({ page }) => {
  await page.goto('/benchmarks');

  await expect(page.getByRole('heading', { name: 'Run an agentic scenario test.' })).toBeVisible();
  await expect(page.getByLabel('Suite contract manifest')).toContainText('Required evidence: transcript, action_trace, final_state');
  await expect(page.getByLabel(/Scenario contract: [a-f0-9]{12}/)).toBeVisible();
  await expect(page.getByLabel(/Suite manifest: [a-f0-9]{12}/)).toBeVisible();

  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByRole('heading', { name: /pass|needs_review/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Report brief' })).toBeVisible();

  const brief = page.getByLabel('Report brief');
  await expect(brief).toContainText('Scenario:');
  await expect(brief).toContainText('Verdict:');
  await expect(brief).toContainText('Score:');
  await expect(brief).toContainText('Regression:');
  await expect(brief).toContainText('Suite coverage:');
  await expect(brief).toContainText('Primary risk:');
  await expect(brief).toContainText('Next step:');
  await expect(brief).toContainText('Missing actions:');
  await expect(brief).toContainText('Suggested fixes:');

  await page.getByRole('button', { name: 'Copy brief' }).click();
  await expect(page.getByText('Copied report brief.')).toBeVisible();
});

test('benchmark report counts the current unsaved run in suite coverage', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  await page.route('**/api/product/runs?*', async (route) => {
    const params = new URL(route.request().url()).searchParams;
    const isSuiteCoverageRequest = params.get('suite_id') === 'call-center-voice-ai' && !params.get('scenario_id');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(isSuiteCoverageRequest ? [{
        id: 'legacy-suite-run',
        project_id: 'qa-project',
        firestore_path: 'users/demo-user/projects/qa-project/runs/legacy-suite-run',
        plan: 'starter',
        created_at: '2026-05-31T12:00:00+00:00',
        report: {
          run_id: 'legacy-suite-run',
          suite_id: 'call-center-voice-ai',
          scenario_id: 'legacy-escalation',
          scenario_title: 'Legacy Escalation',
          verdict: 'pass',
          overall_score: 80,
        },
      }] : []),
    });
  });

  await page.route('**/api/benchmarks/simulate', async (route) => {
    const payload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_id: payload.suite_id,
        scenario_id: payload.scenario_id,
        scenario_title: 'Billing Address Change',
        transcript: 'Agent verified the account and confirmed the new billing address.',
        action_trace: [{ action: 'confirm_address_update', result: 'success' }],
        final_state: { address_updated: true },
        benchmark_report: {
          run_id: 'current-unsaved-run',
          suite_id: payload.suite_id,
          scenario_id: payload.scenario_id,
          scenario_title: 'Billing Address Change',
          verdict: 'pass',
          overall_score: 94,
          evidence: ['Agent verified the account and confirmed the new billing address.'],
          recommendations: [],
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByLabel('Saved runs and e2e validation')).toContainText(
    '1/4 suite scenarios covered (25%); 3 missing: Angry Outage Escalation, Interruption and Correction Handling. Next: Angry Outage Escalation. Outside suite: Legacy Escalation.',
  );
  await expect(page.getByLabel('Report brief')).toContainText(
    'Suite coverage: 1/4 suite scenarios covered (25%); 3 missing: Angry Outage Escalation, Interruption and Correction Handling. Next: Angry Outage Escalation. Outside suite: Legacy Escalation.',
  );
  await expect(page.getByLabel('Operator action plan')).toContainText('Keep moving through uncovered scenarios');
  await expect(page.getByLabel('Operator action plan')).toContainText(
    'Run Angry Outage Escalation next to keep suite coverage moving before release review.',
  );
  await expect(page.getByLabel('Report brief')).toContainText(
    'Primary risk: 3 suite scenarios still need fresh coverage before release review.',
  );
  await expect(page.getByLabel('Report brief')).toContainText(
    'Next step: Run Angry Outage Escalation next to keep suite coverage moving before release review.',
  );
});

test('benchmark report marks complete suite coverage as ready for release review', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  await page.route('**/api/product/runs?*', async (route) => {
    const params = new URL(route.request().url()).searchParams;
    const isSuiteCoverageRequest = params.get('suite_id') === 'call-center-voice-ai' && !params.get('scenario_id');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(isSuiteCoverageRequest ? [
        {
          id: 'saved-angry-outage',
          project_id: 'qa-project',
          firestore_path: 'users/demo-user/projects/qa-project/runs/saved-angry-outage',
          plan: 'starter',
          created_at: '2026-05-31T12:00:00+00:00',
          report: {
            run_id: 'saved-angry-outage',
            suite_id: 'call-center-voice-ai',
            scenario_id: 'angry-outage-escalation',
            scenario_title: 'Angry Outage Escalation',
            verdict: 'pass',
            overall_score: 90,
          },
        },
        {
          id: 'saved-interruption',
          project_id: 'qa-project',
          firestore_path: 'users/demo-user/projects/qa-project/runs/saved-interruption',
          plan: 'starter',
          created_at: '2026-05-31T12:30:00+00:00',
          report: {
            run_id: 'saved-interruption',
            suite_id: 'call-center-voice-ai',
            scenario_id: 'interruption-correction-handling',
            scenario_title: 'Interruption and Correction Handling',
            verdict: 'pass',
            overall_score: 92,
          },
        },
        {
          id: 'saved-refund-policy',
          project_id: 'qa-project',
          firestore_path: 'users/demo-user/projects/qa-project/runs/saved-refund-policy',
          plan: 'starter',
          created_at: '2026-05-31T13:00:00+00:00',
          report: {
            run_id: 'saved-refund-policy',
            suite_id: 'call-center-voice-ai',
            scenario_id: 'refund-policy-boundary',
            scenario_title: 'Refund Policy Boundary',
            verdict: 'pass',
            overall_score: 93,
          },
        },
      ] : []),
    });
  });

  await page.route('**/api/benchmarks/simulate', async (route) => {
    const payload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_id: payload.suite_id,
        scenario_id: payload.scenario_id,
        scenario_title: 'Billing Address Change',
        transcript: 'Agent verified the account and confirmed the new billing address.',
        action_trace: [{ action: 'confirm_address_update', result: 'success' }],
        final_state: { address_updated: true },
        benchmark_report: {
          run_id: 'current-unsaved-run',
          suite_id: payload.suite_id,
          scenario_id: payload.scenario_id,
          scenario_title: 'Billing Address Change',
          verdict: 'pass',
          overall_score: 94,
          evidence: ['Agent verified the account and confirmed the new billing address.'],
          recommendations: [],
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByLabel('Saved runs and e2e validation')).toContainText('4/4 suite scenarios covered (100%); full suite covered.');
  await expect(page.getByLabel('Operator action plan')).toContainText('Ready for release review');
  await expect(page.getByLabel('Operator action plan')).toContainText('No blocking failure category was reported for this scenario.');
  await expect(page.getByLabel('Report brief')).toContainText(
    'Primary risk: No blocking failure category was reported for this scenario.',
  );
  await expect(page.getByLabel('Operator action plan')).toContainText(
    'Save this run as the baseline, then compare the next prompt or model change against it.',
  );
  await expect(page.getByLabel('Report brief')).toContainText(
    'Next step: Save this run as the baseline, then compare the next prompt or model change against it.',
  );
  await expect(page.getByRole('button', { name: 'Open next uncovered scenario' })).toHaveCount(0);
});

test('benchmark report keeps failure remediation guidance when the scenario needs review', async ({ page }) => {
  await page.route('**/api/benchmarks/simulate', async (route) => {
    const payload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_id: payload.suite_id,
        scenario_id: payload.scenario_id,
        scenario_title: 'Billing Address Change',
        transcript: 'Agent changed the billing address without confirming identity.',
        action_trace: [{ action: 'confirm_address_update', result: 'success' }],
        final_state: { address_updated: true },
        benchmark_report: {
          run_id: 'current-unsaved-run',
          suite_id: payload.suite_id,
          scenario_id: payload.scenario_id,
          scenario_title: 'Billing Address Change',
          verdict: 'needs_review',
          overall_score: 61,
          failure_categories: ['required_action_execution'],
          missing_actions: ['confirm identity'],
          recommendations: ['Confirm the caller identity before changing the billing address.'],
          evidence: ['Agent changed the billing address without confirming identity.'],
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByLabel('Operator action plan')).toContainText('Needs operator review');
  await expect(page.getByLabel('Operator action plan')).toContainText('required action execution');
  await expect(page.getByLabel('Operator action plan')).toContainText(
    'Confirm the caller identity before changing the billing address.',
  );
  await expect(page.getByLabel('Report brief')).toContainText('Primary risk: required action execution');
  await expect(page.getByLabel('Report brief')).toContainText(
    'Next step: Confirm the caller identity before changing the billing address.',
  );
});

test('suite coverage can jump to the next uncovered scenario', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  await page.route('**/api/product/runs?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.route('**/api/benchmarks/simulate', async (route) => {
    const payload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_id: payload.suite_id,
        scenario_id: payload.scenario_id,
        scenario_title: 'Billing Address Change',
        transcript: 'Agent verified the account and confirmed the new billing address.',
        action_trace: [{ action: 'confirm_address_update', result: 'success' }],
        final_state: { address_updated: true },
        benchmark_report: {
          run_id: 'current-unsaved-run',
          suite_id: payload.suite_id,
          scenario_id: payload.scenario_id,
          scenario_title: 'Billing Address Change',
          verdict: 'pass',
          overall_score: 94,
          evidence: ['Agent verified the account and confirmed the new billing address.'],
          recommendations: [],
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await page.getByRole('button', { name: 'Open next uncovered scenario' }).click();

  const benchmarkForm = page.locator('form').first();
  const scenarioSelect = benchmarkForm.locator('select').nth(1);
  const selectedScenario = page.getByLabel('Selected scenario');
  await expect(page.getByText('Switched to the next uncovered scenario: Angry Outage Escalation.')).toBeVisible();
  await expect(selectedScenario.getByRole('heading', { name: 'Angry Outage Escalation' })).toBeVisible();
  await expect(scenarioSelect).toHaveValue('angry-outage-escalation');
});


test('suite coverage can focus a specific uncovered scenario from the coverage card', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  await page.route('**/api/product/runs?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.route('**/api/benchmarks/simulate', async (route) => {
    const payload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_id: payload.suite_id,
        scenario_id: payload.scenario_id,
        scenario_title: 'Billing Address Change',
        transcript: 'Agent verified the account and confirmed the new billing address.',
        action_trace: [{ action: 'confirm_address_update', result: 'success' }],
        final_state: { address_updated: true },
        benchmark_report: {
          run_id: 'current-unsaved-run',
          suite_id: payload.suite_id,
          scenario_id: payload.scenario_id,
          scenario_title: 'Billing Address Change',
          verdict: 'pass',
          overall_score: 94,
          evidence: ['Agent verified the account and confirmed the new billing address.'],
          recommendations: [],
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await page.getByRole('button', { name: 'Refund Policy Boundary' }).click();

  const benchmarkForm = page.locator('form').first();
  const scenarioSelect = benchmarkForm.locator('select').nth(1);
  const selectedScenario = page.getByLabel('Selected scenario');
  await expect(page.getByText('Focused uncovered scenario: Refund Policy Boundary.')).toBeVisible();
  await expect(selectedScenario.getByRole('heading', { name: 'Refund Policy Boundary' })).toBeVisible();
  await expect(scenarioSelect).toHaveValue('refund-policy-boundary');
  await expect(page.getByLabel('Saved runs and e2e validation')).not.toContainText('Outside suite history:');
});

test('current benchmark report previews regression delta before saving', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  await page.route('**/api/product/runs?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 'saved-baseline',
          project_id: 'qa-project',
          firestore_path: 'users/demo-user/projects/qa-project/runs/saved-baseline',
          plan: 'starter',
          created_at: '2026-05-31T12:00:00+00:00',
          report: {
            run_id: 'saved-baseline',
            suite_id: 'call-center-voice-ai',
            scenario_id: 'billing-address-change',
            scenario_title: 'Billing Address Change',
            verdict: 'pass',
            overall_score: 88,
          },
          artifacts: {
            regression_delta: {
              status: 'baseline',
              previous_run_id: null,
              previous_overall_score: null,
              current_overall_score: 88,
              score_delta: null,
            },
            vcon_export: { available: false },
          },
        },
      ]),
    });
  });

  await page.route('**/api/benchmarks/simulate', async (route) => {
    const payload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suite_id: payload.suite_id,
        scenario_id: payload.scenario_id,
        scenario_title: 'Billing Address Change',
        transcript: 'Agent verified the account and confirmed the new billing address.',
        action_trace: [{ action: 'confirm_address_update', result: 'success' }],
        final_state: { address_updated: true },
        benchmark_report: {
          run_id: 'current-unsaved-run',
          suite_id: payload.suite_id,
          scenario_id: payload.scenario_id,
          scenario_title: 'Billing Address Change',
          verdict: 'pass',
          overall_score: 94,
          evidence: ['Agent verified the account and confirmed the new billing address.'],
          recommendations: [],
        },
      }),
    });
  });

  await page.goto('/benchmarks');
  await expect(page.getByRole('heading', { name: /1 saved for Billing Address Change/ })).toBeVisible();

  await page.getByRole('button', { name: 'Simulate scenario' }).click();

  await expect(page.getByLabel('Unsaved regression comparison')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Current run: improved' })).toBeVisible();
  await expect(page.getByLabel('Unsaved regression comparison')).toContainText('improved: 94 vs 88 (+6) against saved-baseline');
  await expect(page.getByLabel('Saved runs and e2e validation')).toContainText(
    '1/4 suite scenarios covered (25%); 3 missing: Angry Outage Escalation, Interruption and Correction Handling. Next: Angry Outage Escalation.',
  );

  const brief = page.getByLabel('Report brief');
  await expect(brief).toContainText('Regression: improved: 94 vs 88 (+6)');
  await expect(brief).toContainText('Suite coverage: 1/4 suite scenarios covered (25%); 3 missing: Angry Outage Escalation, Interruption and Correction Handling. Next: Angry Outage Escalation.');
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
  await expect(page.getByLabel('Contract: abcdef123456', { exact: true })).toBeVisible();
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
          run_lifecycle: {
            status: 'completed',
            terminal: true,
            transitions: [
              { to: 'queued', at: '2026-05-29T14:45:00+00:00', reason: 'Queued after sign-in.' },
              { to: 'running', at: '2026-05-29T14:46:00+00:00', reason: 'Scenario evaluation started.' },
              { to: 'completed', at: '2026-05-29T15:00:00+00:00', reason: 'Suite report retained.' },
            ],
          },
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
              {
                scenario_id: 'billing-escalation',
                run_id: 'scenario-run-2',
                status: 'needs_review',
                overall_score: 73,
                failure_categories: ['required_action_execution', 'task_completion'],
              },
            ],
          },
        },
        {
          suite_run_id: 'suite-history-0',
          suite_id: 'call-center-voice-ai',
          status: 'completed',
          scenario_count: 2,
          pass_count: 2,
          needs_review_count: 0,
          average_score: 76,
          updated_at: '2026-05-28T15:00:00+00:00',
          suite_report: { suite_name: 'Call Center Voice AI' },
          retention: { retained_until: '2026-08-26T15:00:00+00:00' },
          artifacts: {
            vcon_export: { available: false },
            scenario_summaries: [
              { scenario_id: 'membership-renewal-save', run_id: 'scenario-run-0', status: 'pass', overall_score: 76 },
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

  await page.route('**/api/benchmarks/suite-runs/suite-history-1/audit-artifacts?*', async (route) => {
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
        filename: 'agentbench-call-center-voice-ai-suite-history-1-suite-audit-artifacts.json',
        operator_summary: {
          ready_for_export: true,
          ready_scenarios: 2,
          missing_scenarios: 0,
        },
        scenario_artifacts: [
          { scenario_id: 'membership-renewal-save', ready_for_export: true },
          { scenario_id: 'billing-escalation', ready_for_export: true },
        ],
        generated_at: '2026-05-29T15:00:00+00:00',
      }),
    });
  });

  await page.route('**/api/benchmarks/runs/export?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'benchmark-history-demo-user-qa-project-call-center-voice-ai',
        user_id: 'demo-user',
        project_id: 'qa-project',
        suite_id: 'call-center-voice-ai',
        scenario_id: 'membership-renewal-save',
        filename: 'agentbench-qa-project-call-center-voice-ai-run-history.json',
        run_count: 2,
        summary: {
          latest_run_id: 'scenario-run-2',
          latest_status: 'fail',
          latest_score: 73,
          previous_score: 91,
          latest_delta: -18,
          latest_trend: 'regressed',
          status_counts: { pass: 1, fail: 1 },
          failure_category_counts: { required_action_execution: 1, task_completion: 1 },
          top_failure_categories: [
            { category: 'required_action_execution', count: 1 },
            { category: 'task_completion', count: 1 },
          ],
        },
        scenario_coverage_summary: {
          suite_id: 'call-center-voice-ai',
          scenario_count: 4,
          covered_scenario_count: 2,
          coverage_percent: 50,
          covered_scenario_ids: ['membership-renewal-save', 'billing-escalation'],
          missing_scenario_ids: ['refund-policy-boundary', 'interruption-correction-handling'],
          out_of_suite_scenario_ids: ['legacy-escalation'],
          covered_scenarios: [
            { id: 'membership-renewal-save', title: 'Membership Renewal Save' },
            { id: 'billing-escalation', title: 'Billing Escalation' },
          ],
          missing_scenarios: [
            { id: 'refund-policy-boundary', title: 'Refund Policy Boundary' },
            { id: 'interruption-correction-handling', title: 'Interruption and Correction Handling' },
          ],
          out_of_suite_scenarios: [
            { id: 'legacy-escalation', title: 'Legacy Escalation' },
          ],
          recommended_next_scenario: { id: 'refund-policy-boundary', title: 'Refund Policy Boundary' },
          coverage_status: 'partial',
        },
        vcon_export_summary: {
          available_records: 2,
          missing_records: 0,
          total_runs: 2,
          dialog_turns: 6,
          analysis_records: 2,
        },
        contract_artifact_summary: {
          available_records: 2,
          total_runs: 2,
          suite_contract_manifest_sha256s: ['abcdef1234567890'],
          scenario_contract_sha256s: ['123456abcdef7890'],
        },
        runs: [{ run_id: 'scenario-run-2' }, { run_id: 'scenario-run-1' }],
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
        suite_run_count: 2,
        summary: {
          latest_suite_run_id: 'suite-history-1',
          latest_status: 'completed',
          latest_average_score: 82,
          previous_average_score: 76,
          latest_delta: 6,
          latest_trend: 'improved',
          status_counts: { completed: 2 },
          total_scenarios: 4,
          total_passes: 3,
          total_needs_review: 1,
          pass_rate: 75,
          failure_category_counts: { required_action_execution: 1, task_completion: 1 },
          top_failure_categories: [
            { category: 'required_action_execution', count: 1 },
            { category: 'task_completion', count: 1 },
          ],
        },
        scenario_coverage_summary: {
          suite_id: 'call-center-voice-ai',
          scenario_count: 4,
          covered_scenario_count: 4,
          coverage_percent: 100,
          covered_scenario_ids: ['membership-renewal-save', 'billing-escalation', 'refund-policy-boundary', 'interruption-correction-handling'],
          missing_scenario_ids: [],
          covered_scenarios: [
            { id: 'membership-renewal-save', title: 'Membership Renewal Save' },
            { id: 'billing-escalation', title: 'Billing Escalation' },
            { id: 'refund-policy-boundary', title: 'Refund Policy Boundary' },
            { id: 'interruption-correction-handling', title: 'Interruption and Correction Handling' },
          ],
          missing_scenarios: [],
          recommended_next_scenario: null,
          coverage_status: 'complete',
        },
        vcon_export_summary: {
          available_records: 3,
          missing_records: 0,
          total_runs: 2,
          dialog_turns: 4,
          analysis_records: 1,
        },
        suite_runs: [{ suite_run_id: 'suite-history-1', status: 'completed' }, { suite_run_id: 'suite-history-0', status: 'completed' }],
        exported_at: '2026-05-29T15:00:00+00:00',
      }),
    });
  });

  await page.goto('/benchmarks');

  const suiteHistory = page.getByLabel('Suite run history');
  const latestSuiteRun = suiteHistory.locator('article').first();
  await expect(suiteHistory).toBeVisible();
  await expect(suiteHistory.getByRole('heading', { name: /2 suite runs/ })).toBeVisible();
  await expect(latestSuiteRun.locator('strong').filter({ hasText: 'Call Center Voice AI' })).toBeVisible();
  await expect(latestSuiteRun.locator('div').first().getByText('completed')).toBeVisible();
  await expect(suiteHistory.getByText('4 dialog turns, 1 analysis records')).toBeVisible();
  await expect(suiteHistory.getByText('EVA-style reliability: 82% accuracy, 100% experience coverage, 4 avg turns.')).toBeVisible();
  await expect(suiteHistory.getByText('Lifecycle: queued -> running -> completed')).toBeVisible();
  await expect(latestSuiteRun.getByLabel('Audit timeline for Call Center Voice AI').getByText('Queued after sign-in.')).toBeVisible();
  await expect(latestSuiteRun.getByLabel('Audit timeline for Call Center Voice AI').getByText('Suite report retained.')).toBeVisible();
  await expect(suiteHistory.getByText('Robustness tags: accent, noise.')).toBeVisible();
  await expect(suiteHistory.getByText('Failure mix: required action execution (1), task completion (1).')).toBeVisible();
  await expect(suiteHistory.getByText('Visible Suite trend improved: 82 vs 76 (+6), 75% pass rate. Top issue: required action execution (1).')).toBeVisible();
  await expect(suiteHistory.getByText(/billing-escalation: needs_review \/ 73 .*required action execution, task completion/)).toBeVisible();

  const benchmarkHistoryDownloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export benchmark history' }).click();
  const benchmarkHistoryDownload = await benchmarkHistoryDownloadPromise;
  expect(benchmarkHistoryDownload.suggestedFilename()).toBe('agentbench-qa-project-call-center-voice-ai-run-history.json');
  await expect(page.getByText(/^Exported 2 benchmark runs to .* Benchmark trend regressed: 73 vs 91 \(-18\)\. Top issue: required action execution \(1\)\. 2\/4 suite scenarios covered \(50%\); 2 missing: Refund Policy Boundary, Interruption and Correction Handling\. Next: Refund Policy Boundary\. Outside suite: Legacy Escalation\./)).toBeVisible();

  await suiteHistory.getByLabel('Filter suite runs by status').selectOption('completed');

  const historyDownloadPromise = page.waitForEvent('download');
  await suiteHistory.getByRole('button', { name: 'Export suite history' }).click();
  const historyDownload = await historyDownloadPromise;
  expect(historyDownload.suggestedFilename()).toBe('agentbench-qa-project-call-center-voice-ai-suite-run-history.json');
  await expect(page.getByText(/^Exported 2 suite runs to .* Suite trend improved: 82 vs 76 \(\+6\), 75% pass rate\. Top issue: required action execution \(1\)\. 4\/4 suite scenarios covered \(100%\); full suite covered\. Covered: Membership Renewal Save, Billing Escalation\./)).toBeVisible();
  await expect(page.getByText('3/2 vCon-ready runs with 4 dialog turns and 1 analysis records.')).toBeVisible();

  await latestSuiteRun.getByRole('button', { name: 'Load suite run' }).click();
  await expect(page.getByText('Loaded retained suite run suite-history-1.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'pass' })).toBeVisible();
  await expect(page.locator('textarea').first()).toHaveValue('Agent retained the renewing member after validating intent.');
  await expect(page.getByLabel('Suite simulation summary')).toBeVisible();
  await expect(page.getByPlaceholder('tightened escalation policy')).toHaveValue('Loaded from retained suite history.');

  const auditDownloadPromise = page.waitForEvent('download');
  await latestSuiteRun.getByRole('button', { name: 'Export suite audit artifacts' }).click();
  const auditDownload = await auditDownloadPromise;
  expect(auditDownload.suggestedFilename()).toBe('agentbench-call-center-voice-ai-suite-history-1-suite-audit-artifacts.json');
  await expect(page.getByText('Exported suite audit artifacts for 2 ready scenarios')).toBeVisible();

  const downloadPromise = page.waitForEvent('download');
  await latestSuiteRun.getByRole('button', { name: 'Export retained vCon bundle' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('agentbench-call-center-voice-ai-suite-history-1-vcon-bundle.json');
  await expect(page.getByText('Exported 3 retained suite vCon records')).toBeVisible();
});


test('suite run history can be refreshed on demand', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'qa-project');
  });

  let requestCount = 0;
  await page.route('**/api/benchmarks/suite-runs?*', async (route) => {
    requestCount += 1;
    const runs = requestCount === 1 ? [] : [
      {
        suite_run_id: 'suite-refresh-1',
        suite_id: 'call-center-voice-ai',
        status: 'queued',
        scenario_count: 4,
        pass_count: 0,
        needs_review_count: 0,
        average_score: 0,
        updated_at: '2026-06-01T09:00:00Z',
        retention: { retained_until: '2026-07-16T09:00:00Z', retention_days: 45 },
        progress: { phase: 'queued', active: true, completed_scenarios: 0, total_scenarios: 4, percent: 0 },
        artifacts: { scenario_summaries: [], vcon_export: { available: false } },
        run_lifecycle: { status: 'queued', terminal: false, transitions: [{ to: 'queued', at: '2026-06-01T09:00:00Z', reason: 'queued for background execution' }] },
      },
    ];
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runs) });
  });

  await page.goto('/benchmarks');

  const suiteHistory = page.getByLabel('Suite run history');
  await expect(suiteHistory.getByRole('heading', { name: /0 suite runs for Call Center Voice AI/ })).toBeVisible();

  await page.getByRole('button', { name: 'Refresh suite runs' }).click();

  await expect(page.getByText('Refreshed 1 suite runs for Call Center Voice AI.')).toBeVisible();
  await expect(suiteHistory.getByRole('heading', { name: /1 suite runs for Call Center Voice AI/ })).toBeVisible();
  await expect(suiteHistory.getByText('call-center-voice-ai')).toBeVisible();
  await expect(page.getByLabel('Latest suite run update')).toContainText('Jun 1, 2026');
  await expect(page.getByLabel('Progress: 0/4 (0%)')).toBeVisible();
});
