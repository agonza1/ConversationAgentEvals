import { expect, test } from '@playwright/test';

const runFixture = {
  execution_run_id: 'exec-demo123',
  status: 'completed',
  mode: 'voice_fixture',
  suite_id: 'call-center-voice-ai',
  scenario_ids: ['cancellation-rescue'],
  user_id: 'demo-user',
  project_id: 'call-center-demo',
  agent_id: 'acc-voice-fixture-agent',
  agent_name: 'ACC voice fixture agent',
  tester_id: 'fixture_replay',
  executor_id: 'evidence_replay',
  provenance: {
    target_id: null,
    target_kind: 'saved_voice_replay',
    target_channel: 'voice',
    tester_id: 'fixture_replay',
    executor_id: 'evidence_replay',
    evidence_source: 'saved_replay',
    live_external_connection: false,
    saved_evidence: true,
    synthetic_media: true,
    honesty_label: 'Saved conversation replay · evidence evaluation · no live call',
  },
  execution_snapshot: {
    agent: { target: 'voice_fixture', environment: 'local' },
  },
  progress: {
    phase: 'completed',
    completed_conversations: 1,
    total_conversations: 1,
    percent: 100,
  },
  conversations: [
    {
      conversation_id: 'exec-demo123-cancellation-rescue-1',
      execution_run_id: 'exec-demo123',
      suite_id: 'call-center-voice-ai',
      scenario_id: 'cancellation-rescue',
      scenario_title: 'Cancellation Rescue',
      mode: 'voice_fixture',
      status: 'completed',
      turns: [
        { turn_index: 1, speaker: 'caller', text: 'I want to cancel today.' },
        { turn_index: 2, speaker: 'agent', text: 'I can help with that.' },
      ],
      transcript: 'Caller: I want to cancel today.\nAgent: I can help with that.',
      action_trace: [{ action: 'confirm cancellation outcome', status: 'completed' }],
      final_state: {
        complete: true,
        outcome: 'scripted_wrap_complete',
        termination_reason: 'plan_complete',
        runtime_provenance: { live_tool_execution: true },
      },
      latency_marks: [
        { label: 'first_response', latency_ms: 420 },
        { label: 'wrap', latency_ms: 880 },
      ],
      metrics_summary: {
        verdict: 'pass',
        score: 91,
        turn_count: 2,
        latency: {
          count: 2,
          avg_ms: 650,
          median_ms: 650,
          p90_ms: 880,
          min_ms: 420,
          max_ms: 880,
          outlier_count: 0,
        },
        interruption_count: 1,
        call_resolution_success: 100,
      },
      timeline: [
        { t_ms: 0, label: 'caller', latency_ms: 420, kind: 'turn' },
        { t_ms: 420, label: 'agent', latency_ms: 880, kind: 'turn' },
      ],
      verdict: 'pass',
      score: 91,
    },
  ],
  created_at: '2026-07-16T00:00:00Z',
  updated_at: '2026-07-16T00:00:01Z',
  completed_at: '2026-07-16T00:00:01Z',
};

test('runs analysis page shows metric tiles and transcript', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });

  await page.route('**/api/execution/runs**', async (route) => {
    const url = route.request().url();
    if (url.includes('/exec-demo123')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runFixture) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([runFixture]) });
  });

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Run an agent' })).toBeVisible();
  await expect(page.getByRole('link', { name: /ACC voice fixture agent/ })).toBeVisible();

  await page.getByRole('link', { name: /ACC voice fixture agent/ }).click();
  await expect(page.getByRole('heading', { name: 'ACC voice fixture agent' })).toBeVisible();
  const participants = page.getByLabel('Run participants and executor');
  await expect(participants).toContainText('Saved Conversation Replay');
  await expect(participants).toContainText('Evidence Replay');
  await expect(participants).toContainText('saved evidence replay');
  await expect(page.getByRole('button', { name: /Interruption Detection/ }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /Latency/ }).first()).toBeVisible();
  await page.getByRole('button', { name: /Verified Resolution Rate/ }).click();
  await expect(page.getByLabel('Resolution verification status')).toContainText('Verified');
  await expect(page.getByLabel('Resolution evidence details')).toContainText('91/100');
  await expect(page.getByLabel('Resolution evidence details')).toContainText('Complete');
  await expect(page.getByLabel('Stub dual-track waveform')).toBeVisible();
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.');
});

