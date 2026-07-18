import { expect, test } from '@playwright/test';

type MockRunnerOptions = {
  agentsDelayMs?: number;
  onExecutionLaunch?: (request: Record<string, unknown>) => void;
};

async function mockRunnerApis(page: import('@playwright/test').Page, options: MockRunnerOptions = {}) {
  await page.route('**/api/benchmarks/suites**', async (route) => {
    if (route.request().url().includes('/contract-manifest')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
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
            {
              id: 'cancellation-rescue',
              title: 'Cancellation Rescue',
              sample_transcript: 'Caller: cancel',
              sample_action_trace: [],
              sample_final_state: {},
            },
          ],
        },
      ]),
    });
  });

  await page.route('**/api/agents**', async (route) => {
    if (options.agentsDelayMs) {
      await new Promise((resolve) => setTimeout(resolve, options.agentsDelayMs));
    }
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
            description: 'Deterministic text mock.',
            metadata: { model_name: 'mock', prompt_version: 'seed' },
          },
          {
            id: 'acc-voice-fixture-agent',
            name: 'Built-in generalist voice agent',
            channel: 'voice',
            target: 'builtin_sample_voice',
            description: 'CAE local audio loop.',
            metadata: { model_name: 'builtin-sample-voice', prompt_version: 'seed' },
          },
          {
            id: 'live-openai-agent',
            name: 'Live OpenAI agent',
            channel: 'text',
            target: 'openai_codex',
            description: 'Live OpenAI text target.',
            metadata: { model_name: 'gpt-5.4', prompt_version: 'seed' },
          },
          {
            id: 'staging-http-agent',
            name: 'Staging HTTP agent',
            channel: 'text',
            target: 'http_endpoint',
            environment: 'staging',
            connection: { endpoint_url: 'https://support.example.test/chat', response_path: 'response' },
            description: 'Black-box HTTP target.',
            metadata: {},
          },
        ],
      }),
    });
  });

  await page.route('**/api/product/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/providers/openai/status')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'connected', provider: 'openai_codex' }),
      });
      return;
    }
    if (url.includes('/providers/openai/models')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ models: [{ id: 'gpt-5.4' }], default_model: 'gpt-5.4' }),
      });
      return;
    }
    if (url.includes('/runs') || url.includes('/audit-events')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return;
    }
    if (url.includes('/regression-summary')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: 'null' });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.route('**/api/benchmarks/suite-runs**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.route('**/api/execution/runs**', async (route) => {
    const url = route.request().url();
    if (route.request().method() === 'POST') {
      options.onExecutionLaunch?.(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'exec-try-it-out',
          status: 'queued',
          mode: 'pipecat_webrtc',
          agent_id: 'acc-voice-fixture-agent',
          conversations: [],
        }),
      });
      return;
    }
    if (url.includes('/exec-try-it-out')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'exec-try-it-out',
          status: 'completed',
          mode: 'pipecat_webrtc',
          agent_id: 'acc-voice-fixture-agent',
          conversations: [],
        }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
  });
}

test('targets page shows agent target cards and try-it-out deep links', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/targets');

  await expect(page.getByRole('heading', { name: 'Targets', exact: true })).toBeVisible();
  await expect(page.locator('.agents-page-header').getByRole('button', { name: 'Add agent target' })).toBeVisible();

  const mockCard = page.getByRole('article').filter({ hasText: 'Mock text agent' });
  await expect(mockCard.locator('.agents-badge-channel').first()).toHaveText('Text');
  await expect(mockCard.getByText('Built-in testing target')).toBeVisible();
  await expect(mockCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=mock-text-agent',
  );

  const voiceCard = page.getByRole('article').filter({ hasText: 'Built-in generalist voice agent' });
  await expect(voiceCard.locator('.agents-badge-channel').first()).toHaveText('Voice');
  await expect(voiceCard).toContainText('Current-run local pipeline · no saved evidence');
  await expect(voiceCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=acc-voice-fixture-agent',
  );
});

