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
        {
          turn_index: 1,
          speaker: 'caller',
          text: 'I want to cancel today.',
          frame_metadata: {
            source_text: 'I want to cancel today.',
            asr_receipt: 'I want to cancel today.',
          },
        },
        {
          turn_index: 2,
          speaker: 'agent',
          text: 'I can help with that.',
          frame_metadata: {
            source_text: 'I can help you with that.',
            asr_receipt: 'I can help with that.',
          },
        },
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
  await expect(page.getByRole('button', { name: /^Latency/ }).first()).toBeVisible();
  const werTile = page.getByRole('button', { name: /Word Error Rate 9.1%/ }).first();
  await expect(werTile).toBeVisible();
  await werTile.click();
  await expect(page.getByRole('heading', { name: 'Word Error Rate' })).toBeVisible();
  await expect(page.getByLabel('Word error rate summary')).toContainText('1');
  const perTurnWer = page.getByLabel('Per-turn word error rates');
  await expect(perTurnWer).toContainText('Target → tester ASR · 17%');
  await expect(perTurnWer).toContainText('S 0 D 1 I 0');
  await expect(perTurnWer.locator('.wer-turn-card')).toHaveCount(2);
  await expect(perTurnWer.getByText('LLM source')).toHaveCount(2);
  await expect(perTurnWer.getByText('ASR transcript')).toHaveCount(2);
  await page.getByRole('button', { name: /Verified Resolution Rate/ }).click();
  await expect(page.getByLabel('Resolution verification status')).toContainText('Verified');
  await expect(page.getByLabel('Resolution evidence details')).toContainText('91/100');
  await expect(page.getByLabel('Resolution evidence details')).toContainText('Complete');
  await expect(page.getByLabel('Two-agent conversation timeline')).toBeVisible();
  await expect(page.getByLabel('Conversation turn sequence')).toContainText('I can help you with that.');
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.');
  await expect(page.getByLabel('Transcript')).toContainText('I can help you with that.');
  await expect(page.getByLabel('Transcript')).not.toContainText('I can help with that.');
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

  await page.getByLabel('Conversation', { exact: true }).selectOption('exec-demo123-not-evaluated-1');
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

test('runs list refreshes active executions until their terminal status is visible', async ({ page }) => {
  let requests = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });
  await page.route('**/api/execution/runs**', async (route) => {
    requests += 1;
    const status = requests === 1 ? 'running' : 'completed';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{
        ...runFixture,
        status,
        conversations: status === 'running' ? [] : runFixture.conversations,
      }]),
    });
  });

  await page.goto('/runs');
  const runLink = page.getByRole('link', { name: /ACC voice fixture agent/ });
  await expect(runLink).toContainText('running');
  await expect(runLink).toContainText('completed', { timeout: 10_000 });
  expect(requests).toBeGreaterThanOrEqual(2);
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

test('text agent analysis hides the voice conversation timeline', async ({ page }) => {
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
  await expect(page.getByLabel('Two-agent conversation timeline')).toHaveCount(0);
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.');
});

test('text latency excludes tester generation and missing timing marks', async ({ page }) => {
  const textRun = {
    ...runFixture,
    execution_run_id: 'exec-text-latency',
    mode: 'text_callable',
    agent_name: 'Text latency agent',
    conversations: [{
      ...runFixture.conversations[0],
      conversation_id: 'exec-text-latency-1',
      execution_run_id: 'exec-text-latency',
      mode: 'text_callable',
      latency_marks: [
        { label: 'exchange 1 target response', latency_ms: 400 },
        { label: 'exchange 2 tester response', latency_ms: 900 },
        { label: 'exchange 2 target response', elapsed_ms: null },
      ],
    }],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });
  await page.route('**/api/execution/runs/exec-text-latency**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(textRun) });
  });

  await page.goto('/runs/exec-text-latency');
  await expect(page.getByRole('button', { name: /Target Response Latency 400ms/ })).toBeVisible();
  await page.getByRole('button', { name: /Target Response Latency 400ms/ }).click();
  await expect(page.getByText(/count 1 · avg 400ms/)).toBeVisible();
  await expect(page.getByText('900ms')).toHaveCount(0);
});

