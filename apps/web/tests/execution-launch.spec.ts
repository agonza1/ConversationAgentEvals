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
          optional_scenarios: [
            {
              id: 'cancellation-rescue',
              title: 'Cancellation Rescue',
              sample_transcript: 'Caller: I want to cancel.',
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
          status: 'connected',
          provider: 'openai_codex',
        }),
      });
      return;
    }
    if (url.includes('/providers/openai/models')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          models: [{ id: 'gpt-5.4', label: 'gpt-5.4' }],
          default_model: 'gpt-5.4',
          source: 'openai',
        }),
      });
      return;
    }
    if (url.includes('/runs') || url.includes('/audit-events') || url.includes('/regression-summary') || url.includes('/export')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.route('**/api/agents**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agents: [
          {
            id: 'mock-text-agent',
            name: 'Mock text agent',
            channel: 'text',
            target: 'mock_agent',
            description: 'Sample agent profile',
            metadata: { model_name: 'gpt-5.4' },
          },
          {
            id: 'generalist-voice-agent',
            name: 'Built-in generalist voice agent',
            channel: 'voice',
            target: 'builtin_sample_voice',
            description: 'Reference voice target',
            metadata: { model_name: 'registry-seed-must-not-override-env' },
          },
        ],
      }),
    });
  });

  let posted: Record<string, unknown> | null = null;
  let voicePosted: Record<string, unknown> | null = null;
  let postAttempts = 0;
  await page.route('**/api/benchmarks/suite-runs**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.route('**/api/execution/runs**', async (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      if (body.agent_id === 'generalist-voice-agent') {
        voicePosted = body;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            execution_run_id: 'exec-ui-voice',
            status: 'queued',
            mode: 'pipecat_webrtc',
            suite_id: 'call-center-voice-ai',
            scenario_ids: ['cancellation-rescue'],
            user_id: 'demo-user',
            project_id: 'call-center-demo',
            progress: { phase: 'queued', completed_conversations: 0, total_conversations: 1, percent: 0 },
            conversations: [],
            created_at: '2026-07-18T00:00:00Z',
            updated_at: '2026-07-18T00:00:00Z',
          }),
        });
        return;
      }
      postAttempts += 1;
      if (postAttempts === 1) {
        await route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: [
              { loc: ['body', 'tester_id'], msg: 'Extra inputs are not permitted' },
              { loc: ['body', 'executor_id'], msg: 'Extra inputs are not permitted' },
            ],
          }),
        });
        return;
      }
      posted = body;
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
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(posted ? [{
        execution_run_id: 'exec-ui-demo',
        status: 'queued',
        mode: 'text_callable',
        suite_id: 'call-center-voice-ai',
        scenario_ids: ['cancellation-rescue'],
        user_id: 'demo-user-exec',
        project_id: 'call-center-demo',
        agent_id: 'mock-text-agent',
        agent_name: 'Mock text agent',
        tester_id: 'scenario_simulator',
        executor_id: 'local_async_runner',
        progress: { phase: 'queued', completed_conversations: 0, total_conversations: 1, percent: 0 },
        conversations: [],
        created_at: '2026-07-15T00:00:00Z',
        updated_at: '2026-07-15T00:00:00Z',
      }] : []),
    });
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
                live_events: [
                  { sequence: 1, kind: 'message', speaker: 'User', text: 'Please update my address.' },
                ],
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
                live_events: [
                  { sequence: 1, kind: 'message', speaker: 'User', text: 'Please update my address.' },
                  { sequence: 2, kind: 'message', speaker: 'Agent', text: 'Updated.' },
                ],
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

  await page.goto('/runs?api_base=http%3A%2F%2Fapi.example.test&suite_id=call-center-voice-ai&scenario_id=cancellation-rescue');
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByLabel('Launch agent run').getByRole('button', { name: 'Run sample evaluation' })).toBeEnabled({
    timeout: 30_000,
  });

  const launch = page.getByLabel('Launch agent run');
  await expect(launch.getByRole('heading', { name: 'Configure this run' })).toBeVisible();
  await expect(launch.getByLabel('Default scenario for launch')).toContainText('Cancellation Rescue');
  await expect(launch.getByLabel('Execution tester')).toHaveValue('scenario_simulator');
  await expect(launch.getByLabel('Execution runner')).toContainText(/local async runner/i);
  await expect(launch.getByText('System under test')).toBeVisible();
  await expect(launch.getByText('Advanced')).toBeVisible();
  await expect(launch.getByLabel('Execution scenario scope')).not.toBeVisible();
  await launch.getByText('Advanced').click();
  await expect(launch.getByLabel('Execution scenario scope')).toBeVisible();
  await launch.getByRole('button', { name: 'Run sample evaluation' }).click();
  await expect(launch).toContainText('tester_id: Extra inputs are not permitted; executor_id: Extra inputs are not permitted');
  await expect(launch).not.toContainText('[object Object]');
  await launch.getByRole('button', { name: 'Run sample evaluation' }).click();
  await expect(launch.getByText('exec-ui-demo', { exact: true })).toBeVisible();
  await launch.getByRole('button', { name: 'Show live exchange' }).click();
  await expect(launch.getByLabel('Observed live exchange')).toContainText('Please update my address.', { timeout: 8000 });
  await expect(launch.getByLabel('Observed live exchange')).toContainText('Updated.', { timeout: 8000 });
  await expect(launch.getByRole('link', { name: 'Open analysis' })).toHaveAttribute(
    'href',
    '/runs/exec-ui-demo?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect.poll(() => posted).not.toBeNull();
  expect(posted).toMatchObject({
    mode: 'text_callable',
    text_callable: 'mock_agent',
    agent_id: 'mock-text-agent',
    model_name: 'gpt-5.4',
    tester_id: 'scenario_simulator',
    executor_id: 'local_async_runner',
    scenario_ids: ['cancellation-rescue'],
  });
  await expect(launch.getByLabel('Execution conversations')).toContainText('Billing Address Change');
  await expect(launch.getByLabel('Execution conversations')).toContainText(/pass/i, { timeout: 8000 });
  await launch.getByLabel('Execution agent target').selectOption('generalist-voice-agent');
  await launch.getByRole('button', { name: 'Run generalist voice evaluation' }).click();
  await expect.poll(() => voicePosted).not.toBeNull();
  expect(voicePosted).toMatchObject({
    mode: 'pipecat_webrtc',
    agent_id: 'generalist-voice-agent',
    tester_id: 'pipecat_tester',
    executor_id: 'cae_local_audio_loop',
  });
  expect(voicePosted).not.toHaveProperty('model_name');
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Mock text agent/ })).toContainText('queued');
});

