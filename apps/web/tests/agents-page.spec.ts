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
            name: 'Built-in sample voice call',
            channel: 'voice',
            target: 'builtin_sample_voice',
            description: 'Offline ACC voice fixture path.',
            metadata: { model_name: 'voice-fixture', prompt_version: 'seed' },
          },
          {
            id: 'live-openai-agent',
            name: 'Live OpenAI agent',
            channel: 'text',
            target: 'openai_codex',
            description: 'Live OpenAI text target.',
            metadata: { model_name: 'gpt-5.4', prompt_version: 'seed' },
          },
        ],
      }),
    });
  });

  await page.route('**/api/product/**', async (route) => {
    const url = route.request().url();
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

  await page.route('**/api/execution/acc-connection**', async (route) => {
    const tested = route.request().method() === 'POST';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        connected: tested,
        status: tested ? 'connected' : 'requires_acc_connection',
        label: tested ? 'ACC connected' : 'Requires ACC connection',
        message: tested
          ? 'Connection verified against the official ACC media readiness API.'
          : 'Enter the ACC URL and test the connection.',
        base_url: tested ? 'http://127.0.0.1:8026' : null,
        destinations: {
          browser_webrtc_agent: {
            creatable: tested,
            executor_kind: 'acc_browser_webrtc',
            label: tested ? 'Ready through ACC' : 'Connect ACC to enable',
          },
          sip_agent: {
            creatable: tested,
            executor_kind: 'acc_sip',
            label: tested ? 'Ready through ACC' : 'Connect ACC to enable',
          },
          phone_agent: {
            creatable: false,
            executor_kind: 'acc_phone',
            label: tested ? 'PSTN trunk routing is not ready.' : 'Connect ACC to check PSTN readiness',
          },
        },
      }),
    });
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
  await expect(mockCard.locator('.agents-badge-builtin')).toHaveText('Built-in sample');
  await expect(mockCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=mock-text-agent',
  );

  const voiceCard = page.getByRole('article').filter({ hasText: 'Built-in sample voice call' });
  await expect(voiceCard.locator('.agents-badge-channel').first()).toHaveText('Voice');
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
  await page.getByRole('article').filter({ hasText: 'Built-in sample voice call' }).getByRole('link', { name: 'Try it Out' }).click();
  await expect(page).toHaveURL(/\/runs\/exec-try-it-out/, { timeout: 20000 });
  expect(launches).toHaveLength(1);
  expect(launches[0]?.agent_id).toBe('acc-voice-fixture-agent');
  expect(launches[0]?.mode).toBe('pipecat_webrtc');
  expect(launches[0]?.executor_kind).toBe('cae_local_audio_loop');
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
  await page.route('**/api/agents**', async (route) => {
    if (route.request().method() === 'POST') {
      created = true;
      const body = route.request().postDataJSON() as { name: string };
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

  await page.getByPlaceholder('Support bot endpoint').fill('Playwright agent created');
  await page.getByRole('button', { name: 'Create target' }).click();

  await expect(page.getByRole('article').filter({ hasText: 'Playwright agent created' })).toBeVisible();
});

test('agent target form connects ACC and enables only ready destinations', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/targets');
  await page.locator('.agents-page-header').getByRole('button', { name: 'Add agent target' }).click();

  const channel = page.getByLabel('Target channel');
  await expect(page.getByRole('radiogroup', { name: 'Target destination' })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Built-in sample agent/i })).toBeChecked();

  await channel.selectOption('voice');
  await expect(page.getByRole('radio', { name: /Built-in sample agent/i })).toBeChecked();
  await expect(page.getByRole('radio', { name: /SIP agent/i })).toBeDisabled();
  await expect(page.getByRole('radio', { name: /Phone agent/i })).toBeDisabled();
  await expect(page.getByRole('radio', { name: /Browser\/WebRTC agent/i })).toBeDisabled();
  await expect(page.getByRole('dialog').getByText('Built-in sample voice call')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Test connection' })).toBeVisible();

  await page.getByRole('button', { name: 'Test connection' }).click();
  await expect(page.getByText(/Connection verified/)).toBeVisible();
  await expect(page.getByRole('radio', { name: /SIP agent/i })).toBeEnabled();
  await expect(page.getByRole('radio', { name: /Browser\/WebRTC agent/i })).toBeEnabled();
  await expect(page.getByRole('radio', { name: /Phone agent/i })).toBeDisabled();
  await expect(page.getByText('PSTN trunk routing is not ready.')).toBeVisible();

  await page.getByRole('radio', { name: /SIP agent/i }).check();
  await expect(page.getByLabel('SIP URI')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create target' })).toBeEnabled();
});