test('voice analysis reports end-to-end target latency and scopes target diagnostics', async ({ page }) => {
  const accurateVoiceRun = {
    ...runFixture,
    execution_run_id: 'exec-voice-timing',
    mode: 'pipecat_webrtc',
    tester_id: 'pipecat_tester',
    executor_id: 'cae_local_audio_loop',
    conversations: [
      {
        ...runFixture.conversations[0],
        conversation_id: 'exec-voice-timing-1',
        execution_run_id: 'exec-voice-timing',
        mode: 'pipecat_webrtc',
        turns: [
          {
            turn_index: 1,
            speaker: 'caller',
            text: 'Please update my billing address.',
            direction: 'tester_to_target',
            frame_metadata: { bytes: 96000, sample_rate: 24000, channels: 1, duration_ms: 2000 },
          },
          {
            turn_index: 2,
            speaker: 'agent',
            text: 'I can help with that.',
            direction: 'target_to_tester',
            frame_metadata: { bytes: 72000, sample_rate: 24000, channels: 1, duration_ms: 1500 },
          },
        ],
        latency_marks: [
          {
            label: 'Missing normalized evidence',
            kind: 'target_first_audio_byte',
            participant: 'target',
            elapsed_ms: null,
          },
          {
            label: 'End-to-end target response · exchange 1',
            kind: 'tester_speech_end_to_first_target_audio_received',
            participant: 'target',
            latency_ms: 640,
            exchange_elapsed_ms: 9120,
            stage_metrics_source: 'built_in_target',
            stage_metrics: {
              asr_finalize_ms: 120,
              llm_ttft_ms: 180,
              llm_total_ms: 610,
              tts_aggregation_delay_ms: 90,
              tts_synthesis_ttfb_ms: 250,
            },
          },
        ],
      },
    ],
  };
  const remoteVoiceRun = {
    ...accurateVoiceRun,
    execution_run_id: 'exec-remote-timing',
    agent_name: 'Remote voice agent',
    provenance: {
      ...accurateVoiceRun.provenance,
      target_kind: 'remote_webrtc_agent',
      live_external_connection: true,
      synthetic_media: false,
    },
    conversations: [
      {
        ...accurateVoiceRun.conversations[0],
        conversation_id: 'exec-remote-timing-1',
        execution_run_id: 'exec-remote-timing',
        latency_marks: [
          {
            label: 'End-to-end target response · exchange 1',
            kind: 'tester_speech_end_to_first_target_audio_received',
            participant: 'target',
            latency_ms: 725,
          },
        ],
      },
    ],
  };
  const legacyVoiceRun = {
    ...accurateVoiceRun,
    execution_run_id: 'exec-legacy-timing',
    conversations: [
      {
        ...accurateVoiceRun.conversations[0],
        conversation_id: 'exec-legacy-timing-1',
        execution_run_id: 'exec-legacy-timing',
        latency_marks: [
          { label: 'Two Pipecat graphs over local duplex frames', latency_ms: 17_534 },
        ],
      },
    ],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });
  await page.route('**/api/execution/runs/**', async (route) => {
    const url = route.request().url();
    const fixture = url.includes('exec-legacy-timing')
      ? legacyVoiceRun
      : url.includes('exec-remote-timing')
        ? remoteVoiceRun
        : accurateVoiceRun;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) });
  });

  await page.goto('/runs/exec-voice-timing');
  await expect(page.getByRole('button', { name: /End-to-end target response latency 640ms/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Tester speech end → first target audio received at tester/ })).toBeVisible();
  await page.getByRole('button', { name: /End-to-end target response latency 640ms/ }).click();
  await expect(page.getByRole('heading', { name: 'End-to-end target response latency' })).toBeVisible();
  await expect(page.getByText('Tester speech end → first target audio received at tester.')).toBeVisible();
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('End-to-end target response');
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('Built-in target diagnostics');
  await expect(page.getByLabel('Per-mark latency bars')).toContainText(
    'Target endpointing + ASR finalization 120ms',
  );
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('LLM TTFT 180ms');
  await expect(page.getByLabel('Per-mark latency bars')).not.toContainText('LLM TTLT');
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('TTS text aggregation 90ms');
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('TTS synthesis TTFB 250ms');
  await expect(page.getByLabel('Per-mark latency bars')).not.toContainText('LLM callback TTFB');
  await expect(page.getByLabel('Conversation turn sequence')).toContainText(/end-to-end target response 640ms/i);

  await page.goto('/runs/exec-remote-timing');
  await page.getByRole('button', { name: /End-to-end target response latency 725ms/ }).click();
  await expect(page.getByLabel('Per-mark latency bars')).not.toContainText('Built-in target diagnostics');
  await expect(page.getByLabel('Per-mark latency bars')).not.toContainText('Target-provided diagnostics');
  await expect(page.getByLabel('Per-mark latency bars')).not.toContainText('ASR finalization');

  await page.goto('/runs/exec-legacy-timing');
  await expect(page.getByRole('button', { name: /End-to-end target response latency n\/a/ })).toBeVisible();
  await page.getByRole('button', { name: /End-to-end target response latency n\/a/ }).click();
  await expect(page.getByText(/legacy marks measured a complete two-agent exchange/)).toBeVisible();
  await expect(page.getByText('17534ms')).toHaveCount(0);
});