test('needs-review resolution explains score and missing proof without calling it a failed call', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  const conversation = {
    ...runFixture.conversations[0],
    mode: 'text_callable',
    verdict: 'needs_review',
    score: 60,
    action_trace: [],
    evaluation_findings: {
      hard_check_failures: [
        { category: 'forbidden_action', action: 'provide_medical_diagnosis' },
        {
          category: 'bad_order',
          action: 'schedule_telehealth_appointment',
          expected_after: 'explain_privacy_consent',
        },
        {
          category: 'final_state_mismatch',
          path: 'appointment_status',
          expected: 'scheduled',
          actual: 'pending',
        },
      ],
    },
    final_state: {
      complete: false,
      outcome: 'conversation_only_evidence_recorded',
      termination_reason: 'max_exchanges',
      runtime_provenance: { live_tool_execution: false },
    },
    metrics_summary: {
      ...runFixture.conversations[0].metrics_summary,
      verdict: 'needs_review',
      score: 60,
      call_resolution_success: 0,
    },
  };
  const reviewRun = {
    ...runFixture,
    status: 'needs_review',
    mode: 'text_callable',
    conversations: [conversation],
  };

  await page.route('**/api/execution/runs/exec-demo123**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(reviewRun) });
  });

  await page.goto('/runs/exec-demo123');
  const resolutionTile = page.getByRole('button', { name: /Verified Resolution Rate/ });
  await expect(resolutionTile).toContainText('0%');
  await expect(resolutionTile).toContainText('1 unverified');
  await expect(resolutionTile).toHaveClass(/is-selected/);
  await expect(page.getByRole('heading', { name: 'Resolution Evidence' })).toBeVisible();

  await expect(page.getByLabel('Resolution verification status')).toContainText('Unverified');
  const details = page.getByLabel('Resolution evidence details');
  await expect(details).toContainText('60/100');
  await expect(details).toContainText('Needs Review');
  await expect(details).toContainText('Not complete');
  await expect(details).toContainText('Max Exchanges');
  await expect(details).toContainText('None recorded');
  await expect(page.getByText('Why resolution is not verified')).toBeVisible();
  await expect(page.getByText('No action or tool evidence was recorded.')).toBeVisible();
  await expect(page.getByText('The conversation reached the configured exchange limit.')).toBeVisible();
  await expect(page.getByText('Hard-check failure: Forbidden Action — Action: Provide Medical Diagnosis.')).toBeVisible();
  await expect(page.getByText(
    'Hard-check failure: Bad Order — Schedule Telehealth Appointment was observed before Explain Privacy Consent.',
  )).toBeVisible();
  await expect(page.getByText(
    'Hard-check failure: Final State Mismatch — Appointment Status expected "scheduled", got "pending".',
  )).toBeVisible();
  await expect(page.getByText(/evaluation score is not a resolution percentage/)).toBeVisible();
});

