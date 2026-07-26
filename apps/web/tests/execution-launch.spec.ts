import { expect, test } from '@playwright/test';

test('launch evaluation streams conversations into the live list', async ({ page }) => {
  let polled = 0;
  let voicePreflightReady = true;

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
            id: 'generalist-text-agent',
            name: 'Built-in generalist text agent',
            channel: 'text',
            target: 'openai_codex',
            description: 'Live OpenAI text target',
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

  await page.route('**/api/execution/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        reference_voice: {
          ready: voicePreflightReady,
          llm_mode: 'real',
          dependencies: [
            { id: 'openai', label: 'OpenAI', ready: voicePreflightReady, detail: voicePreflightReady ? 'Ready for both agents.' : 'Set OPENAI_API_KEY or connect OAuth.' },
            { id: 'shared_token', label: 'Shared token', ready: true, detail: 'Ready.' },
            { id: 'pipecat', label: 'Pipecat', ready: true, detail: 'Reachable.' },
            { id: 'rtc_asr', label: 'rtc-asr', ready: true, detail: 'Reachable.' },
            { id: 'kokoro', label: 'Kokoro', ready: true, detail: 'Reachable.' },
          ],
        },
      }),
    });
  });

  let posted: Record<string, unknown> | null = null;
  const textPostAttempts: Record<string, unknown>[] = [];
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
            scenario_ids: ['billing-address-change'],
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
      textPostAttempts.push(body);
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

  await page.route('**/api/execution/runs/exec-ui-voice**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        execution_run_id: 'exec-ui-voice',
        status: 'running',
        mode: 'pipecat_webrtc',
        suite_id: 'call-center-voice-ai',
        scenario_ids: ['billing-address-change'],
        user_id: 'demo-user',
        project_id: 'call-center-demo',
        progress: {
          phase: 'executing',
          completed_conversations: 0,
          total_conversations: 1,
          percent: 0,
          active_conversation_id: 'exec-ui-voice-billing-address-change-1',
        },
        conversations: [],
        created_at: '2026-07-18T00:00:00Z',
        updated_at: '2026-07-18T00:00:01Z',
      }),
    });
  });

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user-exec');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
    window.localStorage.setItem('conversation-evals-demo-plan', 'free');
  });

  await page.goto('/runs?api_base=http%3A%2F%2Fapi.example.test&suite_id=call-center-voice-ai&scenario_id=billing-address-change');
  await expect(page.getByLabel('Launch agent run')).toBeVisible();
  await expect(page.getByLabel('Launch agent run').getByRole('button', { name: 'Run evaluation' })).toBeEnabled({
    timeout: 30_000,
  });

  const launch = page.getByLabel('Launch agent run');
  await expect(launch.getByRole('heading', { name: 'Configure this run' })).toBeVisible();
  await expect(launch.getByLabel('Execution agent target')).toHaveValue('generalist-text-agent');
  await expect(launch.getByLabel('Maximum exchanges')).toHaveValue('3');
  await expect(launch.getByLabel('Maximum exchanges')).toBeEnabled();
  await expect(launch.getByLabel('Selected run scope')).toContainText('Billing Address Change');
  await expect(launch.getByLabel('Execution tester')).toContainText('Scenario user (AI)');
  await expect(launch.getByLabel('Execution runner')).toContainText(/local async runner/i);
  await expect(launch.locator('.run-config-step-heading strong')).toHaveText([
    'Tester',
    'Agent target',
    'Execution',
  ]);
  await expect(launch.getByText('System under test')).toBeVisible();
  await expect(launch.getByText('Advanced', { exact: true })).toHaveCount(0);
  await expect(launch.getByLabel('Execution scenario scope')).toHaveCount(0);
  await launch.getByLabel('Execution agent target').selectOption('mock-text-agent');
  await expect(launch.getByLabel('Maximum exchanges')).toBeVisible();
  await expect(launch.getByLabel('Maximum exchanges')).toBeDisabled();
  await expect(launch).toContainText('fixed sample targets replay one exchange');
  const runScope = launch.getByRole('group', { name: 'Run scope' });
  await expect(runScope.getByRole('button', { name: /Single scenario/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(runScope.getByRole('button', { name: /Entire suite/ })).toHaveAttribute('aria-pressed', 'false');
  await launch.getByRole('button', { name: 'Run evaluation' }).click();
  await expect(launch).toContainText('tester_id: Extra inputs are not permitted; executor_id: Extra inputs are not permitted');
  await expect(launch).not.toContainText('[object Object]');
  expect(textPostAttempts[0]).toMatchObject({ scenario_ids: ['billing-address-change'] });
  await runScope.getByRole('button', { name: /Entire suite/ }).click();
  await expect(runScope.getByRole('button', { name: /Entire suite/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(launch.getByLabel('Selected run scope')).toContainText('Call Center Voice AI');
  await expect(launch.getByLabel('Selected run scope')).toContainText('2 scenarios');
  await expect(launch).toContainText('2 scenarios × 1 iteration · 2 conversations');
  await launch.getByRole('button', { name: 'Run evaluation' }).click();
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
    scenario_ids: ['billing-address-change', 'cancellation-rescue'],
    max_exchanges: 3,
  });
  await launch.getByLabel('Execution agent target').selectOption('generalist-text-agent');
  await expect(launch.getByLabel('Maximum exchanges')).toHaveValue('3');
  await expect(launch.getByLabel('Maximum exchanges')).toBeEnabled();
  await launch.getByLabel('Maximum exchanges').fill('4');
  await launch.getByRole('button', { name: 'Run evaluation' }).click();
  await expect.poll(() => textPostAttempts.length).toBe(3);
  expect(textPostAttempts.at(-1)).toMatchObject({
    mode: 'text_callable',
    text_callable: 'openai_codex',
    agent_id: 'generalist-text-agent',
    max_exchanges: 4,
  });
  await expect(launch.getByLabel('Execution conversations')).toContainText('Billing Address Change');
  await expect(launch.getByLabel('Execution conversations')).toContainText(/pass/i, { timeout: 8000 });
  const recentRun = page
    .getByRole('region', { name: 'Recent runs' })
    .locator('a[href="/runs/exec-ui-demo?api_base=http%3A%2F%2Fapi.example.test"]');
  await expect(recentRun).toHaveCount(1);
  await expect(recentRun).toContainText('completed', { timeout: 8000 });
  await page.goto('/runs?api_base=http%3A%2F%2Fapi.example.test&suite_id=call-center-voice-ai&scenario_id=billing-address-change');
  await expect(launch.getByLabel('Selected run scope')).toContainText('Billing Address Change');
  await launch.getByLabel('Execution agent target').selectOption('generalist-voice-agent');
  await expect(launch.getByLabel('Execution tester')).toContainText('Scenario user (AI)');
  await expect(launch).toContainText('adapts to the target\'s responses');
  await launch.getByLabel('Maximum exchanges').fill('5');
  await expect(launch).toContainText('up to 5 exchanges each');
  await launch.getByRole('button', { name: 'Run evaluation' }).click();
  await expect.poll(() => voicePosted).not.toBeNull();
  expect(voicePosted).toMatchObject({
    mode: 'pipecat_webrtc',
    agent_id: 'generalist-voice-agent',
    tester_id: 'pipecat_tester',
    executor_id: 'cae_local_audio_loop',
    suite_id: 'call-center-voice-ai',
    scenario_ids: ['billing-address-change'],
    max_exchanges: 5,
  });
  expect(voicePosted).not.toHaveProperty('model_name');
  await expect(launch.getByLabel('Run listener link')).toContainText('Available only while this run is active.');
  await expect(launch.getByRole('button', { name: 'Listen to live WebRTC' })).toBeEnabled();
  await expect(launch.getByRole('button', { name: 'Create live listener link' })).toBeEnabled();
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Mock text agent/ })).toContainText('queued');

  voicePreflightReady = false;
  await page.goto('/runs?api_base=http%3A%2F%2Fapi.example.test&suite_id=call-center-voice-ai&scenario_id=cancellation-rescue');
  const blockedLaunch = page.getByRole('region', { name: 'Launch agent run' });
  await blockedLaunch.getByLabel('Execution agent target').selectOption('generalist-voice-agent');
  await expect(blockedLaunch.getByLabel('Run Agent voice preflight blocked')).toContainText('Set OPENAI_API_KEY');
  await expect(blockedLaunch.getByRole('button', { name: 'Run evaluation' })).toBeDisabled();
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