test('active voice listening falls back after disconnect grace and completed playback restarts from the beginning', async ({ page }) => {
  let polls = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & {
      __playedVoiceUrls: string[];
      __liveWebrtcPlayCount: number;
    };
    runtime.__playedVoiceUrls = [];
    runtime.__liveWebrtcPlayCount = 0;
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        setTimeout(() => this.dispatchEvent(new Event('ended')), 20);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value() {
        runtime.__liveWebrtcPlayCount += 1;
        return Promise.resolve();
      },
    });
    class TestPeerConnection {
      connectionState = 'new';
      ontrack: ((event: { track: { kind: string }; streams: MediaStream[] }) => void) | null = null;
      onconnectionstatechange: (() => void) | null = null;
      onicecandidate: ((event: { candidate: null }) => void) | null = null;
      addTransceiver() {}
      async createOffer() {
        return { type: 'offer', sdp: 'test-offer' };
      }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.connectionState = 'connected';
        this.ontrack?.({
          track: { kind: 'audio' },
          streams: [new MediaStream()],
        });
        this.onconnectionstatechange?.();
        setTimeout(() => {
          if (this.connectionState !== 'connected') return;
          this.connectionState = 'disconnected';
          this.onconnectionstatechange?.();
        }, 1800);
      }
      close() {
        this.connectionState = 'closed';
      }
    }
    Object.defineProperty(window, 'RTCPeerConnection', { value: TestPeerConnection });
  });

  await page.route('**/api/execution/runs/exec-live-cursor**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'live-webrtc-token',
            expires_at: '2099-07-26T12:00:00Z',
            listen_url: '/api/execution/listeners/live-webrtc-token',
            webrtc_url: '/api/execution/listeners/live-webrtc-token/webrtc',
            webrtc_ice_url: '/api/execution/listeners/live-webrtc-token/webrtc/ice',
            webrtc_stop_url: '/api/execution/listeners/live-webrtc-token/webrtc/stop',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      return;
    }
    polls += 1;
    const hasWebrtcAgentAudio = polls >= 2;
    const hasFallbackCallerAudio = polls >= 3;
    const completed = polls >= 4;
    const liveEvents = [
      {
        sequence: 1,
        kind: 'audio',
        speaker: 'Caller',
        text: 'Please update my billing address.',
        direction: 'tester_to_target',
        media_url: '/api/execution/runs/exec-live-cursor/conversations/exec-live-cursor-1/audio/1?user_id=demo-user',
      },
      ...(hasWebrtcAgentAudio ? [{
        sequence: 2,
        kind: 'audio',
        speaker: 'Agent',
        text: 'I can help with that.',
        direction: 'target_to_tester',
        media_url: '/api/execution/runs/exec-live-cursor/conversations/exec-live-cursor-1/audio/2?user_id=demo-user',
      }] : []),
      ...(hasFallbackCallerAudio ? [{
        sequence: 3,
        kind: 'audio',
        speaker: 'Caller',
        text: 'This turn arrived after WebRTC failed.',
        direction: 'tester_to_target',
        media_url: '/api/execution/runs/exec-live-cursor/conversations/exec-live-cursor-1/audio/3?user_id=demo-user',
      }] : []),
    ];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-live-cursor',
        status: completed ? 'completed' : 'running',
        mode: 'pipecat_webrtc',
        tester_id: 'pipecat_tester',
        executor_id: 'cae_local_audio_loop',
        progress: {
          phase: completed ? 'completed' : 'executing',
          completed_conversations: completed ? 1 : 0,
          total_conversations: 1,
          percent: completed ? 100 : 50,
        },
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-live-cursor-1',
          execution_run_id: 'exec-live-cursor',
          mode: 'pipecat_webrtc',
          status: completed ? 'completed' : 'running',
          live_events: liveEvents,
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/live-webrtc-token**', async (route) => {
    const url = route.request().url();
    if (url.endsWith('/webrtc')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          answer: { type: 'answer', sdp: 'test-answer' },
          status: 'listening',
        }),
      });
      return;
    }
    if (url.endsWith('/webrtc/ice') || url.endsWith('/webrtc/stop')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          run_status: polls >= 4 ? 'completed' : 'running',
        },
        conversations: [],
      }),
    });
  });

  await page.goto('/runs/exec-live-cursor');
  const feedback = page.getByLabel('Live run feedback');
  await expect(feedback.getByRole('button', { name: 'Listen to live WebRTC' })).toBeVisible();
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect(feedback.getByRole('button', { name: 'Stop live WebRTC' })).toBeVisible();
  await expect(feedback.getByText('Listening to the ongoing WebRTC audio stream. Earlier audio is not replayed.')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __liveWebrtcPlayCount: number }
  ).__liveWebrtcPlayCount)).toBe(1);
  await expect(feedback.getByLabel('WebRTC listener status')).toContainText('HTTP fallback');
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/3?')).length)).toBe(1);
  expect(await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.some((url) => url.includes('/audio/1?') || url.includes('/audio/2?')))).toBe(false);

  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible({ timeout: 10_000 });
  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/1?')).length)).toBe(1);
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/2?')).length)).toBe(1);
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/3?')).length)).toBe(2);
});