test('run detail requests the LLM judge for the selected persisted conversation', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  const conversation = {
    ...runFixture.conversations[0],
    mode: 'text_callable',
    verdict: 'needs_review',
    score: 60,
    action_trace: [{ action: 'verify_identity', status: 'completed', identifiers: ['email', 'last4'] }],
    evaluation_findings: {
      missing_actions: ['policy_hold_entered'],
      rubric_checks: [{ name: 'retention_policy', status: 'failed' }],
      hard_check_failures: [{ category: 'policy', summary: 'Required policy hold was not entered.' }],
      scenario_contract: {
        required_actions: [{ id: 'policy_hold_entered', description: 'Enter the required policy hold.' }],
      },
    },
    final_state: {
      complete: false,
      outcome: 'conversation_only_evidence_recorded',
      termination_reason: 'max_exchanges',
      runtime_provenance: { live_tool_execution: false },
    },
    metrics_summary: {
      ...runFixture.conversations[0].metrics_summary,
      verdict: 'needs_review',
      score: 60,
      call_resolution_success: 0,
    },
  };
  const reviewRun = {
    ...runFixture,
    status: 'needs_review',
    mode: 'text_callable',
    conversations: [conversation],
  };
  let judgeRequest: Record<string, unknown> | null = null;
  let judgeCalls = 0;

  await page.route('**/api/execution/runs/exec-demo123**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(reviewRun) });
  });
  await page.route('**/api/product/judge', async (route) => {
    judgeCalls += 1;
    judgeRequest = route.request().postDataJSON() as Record<string, unknown>;
    if (judgeCalls === 2) {
      await route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'temporary judge failure' }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ready',
        required_plan: 'starter',
        credits: 10,
        message: 'LLM judge disagrees with the deterministic verdict via openai_codex.',
        evidence_citations: ['source=caller; text=I want to cancel today.'],
        judge_result: {
          agrees: false,
          rationale: 'The exchange did not prove a completed business action.',
          next_action: 'Capture tool output and the resulting account state.',
        },
        provider: 'openai_codex',
        model: 'gpt-5.4-mini',
        prompt_preview: 'Deterministic verdict: needs_review\nDeterministic score: 60',
        latency_ms: 840,
        spend_control: { remaining_daily_credits: 190 },
      }),
    });
  });

  await page.goto('/runs/exec-demo123');
  await expect(page.getByRole('heading', { name: 'Review the deterministic verdict' })).toBeVisible();
  await expect(page.getByText('Missing required action: Policy Hold Entered.')).toBeVisible();
  await expect(page.getByText('Failed rubric check: Retention Policy.')).toBeVisible();
  await expect(page.getByText('Hard-check failure: Policy — Required policy hold was not entered.')).toBeVisible();
  expect(judgeCalls).toBe(0);

  await page.getByRole('button', { name: 'Review with LLM judge' }).click();
  await expect(page.getByLabel('LLM judge result')).toContainText('Disagrees with the deterministic verdict');
  await expect(page.getByLabel('LLM judge result')).toContainText(
    'The exchange did not prove a completed business action.',
  );
  await expect(page.getByLabel('LLM judge result')).toContainText(
    'Capture tool output and the resulting account state.',
  );
  await expect(page.getByLabel('LLM judge result')).toContainText('gpt-5.4-mini');
  await expect(page.getByLabel('LLM judge result')).toContainText('190 daily credits remaining');
  expect(judgeCalls).toBe(1);
  expect(judgeRequest).toMatchObject({
    plan: 'free',
    user_id: 'demo-user',
    execution_run_id: 'exec-demo123',
    conversation_id: conversation.conversation_id,
  });
  expect(judgeRequest).not.toHaveProperty('project_id');
  expect(judgeRequest).not.toHaveProperty('transcript');
  expect(judgeRequest).not.toHaveProperty('report');

  await page.getByRole('button', { name: 'What the judge saw' }).click();
  await expect(page.getByText('Deterministic verdict: needs_review')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run LLM review again' })).toBeVisible();

  await page.getByRole('button', { name: 'Run LLM review again' }).click();
  await expect(page.getByText('temporary judge failure', { exact: true })).toBeVisible();
  await expect(page.getByLabel('LLM judge result')).toHaveCount(0);
});

