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
  await expect(page.getByText(/evaluation score is not a resolution percentage/)).toBeVisible();
});

test('run detail can request the existing LLM judge with selected conversation evidence', async ({ page }) => {
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
    project_id: 'call-center-demo',
    transcript: conversation.transcript,
    report: {
      run_id: 'exec-demo123',
      suite_id: 'call-center-voice-ai',
      scenario_id: 'cancellation-rescue',
      verdict: 'needs_review',
      overall_score: 60,
      missing_actions: ['policy_hold_entered'],
      rubric_checks: [{ name: 'retention_policy', status: 'failed' }],
      hard_check_failures: [{ category: 'policy', summary: 'Required policy hold was not entered.' }],
      scenario_contract: {
        required_actions: [{ id: 'policy_hold_entered', description: 'Enter the required policy hold.' }],
      },
      evidence_citations: expect.arrayContaining([
        {
          source: 'action_trace',
          text: JSON.stringify(conversation.action_trace[0]),
        },
        {
          source: 'final_state',
          text: JSON.stringify(conversation.final_state),
        },
      ]),
      action_trace: conversation.action_trace,
      final_state: conversation.final_state,
      require_evaluator_findings: true,
    },
  });
  const judgeReportPayload = (
    judgeRequest as { report?: Record<string, unknown> } | null
  )?.report;
  expect(judgeReportPayload).not.toHaveProperty('final_state_score');

  await page.getByRole('button', { name: 'What the judge saw' }).click();
  await expect(page.getByText('Deterministic verdict: needs_review')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run LLM review again' })).toBeVisible();
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

  let judgeReport: Record<string, unknown> | null = null;
  await page.route('**/api/product/judge', async (route) => {
    const payload = route.request().postDataJSON() as { report?: Record<string, unknown> };
    judgeReport = payload.report || null;
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
  expect(judgeReport).toMatchObject({
    failure_categories: expect.arrayContaining(['Execution error: simulated provider disconnect']),
    evidence_citations: expect.arrayContaining([
      { source: 'execution_error', text: 'simulated provider disconnect' },
    ]),
  });

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