test('brief WebRTC recovery switches to lossless HTTP fallback', async ({ page }) => {
  let listenerPolls = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & {
      __playedVoiceUrls: string[];
      __blockedRecoveryOnce: boolean;
      __liveWebrtcMutedAtReconnect: boolean;
    };
    runtime.__playedVoiceUrls = [];
    runtime.__blockedRecoveryOnce = false;
    runtime.__liveWebrtcMutedAtReconnect = false;
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        if (this.url.includes('/audio/2?') && !runtime.__blockedRecoveryOnce) {
          runtime.__blockedRecoveryOnce = true;
          return Promise.reject(new Error('Browser blocked recovery playback'));
        }
        setTimeout(() => this.dispatchEvent(new Event('ended')), 10);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    class RecoveringPeerConnection {
      connectionState = 'new';
      ontrack: ((event: { track: { kind: string }; streams: MediaStream[] }) => void) | null = null;
      onconnectionstatechange: (() => void) | null = null;
      onicecandidate = null;
      addTransceiver() {}
      async createOffer() { return { type: 'offer', sdp: 'test-offer' }; }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.connectionState = 'connected';
        this.ontrack?.({ track: { kind: 'audio' }, streams: [new MediaStream()] });
        this.onconnectionstatechange?.();
        setTimeout(() => {
          if (this.connectionState !== 'connected') return;
          this.connectionState = 'disconnected';
          this.onconnectionstatechange?.();
        }, 500);
        setTimeout(() => {
          if (this.connectionState !== 'disconnected') return;
          this.connectionState = 'connected';
          runtime.__liveWebrtcMutedAtReconnect = Boolean(
            document.querySelector<HTMLAudioElement>('audio[aria-label="Receive-only live run audio"]')?.muted,
          );
          this.onconnectionstatechange?.();
        }, 800);
      }
      close() { this.connectionState = 'closed'; }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value() { return Promise.resolve(); },
    });
    Object.defineProperty(window, 'RTCPeerConnection', { value: RecoveringPeerConnection });
  });

  const liveEvents = (includeDisconnectedTurn: boolean) => [
    {
      sequence: 1,
      kind: 'audio',
      speaker: 'Caller',
      text: 'Existing audio.',
      direction: 'tester_to_target',
      media_url: '/api/execution/runs/exec-brief-recovery/conversations/exec-brief-recovery-1/audio/1?user_id=demo-user',
    },
    ...(includeDisconnectedTurn ? [{
      sequence: 2,
      kind: 'audio',
      speaker: 'Agent',
      text: 'Audio captured during a brief disconnect.',
      direction: 'target_to_tester',
      media_url: '/api/execution/runs/exec-brief-recovery/conversations/exec-brief-recovery-1/audio/2?user_id=demo-user',
    }] : []),
  ];

  await page.route('**/api/execution/runs/exec-brief-recovery**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'brief-recovery-token',
            expires_at: '2099-07-26T12:00:00Z',
            listen_url: '/api/execution/listeners/brief-recovery-token',
            webrtc_url: '/api/execution/listeners/brief-recovery-token/webrtc',
            webrtc_stop_url: '/api/execution/listeners/brief-recovery-token/webrtc/stop',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-brief-recovery',
        status: 'running',
        mode: 'pipecat_webrtc',
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-brief-recovery-1',
          execution_run_id: 'exec-brief-recovery',
          status: 'running',
          mode: 'pipecat_webrtc',
          live_events: liveEvents(false),
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/brief-recovery-token**', async (route) => {
    const url = route.request().url();
    if (url.endsWith('/webrtc')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ answer: { type: 'answer', sdp: 'test-answer' }, status: 'listening' }),
      });
      return;
    }
    if (url.endsWith('/webrtc/stop')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return;
    }
    listenerPolls += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          run_status: 'running',
        },
        conversations: [{
          conversation_id: 'exec-brief-recovery-1',
          // The outage turn first appears in the listener snapshot requested
          // after reconnection, before the normal 1.5-second polling tick.
          live_events: liveEvents(listenerPolls >= 2),
        }],
      }),
    });
  });

  await page.goto('/runs/exec-brief-recovery');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect(feedback.getByText('The live WebRTC connection was interrupted. Waiting briefly for it to recover.')).toBeVisible();
  await expect(feedback.getByLabel('WebRTC listener status')).toContainText('HTTP fallback');
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/2?')).length)).toBe(1);
  await expect(feedback.getByRole('button', { name: 'Retry missed live audio' })).toBeVisible();
  expect(await page.evaluate(() => (
    window as Window & { __liveWebrtcMutedAtReconnect: boolean }
  ).__liveWebrtcMutedAtReconnect)).toBe(true);
  await feedback.getByRole('button', { name: 'Retry missed live audio' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/2?')).length)).toBe(2);
  expect(await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.some((url) => url.includes('/audio/1?')))).toBe(false);
});

test('HTTP fallback queues setup audio while listener-token creation is still pending', async ({ page }) => {
  let runPolls = 0;
  let tokenResponded = false;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & { __playedVoiceUrls: string[]; __blockedFallbackOnce: boolean };
    runtime.__playedVoiceUrls = [];
    runtime.__blockedFallbackOnce = false;
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        if (this.url.includes('/audio/2?') && !runtime.__blockedFallbackOnce) {
          runtime.__blockedFallbackOnce = true;
          return Promise.reject(new Error('Browser blocked autoplay'));
        }
        setTimeout(() => this.dispatchEvent(new Event('ended')), 10);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    class PendingPeerConnection {
      connectionState = 'new';
      ontrack = null;
      onconnectionstatechange = null;
      onicecandidate = null;
      addTransceiver() {}
      async createOffer() { return { type: 'offer', sdp: 'test-offer' }; }
      async setLocalDescription() {}
      async setRemoteDescription() {}
      close() { this.connectionState = 'closed'; }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
    Object.defineProperty(window, 'RTCPeerConnection', { value: PendingPeerConnection });
  });

  const audioEvents = (includeNewTurn: boolean) => [
    {
      sequence: 1,
      kind: 'audio',
      speaker: 'Caller',
      text: 'Existing audio.',
      direction: 'tester_to_target',
      media_url: '/api/execution/runs/exec-live-fallback/conversations/exec-live-fallback-1/audio/1?user_id=demo-user',
    },
    ...(includeNewTurn ? [{
      sequence: 2,
      kind: 'audio',
      speaker: 'Agent',
      text: 'Audio captured during setup.',
      direction: 'target_to_tester',
      media_url: '/api/execution/runs/exec-live-fallback/conversations/exec-live-fallback-1/audio/2?user_id=demo-user',
    }] : []),
  ];

  await page.route('**/api/execution/runs/exec-live-fallback**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await new Promise((resolve) => setTimeout(resolve, 4000));
      tokenResponded = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'live-fallback-token',
            expires_at: '2099-07-26T12:00:00Z',
            listen_url: '/api/execution/listeners/live-fallback-token',
            webrtc_url: '/api/execution/listeners/live-fallback-token/webrtc',
            webrtc_stop_url: '/api/execution/listeners/live-fallback-token/webrtc/stop',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      return;
    }
    runPolls += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-live-fallback',
        status: 'running',
        mode: 'pipecat_webrtc',
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-live-fallback-1',
          execution_run_id: 'exec-live-fallback',
          status: 'running',
          mode: 'pipecat_webrtc',
          live_events: audioEvents(runPolls >= 2),
        }],
      }),
    });
  });
  await page.goto('/runs/exec-live-fallback');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect(feedback.getByLabel('WebRTC listener status')).toContainText('HTTP fallback', { timeout: 6000 });
  expect(tokenResponded).toBe(false);
  await expect(feedback.getByRole('button', { name: 'Retry missed live audio' })).toBeVisible();
  await feedback.getByRole('button', { name: 'Retry missed live audio' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/2?')).length)).toBe(2);
  expect(await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.some((url) => url.includes('/audio/1?')))).toBe(false);
});

