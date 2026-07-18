import { expect, test } from '@playwright/test';

test('voice eval page launches and shows conversation evidence', async ({ page }) => {
  let polled = 0;

  await page.route('**/api/agents', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agents: [
          {
            id: 'generalist-voice-agent',
            name: 'Built-in generalist voice agent',
            channel: 'voice',
            target: 'builtin_sample_voice',
            description: 'Built-in target for cancellation-rescue voice evaluation.',
            metadata: { model_name: 'gpt-5.4-mini', prompt_version: 'generalist-v1' },
          },
          {
            id: 'acc-offline-fixture-agent',
            name: 'ACC offline text fixture',
            channel: 'text',
            target: 'offline_acc_fixture',
            description: 'Text-only fixture that must not be offered through a voice transport.',
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
        surface: 'execution',
        audio: {
          transports: [
            { id: 'none', label: 'No audio transport', available: true },
            {
              id: 'pipecat_small_webrtc',
              label: 'Local Pipecat small WebRTC hooks',
              available: true,
              default_execution_mode: 'pipecat_webrtc',
              notes: ['In-process hooks; no live browser peer.'],
            },
          ],
        },
      }),
    });
  });

  await page.route('**/api/execution/runs**', async (route) => {
    const url = route.request().url();
    if (route.request().method() === 'POST' && url.endsWith('/api/execution/runs')) {
      expect(JSON.parse(route.request().postData() ?? '{}')).toMatchObject({
        suite_id: 'call-center-voice-ai',
        scenario_ids: ['cancellation-rescue'],
        mode: 'pipecat_webrtc',
        agent_id: 'generalist-voice-agent',
        model_name: 'gpt-5.4-mini',
        tester_id: 'pipecat_tester',
        executor_id: 'cae_local_audio_loop',
        audio_transport: 'pipecat_small_webrtc',
        evaluate: true,
      });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          execution_run_id: 'voice-run-1',
          status: 'running',
          mode: 'pipecat_webrtc',
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
          mode: 'pipecat_webrtc',
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
                    mode: 'pipecat_webrtc',
                    status: 'completed',
                    iteration: 1,
                    turns: [{ turn_index: 1, speaker: 'caller', text: 'I want to cancel.' }],
                    transcript: 'Caller: I want to cancel.\nAgent: I can help with that.',
                    recording: { recording_url: 'artifact://voice-run-1.wav', mime_type: 'audio/wav' },
                    vcon_export_summary: {
                      dialog_turns: 1,
                      source_format: 'pipecat_execution',
                      recording_attached: true,
                    },
                    audio_session: {
                      frames_sent: 3,
                      frames_received: 3,
                      tester_status: 'completed',
                      runtime_provenance: {
                        execution_engine: 'run_agent',
                        live_media: false,
                        browser_peer: false,
                        fixture_backed_scoring: true,
                      },
                      real_call_readiness: {
                        run_agent_execution: 'proven',
                        pipecat_capture_hooks: 'proven',
                        browser_webrtc_peer: 'not_connected',
                        scoring: 'fixture_backed',
                      },
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

  await page.goto('/voice?api_base=http%3A%2F%2Fapi.example.test');
  await expect(page.getByRole('heading', { name: 'Voice eval' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Voice evaluation' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Manage targets' })).toHaveAttribute(
    'href',
    '/targets?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect(page.getByLabel('Voice target')).toHaveValue('generalist-voice-agent');
  await expect(page.getByLabel('Voice target').locator('option')).toHaveCount(1);
  await expect(page.getByLabel('Voice target').locator('option')).not.toContainText('ACC offline text fixture');
  await expect(page.getByRole('heading', { name: 'Pick the Run Agent target' })).toBeVisible();
  await expect(page.getByLabel('Built-in generalist voice evaluation')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open Eval evidence' })).toHaveAttribute(
    'href',
    '/eval?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect(page.getByText('Browser mic peer')).toBeVisible();
  await expect(page.getByText('Not connected in this slice')).toBeVisible();
  await page.getByRole('button', { name: 'Run generalist voice evaluation' }).click();
  const results = page.getByRole('region', { name: 'Run results' });
  await expect(results.getByText('voice-run-1')).toBeVisible();
  await expect(results.getByRole('link', { name: 'Open Run Agent detail' })).toHaveAttribute(
    'href',
    '/runs/voice-run-1?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect(results.getByText('Cancellation rescue', { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(results.getByText(/vCon|recording metadata|Pipecat capture proof|sample-based score/i).first()).toBeVisible();
  await expect(results.getByRole('progressbar', { name: 'Voice evaluation progress' })).toHaveAttribute('aria-valuenow', '100');
});
