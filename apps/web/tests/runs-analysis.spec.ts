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
  await expect(page.getByText('ACC voice fixture agent')).toBeVisible();

  await page.getByRole('link', { name: /ACC voice fixture agent/ }).click();
  await expect(page.getByRole('heading', { name: 'ACC voice fixture agent' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Interruption Detection/ }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /Latency/ }).first()).toBeVisible();
  await expect(page.getByLabel('Stub dual-track waveform')).toBeVisible();
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.');
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