test('HTTP fallback preserves setup audio identified by the atomic attachment watermark', async ({ page }) => {
  let listenerPolls = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & { __playedVoiceUrls: string[] };
    runtime.__playedVoiceUrls = [];
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        setTimeout(() => this.dispatchEvent(new Event('ended')), 10);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    class SuccessfulPeerConnection {
      connectionState = 'new';
      ontrack = null;
      onconnectionstatechange: (() => void) | null = null;
      onicecandidate = null;
      addTransceiver() {}
      async createOffer() { return { type: 'offer', sdp: 'test-offer' }; }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.connectionState = 'connected';
        this.onconnectionstatechange?.();
      }
      close() { this.connectionState = 'closed'; }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
    Object.defineProperty(window, 'RTCPeerConnection', { value: SuccessfulPeerConnection });
  });

  const audioEvents = (includeSetupTurn: boolean) => [
    {
      sequence: 1,
      kind: 'audio',
      speaker: 'Caller',
      text: 'Existing audio.',
      direction: 'tester_to_target',
      frame_metadata: {
        listener_media_key: 'active-session:1:tester_to_target',
      },
      media_url: '/api/execution/runs/exec-preconnect-failure/conversations/exec-preconnect-failure-1/audio/1?user_id=demo-user',
    },
    ...(includeSetupTurn ? [{
      sequence: 2,
      kind: 'audio',
      speaker: 'Agent',
      text: 'The in-progress setup turn completed.',
      direction: 'target_to_tester',
      frame_metadata: {
        listener_media_key: 'active-session:1:target_to_tester',
      },
      media_url: '/api/execution/runs/exec-preconnect-failure/conversations/exec-preconnect-failure-1/audio/2?user_id=demo-user',
    }] : []),
  ];

  await page.route('**/api/execution/runs/exec-preconnect-failure**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'preconnect-failure-token',
            expires_at: '2099-07-26T12:00:00Z',
            listen_url: '/api/execution/listeners/preconnect-failure-token',
            webrtc_url: '/api/execution/listeners/preconnect-failure-token/webrtc',
            webrtc_stop_url: '/api/execution/listeners/preconnect-failure-token/webrtc/stop',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-preconnect-failure',
        status: 'running',
        mode: 'pipecat_webrtc',
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-preconnect-failure-1',
          execution_run_id: 'exec-preconnect-failure',
          status: 'running',
          mode: 'pipecat_webrtc',
          live_events: audioEvents(false),
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/preconnect-failure-token**', async (route) => {
    const url = route.request().url();
    if (url.endsWith('/webrtc')) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          answer: { type: 'answer', sdp: 'test-answer' },
          status: 'listening',
          pre_attach_audio_event_keys: [
            'exec-preconnect-failure-1:1',
          ],
          pre_attach_listener_media_keys: [
            'active-session:1:tester_to_target',
            'active-session:1:target_to_tester',
          ],
          audio_published_during_attach: false,
        }),
      });
      return;
    }
    if (url.endsWith('/webrtc/stop')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          run_status: 'running',
        },
        conversations: [{
          conversation_id: 'exec-preconnect-failure-1',
          live_events: (() => {
            listenerPolls += 1;
            if (listenerPolls >= 2) return audioEvents(true);
            return [
              ...audioEvents(false),
              {
                sequence: 2,
                kind: 'message',
                speaker: 'Agent',
                text: 'Speech started before the listener attached.',
                direction: 'target_to_tester',
                frame_metadata: {
                  media_event: 'first_audible_byte',
                  listener_media_key: 'active-session:1:target_to_tester',
                },
              },
            ];
          })(),
        }],
      }),
    });
  });

  await page.goto('/runs/exec-preconnect-failure');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect(feedback.getByLabel('WebRTC listener status')).toContainText('HTTP fallback');
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/2?')).length)).toBe(1);
  expect(await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.some((url) => url.includes('/audio/1?')))).toBe(false);
});