test('targets try-it-out links preserve the api base override', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/targets?api_base=https%3A%2F%2Fapi.example.test');

  const mockCard = page.getByRole('article').filter({ hasText: 'Mock text agent' });
  await expect(mockCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=mock-text-agent&api_base=https%3A%2F%2Fapi.example.test',
  );
});

test('legacy agents redirect preserves the api base override', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/agents?api_base=https%3A%2F%2Fapi.example.test');

  await expect(page).toHaveURL(/\/targets\?api_base=https%3A%2F%2Fapi\.example\.test$/);
});

test('targets try-it-out auto-launches and opens run analysis', async ({ page }) => {
  const launches: Record<string, unknown>[] = [];
  await mockRunnerApis(page, {
    onExecutionLaunch: (request) => launches.push(request),
  });
  await page.goto('/targets');
  await page.getByRole('article').filter({ hasText: 'Built-in generalist voice agent' }).getByRole('link', { name: 'Try it Out' }).click();
  await expect(page).toHaveURL(/\/runs\/exec-try-it-out/, { timeout: 20000 });
  expect(launches).toHaveLength(1);
  expect(launches[0]?.agent_id).toBe('acc-voice-fixture-agent');
  expect(launches[0]?.mode).toBe('pipecat_webrtc');
  expect(launches[0]?.tester_id).toBe('pipecat_tester');
  expect(launches[0]?.executor_id).toBe('cae_local_audio_loop');
  expect(launches[0]?.audio_transport).toBe('pipecat_small_webrtc');
});

test('OpenAI agent try-it-out launches its configured live target', async ({ page }) => {
  const launches: Record<string, unknown>[] = [];
  await mockRunnerApis(page, {
    onExecutionLaunch: (request) => launches.push(request),
  });
  await page.goto('/targets');
  await page.getByRole('article').filter({ hasText: 'Live OpenAI agent' }).getByRole('link', { name: 'Try it Out' }).click();
  await expect(page).toHaveURL(/\/runs\/exec-try-it-out/, { timeout: 20000 });
  expect(launches).toHaveLength(1);
  expect(launches[0]).toMatchObject({
    agent_id: 'live-openai-agent',
    mode: 'text_callable',
    text_callable: 'openai_codex',
    tester_id: 'scenario_simulator',
    executor_id: 'local_async_runner',
  });
});

test('HTTP agent try-it-out uses its configured adapter and explicit tester', async ({ page }) => {
  const launches: Record<string, unknown>[] = [];
  await mockRunnerApis(page, {
    onExecutionLaunch: (request) => launches.push(request),
  });
  await page.goto('/targets');
  const card = page.getByRole('article').filter({ hasText: 'Staging HTTP agent' });
  await expect(card).toContainText('HTTP JSON endpoint (live)');
  await expect(card).toContainText('Black-box response');
  await card.getByRole('link', { name: 'Try it Out' }).click();
  await expect(page).toHaveURL(/\/runs\/exec-try-it-out/, { timeout: 20000 });
  expect(launches).toHaveLength(1);
  expect(launches[0]).toMatchObject({
    agent_id: 'staging-http-agent',
    mode: 'text_callable',
    text_callable: 'http_endpoint',
    tester_id: 'scenario_simulator',
    executor_id: 'local_async_runner',
  });
});

test('homepage demo waits for its default agent before auto-launching', async ({ page }) => {
  const launches: Record<string, unknown>[] = [];
  await mockRunnerApis(page, {
    agentsDelayMs: 250,
    onExecutionLaunch: (request) => launches.push(request),
  });

  await page.goto('/runs?launch=demo');
  await expect(page).toHaveURL(/\/runs\/exec-try-it-out/, { timeout: 20000 });

  expect(launches).toHaveLength(1);
  expect(launches[0]?.agent_id).toBe('mock-text-agent');
});