test('saved ACC evidence is not offered as a Run Agent target', async ({ page }) => {
  let posted: Record<string, unknown> | null = null;
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

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
          auth: { enabled: false, mode: 'placeholder', providers: [], project_id: null, api_key_configured: false },
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
        body: JSON.stringify({ status: 'disconnected', message: 'Connect OpenAI.' }),
      });
      return;
    }
    if (url.includes('/providers/openai/models')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          models: [{ id: 'gpt-5.4', label: 'gpt-5.4' }],
          default_model: 'gpt-5.4',
          source: 'fallback',
        }),
      });
      return;
    }
    if (url.includes('/runs') || url.includes('/audit-events') || url.includes('/regression-summary') || url.includes('/export')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.route('**/api/agents**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agents: [
          {
            id: 'offline-text-fixture',
            name: 'Offline text fixture',
            channel: 'text',
            target: 'offline_acc_fixture',
            description: 'Text offline ACC fixture agent',
            metadata: { model_name: 'gpt-5.4' },
          },
        ],
      }),
    });
  });

  await page.route('**/api/execution/runs', async (route) => {
    if (route.request().method() === 'POST') {
      posted = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'exec-offline-fixture',
          status: 'completed',
          mode: 'text_callable',
          suite_id: 'call-center-voice-ai',
          scenario_ids: ['cancellation-rescue'],
          user_id: 'demo-user-offline',
          project_id: 'call-center-demo',
          agent_id: 'offline-text-fixture',
          model_name: 'gpt-5.4',
          progress: {
            phase: 'completed',
            completed_conversations: 1,
            total_conversations: 1,
            percent: 100,
          },
          conversations: [],
          created_at: '2026-07-16T00:00:00Z',
          updated_at: '2026-07-16T00:00:01Z',
          completed_at: '2026-07-16T00:00:01Z',
        }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user-offline');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });

  await page.goto('/runs?agent_id=offline-text-fixture');
  const launch = page.getByLabel('Launch agent run');
  await expect(launch.getByLabel('Execution agent target')).toHaveValue('');
  await expect(launch.getByLabel('Execution agent target').locator('option')).toHaveCount(1);
  await expect(launch.getByLabel('Execution agent target').locator('option')).toHaveText('No targets');
  await expect(launch.getByRole('button', { name: 'Run evaluation' })).toBeDisabled();
  expect(posted).toBeNull();
  expect(pageErrors).toEqual([]);
});