test('blocked WebRTC playback falls back without consuming unheard audio', async ({ page }) => {
  let listenerPolls = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & { __playedVoiceUrls: string[] };
    runtime.__playedVoiceUrls = [];
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        setTimeout(() => this.dispatchEvent(new Event('ended')), 10);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value() { return Promise.reject(new Error('autoplay blocked')); },
    });
    class BlockedPlaybackPeerConnection {
      connectionState = 'new';
      ontrack: ((event: { track: { kind: string }; streams: MediaStream[] }) => void) | null = null;
      onconnectionstatechange: (() => void) | null = null;
      onicecandidate = null;
      addTransceiver() {}
      async createOffer() { return { type: 'offer', sdp: 'test-offer' }; }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.connectionState = 'connected';
        this.ontrack?.({ track: { kind: 'audio' }, streams: [new MediaStream()] });
        this.onconnectionstatechange?.();
      }
      close() { this.connectionState = 'closed'; }
    }
    Object.defineProperty(window, 'RTCPeerConnection', { value: BlockedPlaybackPeerConnection });
  });

  const audioEvents = (includeUnheardTurn: boolean) => [{
    sequence: 1,
    kind: 'audio',
    speaker: 'Caller',
    text: 'Audio from before listening started.',
    direction: 'tester_to_target',
    media_url: '/api/execution/runs/exec-blocked-playback/conversations/exec-blocked-playback-1/audio/1?user_id=demo-user',
  }, ...(includeUnheardTurn ? [{
    sequence: 2,
    kind: 'audio',
    speaker: 'Agent',
    text: 'Audio that the blocked element never played.',
    direction: 'target_to_tester',
    media_url: '/api/execution/runs/exec-blocked-playback/conversations/exec-blocked-playback-1/audio/2?user_id=demo-user',
  }] : [])];

  await page.route('**/api/execution/runs/exec-blocked-playback**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ listener: {
          token: 'blocked-playback-token',
          expires_at: '2099-07-26T12:00:00Z',
          listen_url: '/api/execution/listeners/blocked-playback-token',
          webrtc_url: '/api/execution/listeners/blocked-playback-token/webrtc',
          webrtc_stop_url: '/api/execution/listeners/blocked-playback-token/webrtc/stop',
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          media_transport: 'webrtc',
        } }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-blocked-playback',
        status: 'running',
        mode: 'pipecat_webrtc',
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-blocked-playback-1',
          execution_run_id: 'exec-blocked-playback',
          status: 'running',
          mode: 'pipecat_webrtc',
          live_events: audioEvents(false),
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/blocked-playback-token**', async (route) => {
    const url = route.request().url();
    if (url.endsWith('/webrtc')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          answer: { type: 'answer', sdp: 'test-answer' },
          status: 'listening',
          pre_attach_audio_event_keys: ['exec-blocked-playback-1:1'],
          audio_published_during_attach: false,
        }),
      });
      return;
    }
    if (url.endsWith('/webrtc/stop')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return;
    }
    listenerPolls += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: { read_only: true, run_status: 'running' },
        conversations: [{
          conversation_id: 'exec-blocked-playback-1',
          live_events: audioEvents(listenerPolls >= 2),
        }],
      }),
    });
  });

  await page.goto('/runs/exec-blocked-playback');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect(feedback.getByLabel('WebRTC listener status')).toContainText('HTTP fallback');
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/2?')).length)).toBe(1);
  expect(await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.some((url) => url.includes('/audio/1?')))).toBe(false);
});

