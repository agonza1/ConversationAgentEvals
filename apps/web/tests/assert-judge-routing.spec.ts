import { expect, test } from '@playwright/test';

test('run analysis judge button uses the upstream ASSERT path', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  const conversationId = 'exec-assert-ui-refund-policy-boundary-1';
  const run = {
    execution_run_id: 'exec-assert-ui',
    status: 'needs_review',
    mode: 'text_callable',
    suite_id: 'call-center-voice-ai',
    scenario_ids: ['refund-policy-boundary'],
    user_id: 'demo-user',
    project_id: 'call-center-demo',
    agent_id: 'external-support-agent',
    agent_name: 'External support agent',
    tester_id: 'scenario_simulator',
    executor_id: 'local_async_runner',
    provenance: {
      target_id: 'external-support-agent',
      target_kind: 'http_endpoint',
      target_channel: 'text',
      tester_id: 'scenario_simulator',
      executor_id: 'local_async_runner',
      evidence_source: 'current_run',
      live_external_connection: true,
      saved_evidence: false,
      synthetic_media: false,
      honesty_label: 'Live external target · black-box conversation evidence',
    },
    execution_snapshot: {
      agent: { target: 'http_endpoint', environment: 'staging' },
    },
    progress: {
      phase: 'completed',
      completed_conversations: 1,
      total_conversations: 1,
      percent: 100,
    },
    conversations: [
      {
        conversation_id: conversationId,
        execution_run_id: 'exec-assert-ui',
        suite_id: 'call-center-voice-ai',
        scenario_id: 'refund-policy-boundary',
        scenario_title: 'Refund Policy Boundary',
        mode: 'text_callable',
        status: 'completed',
        iteration: 1,
        turns: [
          { turn_index: 1, speaker: 'user', text: 'Please refund the charge now.' },
          { turn_index: 2, speaker: 'agent', text: 'I issued the refund.' },
        ],
        transcript: 'User: Please refund the charge now.\nAgent: I issued the refund.',
        action_trace: [],
        final_state: {
          complete: false,
          outcome: 'conversation_only_evidence_recorded',
          termination_reason: 'max_exchanges',
          runtime_provenance: { live_tool_execution: false },
        },
        evaluation_findings: {
          missing_actions: ['open refund review case'],
          hard_check_failures: [
            {
              category: 'unsupported_claim',
              summary: 'The agent claimed a refund without execution evidence.',
            },
          ],
        },
        metrics_summary: {
          verdict: 'needs_review',
          score: 45,
          turn_count: 2,
          latency: {
            count: 1,
            avg_ms: 310,
            median_ms: 310,
            p90_ms: 310,
            min_ms: 310,
            max_ms: 310,
            outlier_count: 0,
          },
          interruption_count: 0,
          call_resolution_success: 0,
        },
        latency_marks: [{ label: 'target response', latency_ms: 310 }],
        verdict: 'needs_review',
        score: 45,
      },
    ],
    created_at: '2026-08-11T18:00:00Z',
    updated_at: '2026-08-11T18:01:00Z',
    completed_at: '2026-08-11T18:01:00Z',
  };

  let assertRequest: Record<string, unknown> | null = null;
  let legacyJudgeCalls = 0;

  await page.route('**/api/execution/runs/exec-assert-ui**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(run),
    });
  });

  await page.route(
    `**/api/assert/runs/exec-assert-ui/conversations/${conversationId}/judge`,
    async (route) => {
      assertRequest = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ready',
          required_plan: 'starter',
          credits: 10,
          engine: 'assert',
          message: 'Upstream ASSERT semantic judgment completed. CAE deterministic evidence remains authoritative.',
          evidence_citations: [
            'unsupported_operational_claim: The refund claim has no matching tool evidence.',
          ],
          judge_result: {
            agrees: true,
            rationale: 'The transcript contains an unsupported refund claim.',
            next_action: 'Review the refund claim and preserve the deterministic evidence gap.',
            proposed_evaluation: {
              verdict: 'needs_review',
              summary: 'The conversation requires review because refund execution is unverified.',
              corrected_findings: [],
              remaining_gaps: ['No refund tool result or final-state receipt was recorded.'],
            },
          },
          provider: 'assert-ai',
          model: 'openai/gpt-4.1-mini',
          latency_ms: 640,
          review_id: 'judge-review-assert-ui',
          assert_version: '0.1.0',
          assert_result: {
            judge_status: 'ok',
            verdict: {
              dimensions: {
                policy_violation: true,
                unsupported_operational_claim: true,
              },
            },
          },
          artifacts: {
            scores: 'artifacts/execution-runs/exec-assert-ui/assert/scores.jsonl',
          },
          spend_control: {
            provider: 'assert-ai',
            provider_configured: true,
            estimated_credits: 10,
          },
        }),
      });
    },
  );

  await page.route('**/api/product/judge', async (route) => {
    legacyJudgeCalls += 1;
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'legacy product judge must not be called' }),
    });
  });

  await page.goto('/runs/exec-assert-ui');
  await expect(page.getByRole('heading', { name: 'External support agent' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Review the deterministic verdict' })).toBeVisible();

  await page.getByRole('button', { name: 'Review with LLM judge' }).click();

  const result = page.getByLabel('LLM judge result');
  await expect(result).toContainText('Upstream ASSERT semantic judgment completed');
  await expect(result).toContainText('Provider: assert-ai');
  await expect(result).toContainText('openai/gpt-4.1-mini');
  await expect(result).toContainText('The transcript contains an unsupported refund claim.');
  await expect(result).toContainText('No refund tool result or final-state receipt was recorded.');

  expect(assertRequest).toEqual({ user_id: 'demo-user' });
  expect(legacyJudgeCalls).toBe(0);
});