test('user can confirm an LLM adjudication while the automatic evaluation remains auditable', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  const conversation = {
    ...runFixture.conversations[0],
    mode: 'text_callable',
    verdict: 'needs_review',
    score: 40,
    transcript: [
      'Patient: I have a persistent cough and would like a same-day telehealth visit.',
      'Agent: What is your name and date of birth?',
      'Patient: Ana Reed, June 2, 1990.',
    ].join('\n'),
    action_trace: [],
    evaluation_findings: {
      missing_actions: [
        'collect_patient_name_and_date_of_birth',
        'schedule_telehealth_appointment',
        'explain_privacy_consent',
      ],
      hard_check_failures: [{ category: 'missing_action' }],
    },
    final_state: {
      complete: false,
      outcome: 'conversation_only_evidence_recorded',
      termination_reason: 'max_exchanges',
      runtime_provenance: { live_tool_execution: false },
    },
    metrics_summary: {
      ...runFixture.conversations[0].metrics_summary,
      verdict: 'needs_review',
      score: 40,
      call_resolution_success: 0,
    },
  };
  let currentRun: Record<string, unknown> = {
    ...runFixture,
    status: 'needs_review',
    mode: 'text_callable',
    conversations: [conversation],
  };
  let applyCalls = 0;

  await page.route('**/api/execution/runs/exec-demo123**', async (route) => {
    if (route.request().method() === 'POST' && route.request().url().includes('/judge-reviews/')) {
      applyCalls += 1;
      expect(route.request().postDataJSON()).toEqual({ user_id: 'demo-user', confirm: true });
      const appliedAt = '2026-07-26T16:00:00Z';
      currentRun = {
        ...currentRun,
        updated_at: appliedAt,
        conversations: [{
          ...conversation,
          judge_reviews: [{
            review_id: 'judge-review-telehealth',
            status: 'applied',
            created_at: '2026-07-26T15:59:00Z',
            applied_at: appliedAt,
          }],
          evaluation_adjudication: {
            review_id: 'judge-review-telehealth',
            source: 'llm_judge',
            status: 'applied',
            applied_at: appliedAt,
            applied_by_user_id: 'demo-user',
            provider: 'openai_codex',
            model: 'gpt-5.4-mini',
            judge_result: {
              agrees: true,
              rationale: 'The overall needs-review verdict is right, but identity was collected.',
              next_action: 'Capture privacy consent and complete scheduling.',
              proposed_evaluation: {
                verdict: 'needs_review',
                summary: 'Identity was collected; consent and scheduling remain unproven.',
                corrected_findings: [
                  'Patient name and date of birth were collected in the transcript.',
                ],
                remaining_gaps: [
                  'Explicit privacy consent was not recorded.',
                  'No completed scheduling action or final state was recorded.',
                ],
              },
            },
            deterministic_snapshot: {
              verdict: 'needs_review',
              score: 40,
              evidence_sha256: 'automatic-evidence-digest',
            },
          },
        }],
      };
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentRun),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(currentRun),
    });
  });
  await page.route('**/api/product/judge', async (route) => {
    const payload = route.request().postDataJSON();
    expect(payload).toMatchObject({
      execution_run_id: 'exec-demo123',
      conversation_id: conversation.conversation_id,
      user_id: 'demo-user',
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ready',
        required_plan: 'starter',
        credits: 10,
        message: 'LLM judge agrees with the deterministic verdict via openai_codex.',
        evidence_citations: ['source=patient; text=Ana Reed, June 2, 1990.'],
        review_id: 'judge-review-telehealth',
        judge_result: {
          agrees: true,
          rationale: 'The overall needs-review verdict is right, but identity was collected.',
          next_action: 'Capture privacy consent and complete scheduling.',
          proposed_evaluation: {
            verdict: 'needs_review',
            summary: 'Identity was collected; consent and scheduling remain unproven.',
            corrected_findings: [
              'Patient name and date of birth were collected in the transcript.',
            ],
            remaining_gaps: [
              'Explicit privacy consent was not recorded.',
              'No completed scheduling action or final state was recorded.',
            ],
          },
        },
        provider: 'openai_codex',
        model: 'gpt-5.4-mini',
        latency_ms: 2259,
        spend_control: { remaining_daily_credits: 180 },
      }),
    });
  });

  await page.goto('/runs/exec-demo123');
  await expect(page.getByText('Missing required action: Collect Patient Name And Date Of Birth.')).toBeVisible();
  await page.getByRole('button', { name: 'Review with LLM judge' }).click();
  const judgeResult = page.getByLabel('LLM judge result');
  await expect(judgeResult).toContainText('Patient name and date of birth were collected in the transcript.');
  await expect(judgeResult.getByRole('button', { name: 'Apply proposed evaluation' })).toBeVisible();

  await judgeResult.getByRole('button', { name: 'Apply proposed evaluation' }).click();
  const dialog = page.getByRole('dialog', { name: 'Apply this LLM adjudication?' });
  await expect(dialog).toContainText('The automatic verdict, score, findings, and evidence remain preserved');
  await dialog.getByRole('button', { name: 'Apply adjudication' }).click();

  const applied = page.getByLabel('Applied LLM adjudication');
  await expect(applied).toContainText('Identity was collected; consent and scheduling remain unproven.');
  await expect(applied).toContainText('Patient name and date of birth were collected in the transcript.');
  await expect(page.getByRole('heading', { name: 'Remaining gaps after adjudication' })).toBeVisible();
  await expect(page.getByText('Explicit privacy consent was not recorded.')).toBeVisible();
  await expect(page.getByText('Missing required action: Collect Patient Name And Date Of Birth.')).toBeHidden();
  await applied.getByText('Original automatic findings').click();
  await expect(page.getByText('Missing required action: Collect Patient Name And Date Of Birth.')).toBeVisible();
  expect(applyCalls).toBe(1);
});

