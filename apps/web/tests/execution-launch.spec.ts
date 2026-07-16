import { expect, test } from '@playwright/test';

test('launch evaluation streams conversations into the live list', async ({ page }) => {
  let polled = 0;

  await page.route('**/api/benchmarks/suites**', async (route) => {
    if (route.request().url().includes('/contract-manifest')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return;
    }
    if (route.request().url().includes('/scenarios')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          scenarios: [{ id: 'billing-address-change', title: 'Billing Address Change', suite_id: 'call-center-voice-ai' }],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 'call-center-voice-ai',
          title: 'Call Center Voice AI',
          scenarios: [
            {
              id: 'billing-address-change',
              title: 'Billing Address Change',
              sample_transcript: 'Caller: hi',
              sample_action_trace: [],
              sample_final_state: {},
            },
          ],
        },
      ]),
    });
  });

  await page.route('**/api/product/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/config')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pricing: [],
          usage_rules: [],
          auth: {
            enabled: false,
            mode: 'placeholder',
            providers: [],
            project_id: null,
            api_key_configured: false,
          },
          voice_status: 'gated',
          llm_judge_status: 'gated',
        }),
      });
      return;
    }
    if (url.includes('/providers/openai/status')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'disconnected',
          message: 'Connect OpenAI (Codex OAuth) to unlock the local LLM judge.',
        }),
      });
      return;
    }
    if (url.includes('/providers/openai/models')) {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Connect OpenAI (Codex OAuth) to load models.' }),
      });
      return;
    }
    if (url.includes('/runs') || url.includes('/audit-events') || url.includes('/regression-summary') || url.includes('/export')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  let postedModel: string | null = null;
  await page.route('**/api/benchmarks/suite-runs**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.route('**/api/execution/runs', async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as { model_name?: string };
      postedModel = body.model_name ?? null;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'exec-ui-demo',
          status: 'queued',
          mode: 'text_callable',
          suite_id: 'call-center-voice-ai',
          scenario_ids: ['billing-address-change'],
          user_id: 'demo-user',
          project_id: 'call-center-demo',
          model_name: body.model_name ?? 'gpt-5.4',
          progress: {
            phase: 'queued',
            completed_conversations: 0,
            total_conversations: 1,
            percent: 0,
          },
          conversations: [],
          created_at: '2026-07-15T00:00:00Z',
          updated_at: '2026-07-15T00:00:00Z',
        }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.route('**/api/execution/runs/exec-ui-demo**', async (route) => {
    polled += 1;
    const body =
      polled < 2
        ? {
            execution_run_id: 'exec-ui-demo',
            status: 'running',
            mode: 'text_callable',
            suite_id: 'call-center-voice-ai',
            scenario_ids: ['billing-address-change'],
            user_id: 'demo-user',
            project_id: 'call-center-demo',
            progress: {
              phase: 'executing',
              completed_conversations: 0,
              total_conversations: 1,
              percent: 0,
              active_conversation_id: 'exec-ui-demo-billing-address-change-1',
            },
            conversations: [
              {
                conversation_id: 'exec-ui-demo-billing-address-change-1',
                execution_run_id: 'exec-ui-demo',
                suite_id: 'call-center-voice-ai',
                scenario_id: 'billing-address-change',
                scenario_title: 'Billing Address Change',
                mode: 'text_callable',
                status: 'running',
                iteration: 1,
                turns: [],
              },
            ],
            created_at: '2026-07-15T00:00:00Z',
            updated_at: '2026-07-15T00:00:01Z',
          }
        : {
            execution_run_id: 'exec-ui-demo',
            status: 'completed',
            mode: 'text_callable',
            suite_id: 'call-center-voice-ai',
            scenario_ids: ['billing-address-change'],
            user_id: 'demo-user',
            project_id: 'call-center-demo',
            progress: {
              phase: 'completed',
              completed_conversations: 1,
              total_conversations: 1,
              percent: 100,
            },
            conversations: [
              {
                conversation_id: 'exec-ui-demo-billing-address-change-1',
                execution_run_id: 'exec-ui-demo',
                suite_id: 'call-center-voice-ai',
                scenario_id: 'billing-address-change',
                scenario_title: 'Billing Address Change',
                mode: 'text_callable',
                status: 'completed',
                iteration: 1,
                turns: [{ turn_index: 1, speaker: 'caller', text: 'Please update my address.' }],
                transcript: 'Caller: Please update my address.\nAgent: Updated.',
                verdict: 'pass',
                score: 91,
              },
            ],
            inference_set_path: 'artifacts/execution-runs/exec-ui-demo/inference_set.jsonl',
            created_at: '2026-07-15T00:00:00Z',
            updated_at: '2026-07-15T00:00:02Z',
            completed_at: '2026-07-15T00:00:02Z',
          };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user-exec');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
    window.localStorage.setItem('conversation-evals-demo-plan', 'free');
  });

  await page.goto('/runs');
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByLabel('Launch agent run').getByRole('button', { name: 'Launch agent run' })).toBeEnabled({
    timeout: 30_000,
  });

  const launch = page.getByLabel('Launch agent run');
  await expect(launch.getByLabel('Execution model')).toHaveValue('gpt-5.4');
  await expect(launch.getByText('Connect OpenAI to load models.')).toBeVisible();
  await expect(launch.getByText('Advanced')).toBeVisible();
  await expect(launch.getByLabel('Execution target mode')).toHaveCount(0);
  await launch.getByText('Advanced').click();
  await expect(launch.getByLabel('Execution target mode')).toBeVisible();
  await launch.getByRole('button', { name: 'Launch agent run' }).click();
  await expect(launch.getByText('exec-ui-demo', { exact: true })).toBeVisible();
  await expect.poll(() => postedModel).toBe('gpt-5.4');
  await expect(launch.getByLabel('Execution conversations')).toContainText('Billing Address Change');
  await expect(launch.getByLabel('Execution conversations')).toContainText(/pass/i, { timeout: 8000 });
});