test('demo analysis redirect preserves the api base override', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/runs?launch=demo&api_base=https%3A%2F%2Fapi.example.test');
  await expect(page).toHaveURL(/\/runs\/exec-try-it-out/, { timeout: 20000 });

  expect(new URL(page.url()).searchParams.get('api_base')).toBe('https://api.example.test');
});

test('add agent target modal creates a registry entry', async ({ page }) => {
  let created = false;
  let createdBody: Record<string, unknown> | null = null;
  await page.route('**/api/agents**', async (route) => {
    if (route.request().method() === 'POST') {
      created = true;
      const body = route.request().postDataJSON() as { name: string };
      createdBody = body as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'playwright-agent',
          name: body.name,
          channel: 'text',
          target: 'mock_agent',
          description: '',
          metadata: {},
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agents: created
          ? [
              {
                id: 'playwright-agent',
                name: 'Playwright agent created',
                channel: 'text',
                target: 'mock_agent',
                description: '',
                metadata: {},
              },
            ]
          : [],
      }),
    });
  });

  await page.goto('/targets');
  await page.locator('.agents-page-header').getByRole('button', { name: 'Add agent target' }).click();
  await expect(page.getByRole('heading', { name: 'Add agent target' })).toBeVisible();

  await page.getByPlaceholder('Billing support — staging').fill('Playwright agent created');
  await page.getByLabel('Endpoint URL').fill('https://staging.example.test/chat');
  await page.getByRole('button', { name: 'Create target' }).click();

  await expect(page.getByRole('article').filter({ hasText: 'Playwright agent created' })).toBeVisible();
  expect(createdBody).toMatchObject({
    target: 'http_endpoint',
    environment: 'staging',
    connection: {
      endpoint_url: 'https://staging.example.test/chat',
      response_path: 'response',
    },
  });
});

test('agent target form only offers connections compatible with its selected channel', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/targets');
  await page.locator('.agents-page-header').getByRole('button', { name: 'Add agent target' }).click();

  const channel = page.getByLabel('Target channel');
  const target = page.getByLabel('Target connection');
  await expect(target.getByRole('option')).toHaveCount(3);
  await expect(target.locator('option[value="offline_acc_fixture"]')).toHaveCount(0);
  await expect(target).toHaveValue('http_endpoint');
  await expect(page.getByLabel('Endpoint URL')).toBeVisible();

  await channel.selectOption('voice');
  await expect(target).toHaveValue('browser_webrtc_agent');
  await expect(target.getByRole('option')).toHaveCount(4);
  await expect(target.locator('option[value="sip_agent"]')).toHaveText(/ACC SIP URI \(coming soon\)/);
  await expect(target.locator('option[value="phone_agent"]')).toHaveText(/ACC phone number \(coming soon\)/);
  await expect(target.locator('option[value="browser_webrtc_agent"]')).toHaveText(/ACC browser WebRTC \(coming soon\)/);
  await expect(target.locator('option[value="builtin_sample_voice"]')).toHaveText('Built-in generalist voice agent');
  await expect(page.getByText('CAE ↔ ACC live adapter coming soon')).toBeVisible();
  await expect(page.getByLabel('ACC base URL')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Coming soon' })).toBeDisabled();

  await target.selectOption('sip_agent');
  await expect(page.getByLabel('SIP URI')).toBeVisible();
  await expect(page.getByLabel('Phone number')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Coming soon' })).toBeDisabled();

  await target.selectOption('phone_agent');
  await expect(page.getByLabel('Phone number')).toBeVisible();
  await expect(page.getByLabel('SIP URI')).toHaveCount(0);

  await target.selectOption('builtin_sample_voice');
  await expect(page.getByText('Built-in generalist voice evaluation')).toBeVisible();
  await expect(page.getByText(/Transcript, score, state, timing, media, and vCon come only from this run/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create target' })).toBeEnabled();
});