test('owner live WebRTC uses a different token from the shared listener link', async ({ page }) => {
  let listenerTokenRequests = 0;
  let ownerWebrtcRequests = 0;
  let sharedWebrtcRequests = 0;
  let abandonedWebrtcRequests = 0;
  let abandonedTokenDelivered = false;
  let overlapAWebrtcRequests = 0;
  let overlapAResponseDelivered = false;
  let overlapBWebrtcRequests = 0;
  let overlapBStopRequests = 0;
  let releaseAbandonedToken!: () => void;
  let releaseOverlapA!: () => void;
  const abandonedTokenGate = new Promise<void>((resolve) => {
    releaseAbandonedToken = resolve;
  });
  const overlapAGate = new Promise<void>((resolve) => {
    releaseOverlapA = resolve;
  });
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    class TestPeerConnection {
      connectionState = 'new';
      ontrack: ((event: { track: { kind: string }; streams: MediaStream[] }) => void) | null = null;
      onconnectionstatechange: (() => void) | null = null;
      onicecandidate: ((event: { candidate: null }) => void) | null = null;
      addTransceiver() {}
      async createOffer() {
        return { type: 'offer', sdp: 'test-offer' };
      }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.connectionState = 'connected';
        this.onconnectionstatechange?.();
      }
      close() {
        this.connectionState = 'closed';
      }
    }
    Object.defineProperty(window, 'RTCPeerConnection', { value: TestPeerConnection });
  });

  await page.route('**/api/execution/runs/exec-expired-listener**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      listenerTokenRequests += 1;
      const token = listenerTokenRequests === 1
        ? 'shared-listener-token'
        : listenerTokenRequests === 2
          ? 'owner-listener-token'
          : listenerTokenRequests === 3
            ? 'abandoned-listener-token'
            : listenerTokenRequests === 4
              ? 'overlap-a-listener-token'
              : 'overlap-b-listener-token';
      if (token === 'abandoned-listener-token') {
        await abandonedTokenGate;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token,
            expires_at: '2099-07-26T12:00:00Z',
            listen_url: `/api/execution/listeners/${token}`,
            webrtc_url: `/api/execution/listeners/${token}/webrtc`,
            webrtc_ice_url: `/api/execution/listeners/${token}/webrtc/ice`,
            webrtc_stop_url: `/api/execution/listeners/${token}/webrtc/stop`,
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      if (token === 'abandoned-listener-token') {
        abandonedTokenDelivered = true;
      }
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-expired-listener',
        status: 'running',
        mode: 'pipecat_webrtc',
        progress: {
          phase: 'executing',
          completed_conversations: 0,
          total_conversations: 1,
          percent: 0,
          active_conversation_id: 'exec-expired-listener-1',
        },
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-expired-listener-1',
          execution_run_id: 'exec-expired-listener',
          mode: 'pipecat_webrtc',
          status: 'running',
          live_events: [],
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/**', async (route) => {
    const url = route.request().url();
    if (url.endsWith('/webrtc')) {
      if (url.includes('owner-listener-token')) ownerWebrtcRequests += 1;
      if (url.includes('shared-listener-token')) sharedWebrtcRequests += 1;
      if (url.includes('abandoned-listener-token')) abandonedWebrtcRequests += 1;
      if (url.includes('overlap-a-listener-token')) {
        overlapAWebrtcRequests += 1;
        await overlapAGate;
        await route.fulfill({
          status: 502,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Attempt A failed after attempt B connected.' }),
        });
        overlapAResponseDelivered = true;
        return;
      }
      if (url.includes('overlap-b-listener-token')) overlapBWebrtcRequests += 1;
      await route.fulfill({
        status: url.includes('shared-listener-token') ? 409 : 200,
        contentType: 'application/json',
        body: url.includes('shared-listener-token')
          ? JSON.stringify({ detail: 'This listener is already attached.' })
          : JSON.stringify({
              answer: { type: 'answer', sdp: 'test-answer' },
              status: 'listening',
            }),
      });
      return;
    }
    if (url.endsWith('/webrtc/ice') || url.endsWith('/webrtc/stop')) {
      if (
        url.endsWith('/webrtc/stop')
        && url.includes('overlap-b-listener-token')
      ) overlapBStopRequests += 1;
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          run_status: 'running',
        },
        conversations: [],
      }),
    });
  });

  await page.goto('/runs/exec-expired-listener');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Create live listener link' }).click();
  await expect(feedback.getByLabel('Run listener link')).toContainText('shared-listener-token');
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect.poll(() => listenerTokenRequests).toBe(2);
  await expect.poll(() => ownerWebrtcRequests).toBe(1);
  expect(sharedWebrtcRequests).toBe(0);
  await expect(feedback.getByRole('button', { name: 'Stop live WebRTC' })).toBeVisible();
  await expect(feedback.getByLabel('Run listener link')).toContainText('shared-listener-token');

  await feedback.getByRole('button', { name: 'Stop live WebRTC' }).click();
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect.poll(() => listenerTokenRequests).toBe(3);
  await feedback.getByRole('button', { name: 'Stop live WebRTC' }).click();
  releaseAbandonedToken();
  await expect.poll(() => abandonedTokenDelivered).toBe(true);
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  expect(abandonedWebrtcRequests).toBe(0);
  await expect(feedback.getByRole('button', { name: 'Listen to live WebRTC' })).toBeVisible();

  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect.poll(() => overlapAWebrtcRequests).toBe(1);
  await feedback.getByRole('button', { name: 'Stop live WebRTC' }).click();
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect.poll(() => overlapBWebrtcRequests).toBe(1);
  await expect(feedback.getByLabel('WebRTC listener status')).toContainText('listening');
  releaseOverlapA();
  await expect.poll(() => overlapAResponseDelivered).toBe(true);
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  expect(overlapBStopRequests).toBe(0);
  await expect(feedback.getByLabel('WebRTC listener status')).toContainText('listening');
  await expect(feedback.getByRole('button', { name: 'Stop live WebRTC' })).toBeVisible();
});

test('recorded conversation playback pauses and resumes from the current position', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    type ReplayAudio = EventTarget & { currentTime: number };
    const runtime = window as Window & {
      __replayPlayCalls: Array<{ url: string; currentTime: number }>;
      __replayPauseCalls: number;
      __currentReplayAudio: ReplayAudio | null;
      __finishReplayAudio: () => void;
    };
    runtime.__replayPlayCalls = [];
    runtime.__replayPauseCalls = 0;
    runtime.__currentReplayAudio = null;
    runtime.__finishReplayAudio = () => {
      runtime.__currentReplayAudio?.dispatchEvent(new Event('ended'));
    };
    class TestAudio extends EventTarget {
      currentTime = 0;

      constructor(private readonly url: string) {
        super();
      }

      play() {
        runtime.__currentReplayAudio = this;
        runtime.__replayPlayCalls.push({ url: this.url, currentTime: this.currentTime });
        return Promise.resolve();
      }

      pause() {
        runtime.__replayPauseCalls += 1;
      }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
  });

  await page.route('**/api/execution/runs/exec-pause-replay**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-pause-replay',
        status: 'completed',
        mode: 'pipecat_webrtc',
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-pause-replay-1',
          execution_run_id: 'exec-pause-replay',
          status: 'completed',
          mode: 'pipecat_webrtc',
          live_events: [1, 2].map((sequence) => ({
            sequence,
            kind: 'audio',
            speaker: sequence === 1 ? 'Caller' : 'Agent',
            text: sequence === 1 ? 'First recorded turn.' : 'Second recorded turn.',
            direction: sequence === 1 ? 'tester_to_target' : 'target_to_tester',
            media_url: `/api/execution/runs/exec-pause-replay/conversations/exec-pause-replay-1/audio/${sequence}?user_id=demo-user`,
          })),
        }],
      }),
    });
  });

  await page.goto('/runs/exec-pause-replay');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __replayPlayCalls: unknown[] }
  ).__replayPlayCalls.length)).toBe(1);
  await expect(feedback.getByRole('button', { name: 'Pause playback' })).toBeVisible();
  await page.evaluate(() => {
    const runtime = window as Window & { __currentReplayAudio: { currentTime: number } | null };
    if (runtime.__currentReplayAudio) runtime.__currentReplayAudio.currentTime = 3.5;
  });

  await feedback.getByRole('button', { name: 'Pause playback' }).click();
  await expect(feedback.getByRole('button', { name: 'Resume playback' })).toBeVisible();
  await expect(feedback.getByText('Playback paused. Resume to continue from this point.')).toBeVisible();
  await page.waitForTimeout(100);
  expect(await page.evaluate(() => (
    window as Window & { __replayPlayCalls: unknown[] }
  ).__replayPlayCalls.length)).toBe(1);

  await feedback.getByRole('button', { name: 'Resume playback' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __replayPlayCalls: unknown[] }
  ).__replayPlayCalls.length)).toBe(2);
  const resumedCall = await page.evaluate(() => (
    window as Window & {
      __replayPlayCalls: Array<{ url: string; currentTime: number }>;
    }
  ).__replayPlayCalls[1]);
  expect(resumedCall.url).toContain('/audio/1?');
  expect(resumedCall.currentTime).toBe(3.5);

  await page.evaluate(() => (
    window as Window & { __finishReplayAudio: () => void }
  ).__finishReplayAudio());
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __replayPlayCalls: unknown[] }
  ).__replayPlayCalls.length)).toBe(3);
  const secondTurnCall = await page.evaluate(() => (
    window as Window & {
      __replayPlayCalls: Array<{ url: string; currentTime: number }>;
    }
  ).__replayPlayCalls[2]);
  expect(secondTurnCall.url).toContain('/audio/2?');
  await page.evaluate(() => (
    window as Window & { __finishReplayAudio: () => void }
  ).__finishReplayAudio());
  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible();
  await expect(feedback.getByText('Playback finished. Play again to restart from the beginning.')).toBeVisible();

  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __replayPlayCalls: unknown[] }
  ).__replayPlayCalls.length)).toBe(4);
  await feedback.getByRole('button', { name: 'Pause playback' }).click();
  await feedback.getByRole('button', { name: 'Stop playback', exact: true }).click();
  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible();
  await expect(feedback.getByText(
    'Playback stopped. Play again to restart from the beginning.',
  )).toBeVisible();
});