test('resolution evidence handles failed and not-evaluated conversations without metric summaries', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  const failedConversation = {
    ...runFixture.conversations[0],
    conversation_id: 'exec-demo123-failed-1',
    scenario_id: 'failed-target',
    scenario_title: 'Failed target',
    status: 'failed',
    verdict: 'fail',
    score: 0,
    error: 'simulated provider disconnect',
    metrics_summary: null,
    action_trace: [],
    final_state: {
      complete: false,
      outcome: 'runner_error',
      termination_reason: 'provider_disconnect',
    },
  };
  const notEvaluatedConversation = {
    ...runFixture.conversations[0],
    conversation_id: 'exec-demo123-not-evaluated-1',
    scenario_id: 'not-evaluated',
    scenario_title: 'Not evaluated',
    status: 'completed',
    verdict: null,
    score: null,
    metrics_summary: null,
    action_trace: [],
    final_state: { tester_termination_reason: 'target_terminal_state' },
  };
  const edgeRun = {
    ...runFixture,
    status: 'failed',
    conversations: [failedConversation, notEvaluatedConversation],
  };

  await page.route('**/api/execution/runs/exec-demo123**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(edgeRun) });
  });

  await page.goto('/runs/exec-demo123');
  const resolutionTile = page.getByRole('button', { name: /Verified Resolution Rate/ });
  await expect(resolutionTile).toContainText('0 verified · 0 unverified · 1 failed · 1 not evaluated');
  await resolutionTile.click();

  await expect(page.getByLabel('Resolution verification status')).toContainText('Failed');
  await expect(page.getByLabel('Resolution evidence details')).toContainText('Not reported');
  await expect(page.getByLabel('Resolution evidence details')).toContainText('Provider Disconnect');
  await expect(page.getByLabel('Resolution evidence details')).toContainText('simulated provider disconnect');
  await expect(page.getByText('The run recorded an execution error.')).toBeVisible();

  let judgeRequest: Record<string, unknown> | null = null;
  await page.route('**/api/product/judge', async (route) => {
    judgeRequest = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'blocked',
        required_plan: 'starter',
        credits: 10,
        message: 'Connect OpenAI to run the local LLM judge.',
        evidence_citations: [],
        block_reason: 'provider',
      }),
    });
  });
  await page.getByRole('button', { name: 'Review with LLM judge' }).click();
  await expect(page.getByLabel('LLM judge result')).toContainText('LLM judge unavailable');
  expect(judgeRequest).toMatchObject({
    execution_run_id: 'exec-demo123',
    conversation_id: failedConversation.conversation_id,
    user_id: 'demo-user',
  });
  expect(judgeRequest).not.toHaveProperty('report');
  expect(judgeRequest).not.toHaveProperty('transcript');

  await page.getByLabel('Conversation').selectOption('exec-demo123-not-evaluated-1');
  await expect(page.getByLabel('Resolution verification status')).toContainText('Not evaluated');
  await expect(page.getByLabel('Resolution verification status')).toContainText(
    'No resolution verdict is available for this conversation.',
  );
  await expect(page.getByLabel('Resolution evidence details')).toContainText('Target Terminal State');
  await expect(page.getByRole('button', { name: 'Unavailable without evaluator verdict' })).toBeDisabled();
});

