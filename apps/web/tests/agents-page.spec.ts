import { expect, test } from '@playwright/test';

async function mockRunnerApis(page: import('@playwright/test').Page) {
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
            name: 'ACC voice fixture agent',
            channel: 'voice',
            target: 'voice_fixture',
            description: 'Offline ACC voice fixture path.',
            metadata: { model_name: 'voice-fixture', prompt_version: 'seed' },
          },
        ],
      }),
    });
  });

  await page.route('**/api/product/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.route('**/api/benchmarks/suite-runs**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.route('**/api/execution/runs**', async (route) => {
    const url = route.request().url();
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'exec-try-it-out',
          status: 'queued',
          mode: 'voice_fixture',
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
          mode: 'voice_fixture',
          agent_id: 'acc-voice-fixture-agent',
          conversations: [],
        }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: [] }) });
  });
}

test('agents page shows persona cards and try-it-out deep links', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/agents');

  await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
  await expect(page.locator('.agents-page-header').getByRole('button', { name: 'Add a new Agent' })).toBeVisible();

  const mockCard = page.getByRole('article').filter({ hasText: 'Mock text agent' });
  await expect(mockCard.locator('.agents-badge-channel')).toHaveText('Text chat');
  await expect(mockCard.getByText('Built-in')).toBeVisible();
  await expect(mockCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=mock-text-agent',
  );

  const voiceCard = page.getByRole('article').filter({ hasText: 'ACC voice fixture agent' });
  await expect(voiceCard.locator('.agents-badge-channel')).toHaveText('Inbound Voice');
  await expect(voiceCard.getByRole('link', { name: 'Try it Out' })).toHaveAttribute(
    'href',
    '/runs?launch=demo&agent_id=acc-voice-fixture-agent',
  );
});

test('agents try-it-out auto-launches and opens run analysis', async ({ page }) => {
  await mockRunnerApis(page);
  await page.goto('/agents');
  await page.getByRole('article').filter({ hasText: 'ACC voice fixture agent' }).getByRole('link', { name: 'Try it Out' }).click();
  await expect(page).toHaveURL(/\/runs\/exec-try-it-out/, { timeout: 20000 });
});

test('add agent modal creates a registry entry', async ({ page }) => {
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

  await page.goto('/agents');
  await page.locator('.agents-page-header').getByRole('button', { name: 'Add a new Agent' }).click();
  await expect(page.getByRole('heading', { name: 'Add a new Agent' })).toBeVisible();

  await page.getByPlaceholder('Support bot v2').fill('Playwright agent created');
  await page.getByRole('button', { name: 'Create agent' }).click();

  await expect(page.getByRole('article').filter({ hasText: 'Playwright agent created' })).toBeVisible();
});