test('completed replay switches from listener-token audio to owner-scoped audio', async ({ page }) => {
  let runPolls = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & { __playedVoiceUrls: string[] };
    runtime.__playedVoiceUrls = [];
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        setTimeout(() => this.dispatchEvent(new Event('ended')), 10);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
  });

  const audioEvents = (scope: 'owner' | 'listener') => [1, 2].map((sequence) => ({
    sequence,
    kind: 'audio',
    speaker: sequence === 1 ? 'Caller' : 'Agent',
    text: sequence === 1 ? 'Please update my address.' : 'I can help.',
    direction: sequence === 1 ? 'tester_to_target' : 'target_to_tester',
    media_url: scope === 'owner'
      ? `/api/execution/runs/exec-listener-replay/conversations/conversation-1/audio/${sequence}?user_id=demo-user`
      : `/api/execution/listeners/expiring-token/conversations/conversation-1/audio/${sequence}`,
  }));

  await page.route('**/api/execution/runs/exec-listener-replay**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'expiring-token',
            expires_at: '2099-07-26T12:00:00Z',
            listen_url: '/listeners/expiring-token',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      return;
    }
    runPolls += 1;
    const completed = runPolls >= 3;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-listener-replay',
        status: completed ? 'completed' : 'running',
        mode: 'pipecat_webrtc',
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'conversation-1',
          execution_run_id: 'exec-listener-replay',
          mode: 'pipecat_webrtc',
          status: completed ? 'completed' : 'running',
          live_events: audioEvents('owner'),
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/expiring-token', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          run_status: runPolls >= 3 ? 'completed' : 'running',
        },
        conversations: [{
          conversation_id: 'conversation-1',
          live_events: audioEvents('listener'),
        }],
      }),
    });
  });

  await page.goto('/runs/exec-listener-replay');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Create live listener link' }).click();
  await expect(feedback.getByText('Read-only live listener')).toBeVisible();
  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible({
    timeout: 10_000,
  });
  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.length)).toBe(2);
  const playedUrls = await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls);
  expect(playedUrls.every((url) => url.includes('/api/execution/runs/exec-listener-replay/'))).toBe(true);
  expect(playedUrls.some((url) => url.includes('/api/execution/listeners/'))).toBe(false);
});