test('LLM judge stays disabled while conversation evidence is still changing', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  const activeRun = {
    ...runFixture,
    status: 'running',
    updated_at: '2026-07-16T00:00:00Z',
    conversations: [
      {
        ...runFixture.conversations[0],
        status: 'running',
        transcript: '',
        turns: [],
        verdict: null,
        score: null,
        metrics_summary: null,
      },
      {
        ...runFixture.conversations[0],
        conversation_id: 'exec-demo123-completed-1',
      },
    ],
  };
  let judgeCalls = 0;

  await page.route('**/api/execution/runs/exec-demo123**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(activeRun) });
  });
  await page.route('**/api/product/judge', async (route) => {
    judgeCalls += 1;
    await route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
  });

  await page.goto('/runs/exec-demo123');
  await expect(page.getByRole('button', { name: /Verified Resolution Rate/ })).toContainText('50%');
  await expect(page.getByRole('button', { name: /Verified Resolution Rate/ })).toContainText('1 not evaluated');
  const judgeButton = page.getByRole('button', { name: 'Available after run completes' });
  await expect(judgeButton).toBeVisible();
  await expect(judgeButton).toBeDisabled();
  expect(judgeCalls).toBe(0);
});

test('active run analysis recovers after a transient polling error', async ({ page }) => {
  let requests = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  await page.route('**/api/execution/runs/exec-demo123**', async (route) => {
    requests += 1;
    if (requests === 1) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...runFixture, status: 'running', conversations: [] }),
      });
      return;
    }
    if (requests === 2) {
      await route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: 'temporary upstream error' }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runFixture) });
  });

  await page.goto('/runs/exec-demo123');
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.', { timeout: 10_000 });
  expect(requests).toBeGreaterThanOrEqual(3);
  await expect(page.locator('.scenarios-error')).toHaveCount(0);
});

test('runs list preserves an API base override in analysis links', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });

  await page.route('http://api.example.test/api/execution/runs**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([runFixture]) });
  });

  await page.goto('/runs?api_base=http%3A%2F%2Fapi.example.test');
  await expect(page.getByRole('link', { name: /ACC voice fixture agent/ })).toHaveAttribute(
    'href',
    '/runs/exec-demo123?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Scenarios' })).toHaveAttribute(
    'href',
    '/scenarios?api_base=http%3A%2F%2Fapi.example.test',
  );
});

test('runs list exposes readable status filtering and run metadata', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });

  const reviewRun = {
    ...runFixture,
    execution_run_id: 'exec-review456',
    status: 'needs_review',
    agent_name: 'Billing support staging',
    tester_id: 'scenario_simulator',
  };
  await page.route('**/api/execution/runs**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([runFixture, reviewRun]) });
  });

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Billing support staging/ })).toContainText('needs review');
  await expect(page.getByRole('link', { name: /Billing support staging/ })).toContainText('scenario simulator');
  await page.getByLabel('Filter runs by status').selectOption('completed');
  await expect(page.getByRole('link', { name: /ACC voice fixture agent/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Billing support staging/ })).toHaveCount(0);
});

test('run analysis preserves an API base override on the All runs link', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });
  await page.route('http://api.example.test/api/execution/runs/exec-demo123**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runFixture) });
  });

  await page.goto('/runs/exec-demo123?api_base=http%3A%2F%2Fapi.example.test');
  await expect(page.getByRole('link', { name: 'All runs' })).toHaveAttribute(
    'href',
    '/runs?api_base=http%3A%2F%2Fapi.example.test',
  );
});

test('text agent analysis hides the stub waveform', async ({ page }) => {
  const textRun = {
    ...runFixture,
    execution_run_id: 'exec-text-demo',
    mode: 'text_callable',
    agent_id: 'mock-text-agent',
    agent_name: 'Mock text agent',
    conversations: [
      {
        ...runFixture.conversations[0],
        conversation_id: 'exec-text-demo-1',
        execution_run_id: 'exec-text-demo',
        mode: 'text_callable',
      },
    ],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });

  await page.route('**/api/execution/runs**', async (route) => {
    const url = route.request().url();
    if (url.includes('/exec-text-demo')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(textRun) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([textRun]) });
  });

  await page.goto('/runs/exec-text-demo');
  await expect(page.getByRole('heading', { name: 'Mock text agent' })).toBeVisible();
  await expect(page.getByLabel('Stub dual-track waveform')).toHaveCount(0);
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.');
});
