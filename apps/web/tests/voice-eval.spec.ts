import { expect, test } from '@playwright/test';

test('voice eval page launches and shows conversation evidence', async ({ page }) => {
  let polled = 0;

  await page.route('**/api/execution/runs**', async (route) => {
    const url = route.request().url();
    if (route.request().method() === 'POST' && url.endsWith('/api/execution/runs')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'voice-run-1',
          status: 'running',
          mode: 'voice_fixture',
          suite_id: 'call-center-voice-ai',
          scenario_ids: ['cancellation-rescue'],
          user_id: 'voice-user',
          project_id: 'conversation-agent-evals',
          progress: {
            phase: 'running',
            completed_conversations: 0,
            total_conversations: 1,
            percent: 0,
          },
          conversations: [],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
      return;
    }

    if (url.includes('/api/execution/runs/voice-run-1')) {
      polled += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'voice-run-1',
          status: polled > 1 ? 'completed' : 'running',
          mode: 'voice_fixture',
          suite_id: 'call-center-voice-ai',
          scenario_ids: ['cancellation-rescue'],
          user_id: 'voice-user',
          project_id: 'conversation-agent-evals',
          progress: {
            phase: polled > 1 ? 'completed' : 'running',
            completed_conversations: polled > 1 ? 1 : 0,
            total_conversations: 1,
            percent: polled > 1 ? 100 : 40,
          },
          conversations:
            polled > 1
              ? [
                  {
                    conversation_id: 'voice-run-1-cancellation-rescue-1',
                    execution_run_id: 'voice-run-1',
                    suite_id: 'call-center-voice-ai',
                    scenario_id: 'cancellation-rescue',
                    scenario_title: 'Cancellation rescue',
                    mode: 'voice_fixture',
                    status: 'completed',
                    iteration: 1,
                    turns: [{ turn_index: 1, speaker: 'caller', text: 'I want to cancel.' }],
                    transcript: 'Caller: I want to cancel.\nAgent: I can help with that.',
                    recording: { recording_url: 'artifact://voice-run-1.wav', mime_type: 'audio/wav' },
                    vcon_export: {
                      vcon: '0.0.1',
                      source_format: 'pipecat_execution',
                      dialog: [{ speaker: 'Caller', body: 'I want to cancel.' }],
                    },
                    vcon_export_summary: {
                      dialog_turns: 1,
                      source_format: 'pipecat_execution',
                      recording_attached: true,
                    },
                    verdict: 'pass',
                    score: 88,
                  },
                ]
              : [],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.goto('/voice');
  await expect(page.getByRole('heading', { name: 'Voice eval' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Voice evaluation' })).toBeVisible();
  await page.getByRole('button', { name: 'Run' }).click();
  await expect(page.getByText('voice-run-1')).toBeVisible();
  await expect(page.getByText('Cancellation rescue')).toBeVisible({ timeout: 10000 });
  await expect(page.getByText(/vCon|recording/i)).toBeVisible();
});
