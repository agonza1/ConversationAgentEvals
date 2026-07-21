import { expect, test } from '@playwright/test';

test('voice eval page launches and shows conversation evidence', async ({ page }) => {
  let polled = 0;

  await page.addInitScript(() => {
    const state = window as Window & { __audioPlayAttempts: string[] };
    state.__audioPlayAttempts = [];
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        state.__audioPlayAttempts.push(this.url);
        const firstAudioSegment = this.url.includes('/audio/1?')
          || (this.url.includes('/listeners/listener-token/') && this.url.includes('/audio/1'));
        if (firstAudioSegment && state.__audioPlayAttempts.filter((item) => item === this.url).length === 1) {
          return Promise.reject(new Error('autoplay blocked'));
        }
        queueMicrotask(() => this.dispatchEvent(new Event('ended')));
        return Promise.resolve();
      }
      pause() {}
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
  });

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
        reference_voice: {
          ready: true,
          llm_mode: 'real',
          dependencies: [
            { id: 'openai', label: 'OpenAI API key or Codex OAuth', ready: true, detail: 'openai ready for both agents.' },
            { id: 'shared_token', label: 'Shared reference token', ready: true, detail: 'Token ready.' },
            { id: 'pipecat', label: 'Pipecat service', ready: true, detail: 'Duplex and listener ready.' },
            { id: 'rtc_asr', label: 'rtc-asr', ready: true, detail: 'Reachable.' },
            { id: 'kokoro', label: 'Kokoro TTS', ready: true, detail: 'Reachable.' },
          ],
        },
      }),
    });
  });

  await page.route('**/api/execution/listeners/listener-token', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          execution_run_id: 'voice-run-1',
          run_status: 'running',
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          media_transport: 'webrtc',
          webrtc_url: '/api/execution/listeners/listener-token/webrtc',
          webrtc_ice_url: '/api/execution/listeners/listener-token/webrtc/ice',
          webrtc_stop_url: '/api/execution/listeners/listener-token/webrtc/stop',
        },
        conversations: [
          {
            conversation_id: 'voice-run-1-cancellation-rescue-1',
            status: 'running',
            scenario_id: 'cancellation-rescue',
            turns: [],
            live_events: [
              {
                sequence: 1,
                kind: 'audio',
                speaker: 'Caller',
                text: 'I want to cancel.',
                direction: 'tester_to_target',
                llm_output: 'I want to cancel.',
                asr_receipt: 'I want to cancel.',
                media_url: '/api/execution/listeners/listener-token/conversations/voice-run-1-cancellation-rescue-1/audio/1',
              },
              {
                sequence: 2,
                kind: 'audio',
                speaker: 'Agent',
                text: 'I can help with that.',
                direction: 'target_to_tester',
                llm_output: 'I can help with that.',
                asr_receipt: 'I can help with that.',
                media_url: '/api/execution/listeners/listener-token/conversations/voice-run-1-cancellation-rescue-1/audio/2',
              },
            ],
          },
        ],
      }),
    });
  });

  await page.route('**/api/execution/runs**', async (route) => {
    const url = route.request().url();
    if (route.request().method() === 'POST' && url.endsWith('/api/execution/runs')) {
      const posted = JSON.parse(route.request().postData() ?? '{}') as Record<string, unknown>;
      expect(posted).toMatchObject({
        suite_id: 'call-center-voice-ai',
        scenario_ids: ['cancellation-rescue'],
        mode: 'pipecat_webrtc',
        agent_id: 'generalist-voice-agent',
        tester_id: 'pipecat_tester',
        executor_id: 'cae_local_audio_loop',
        audio_transport: 'pipecat_small_webrtc',
        evaluate: true,
      });
      expect(posted).not.toHaveProperty('model_name');
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

    if (route.request().method() === 'POST' && url.includes('/api/execution/runs/voice-run-1/listener-token')) {
      expect(url).toContain('user_id=voice-user');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'listener-token',
            execution_run_id: 'voice-run-1',
            expires_at: '2026-07-19T21:00:00.000Z',
            listen_url: '/api/execution/listeners/listener-token',
            media_transport: 'webrtc',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
          },
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
                    live_events: [
                      {
                        sequence: 1,
                        kind: 'audio',
                        speaker: 'Caller',
                        text: 'I want to cancel.',
                        direction: 'tester_to_target',
                        llm_output: 'I want to cancel.',
                        asr_receipt: 'I want to cancel.',
                        media_url: '/api/execution/runs/voice-run-1/conversations/voice-run-1-cancellation-rescue-1/audio/1?user_id=voice-user',
                      },
                      {
                        sequence: 2,
                        kind: 'audio',
                        speaker: 'Agent',
                        text: 'I can help with that.',
                        direction: 'target_to_tester',
                        llm_output: 'I can help with that.',
                        asr_receipt: 'I can help with that.',
                        media_url: '/api/execution/runs/voice-run-1/conversations/voice-run-1-cancellation-rescue-1/audio/2?user_id=voice-user',
                      },
                    ],
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
                        live_media: true,
                        browser_peer: false,
                        fixture_backed_scoring: false,
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
  await expect(page.getByText('Browser microphone/target')).toBeVisible();
  await expect(page.getByText('Unavailable; listener is receive-only')).toBeVisible();
  await page.getByRole('button', { name: 'Run evaluation' }).click();
  const results = page.getByRole('region', { name: 'Run results' });
  await expect(results.getByText('voice-run-1')).toBeVisible();
  await results.getByRole('button', { name: 'Create listener link' }).click();
  const listenerLink = results.getByRole('link', { name: '/listeners/listener-token?api_base=http%3A%2F%2Fapi.example.test' });
  await expect(listenerLink).toHaveAttribute('href', '/listeners/listener-token?api_base=http%3A%2F%2Fapi.example.test');
  await expect(results.getByLabel('Read-only browser listener')).toContainText('cannot inject audio');
  await expect(results.getByLabel('Read-only browser listener')).toContainText('no microphone');
  await expect(results.getByLabel('Observed live exchange')).toContainText('I want to cancel.', { timeout: 10000 });
  await expect(results.getByLabel('Observed live exchange')).toContainText('tester → target');
  await expect(results.getByLabel('Observed live exchange')).toContainText('LLM output: I want to cancel.');
  await expect(results.getByLabel('Observed live exchange')).toContainText('ASR receipt: I want to cancel.');
  await results.getByRole('button', { name: 'Unmute live conversation' }).click();
  await expect(results.getByRole('button', { name: 'Mute live conversation' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __audioPlayAttempts: string[] }
  ).__audioPlayAttempts.filter((url) => url.includes('/listeners/listener-token/') && url.includes('/audio/1')).length)).toBe(1);
  await results.getByRole('button', { name: 'Mute live conversation' }).click();
  await results.getByRole('button', { name: 'Unmute live conversation' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __audioPlayAttempts: string[] }
  ).__audioPlayAttempts.filter((url) => url.includes('/listeners/listener-token/') && url.includes('/audio/1')).length)).toBeGreaterThan(1);
  await expect(results.getByRole('link', { name: 'Open Run Agent detail' })).toHaveAttribute(
    'href',
    '/runs/voice-run-1?api_base=http%3A%2F%2Fapi.example.test',
  );
  await expect(results.getByText('Cancellation rescue', { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(results.getByText(/vCon|recording metadata|Pipecat capture proof|sample-based score/i).first()).toBeVisible();
  await expect(results.getByRole('progressbar', { name: 'Voice evaluation progress' })).toHaveAttribute('aria-valuenow', '100');
});

test('browser listener page polls token-scoped live events', async ({ page }) => {
  let listenerPolls = 0;
  let webrtcOffers = 0;
  let iceCandidates = 0;
  let listenerStops = 0;
  await page.addInitScript(() => {
    const runtime = window as Window & { __getUserMediaCalls: number };
    runtime.__getUserMediaCalls = 0;
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: () => { runtime.__getUserMediaCalls += 1; throw new Error('Listener must not request media.'); } },
      configurable: true,
    });
    class ListenerPeer {
      connectionState = 'new';
      onconnectionstatechange: (() => void) | null = null;
      onicecandidate: ((event: { candidate: { toJSON: () => RTCIceCandidateInit } }) => void) | null = null;
      ontrack = null;
      addTransceiver(kind: string, init: RTCRtpTransceiverInit) {
        if (kind !== 'audio' || init.direction !== 'recvonly') throw new Error('Listener must offer receive-only audio.');
        return {};
      }
      async createOffer() { return { sdp: 'receive-only-offer', type: 'offer' as RTCSdpType }; }
      async setLocalDescription() {
        this.onicecandidate?.({ candidate: { toJSON: () => ({ candidate: 'early-candidate' }) } });
      }
      async setRemoteDescription() {
        this.connectionState = 'connected';
        this.onconnectionstatechange?.();
      }
      close() { this.connectionState = 'closed'; }
    }
    Object.defineProperty(window, 'RTCPeerConnection', { value: ListenerPeer, configurable: true });
  });
  await page.route(/\/api\/execution\/listeners\/listener-token\/webrtc$/, async (route) => {
    webrtcOffers += 1;
    const payload = JSON.parse(route.request().postData() ?? '{}');
    expect(payload).toEqual({ sdp: 'receive-only-offer', type: 'offer' });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'listening', answer: { sdp: 'send-only-answer', type: 'answer' } }),
    });
  });
  await page.route(/\/api\/execution\/listeners\/listener-token\/webrtc\/ice$/, async (route) => {
    iceCandidates += 1;
    expect(JSON.parse(route.request().postData() ?? '{}')).toEqual({ candidate: { candidate: 'early-candidate' } });
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  await page.route(/\/api\/execution\/listeners\/listener-token\/webrtc\/stop$/, async (route) => {
    listenerStops += 1;
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  await page.route('**/api/execution/listeners/listener-token', async (route) => {
    listenerPolls += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          execution_run_id: 'voice-run-1',
          run_status: listenerPolls < 2 ? 'running' : 'completed',
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          media_transport: 'webrtc',
          webrtc_url: '/api/execution/listeners/listener-token/webrtc',
          webrtc_ice_url: '/api/execution/listeners/listener-token/webrtc/ice',
          webrtc_stop_url: '/api/execution/listeners/listener-token/webrtc/stop',
        },
        conversations: [
          {
            conversation_id: 'voice-run-1-cancellation-rescue-1',
            live_events: [
              {
                sequence: 1,
                kind: 'message',
                speaker: 'Caller',
                text: 'I want to cancel.',
                direction: 'tester_to_target',
                llm_output: 'I want to cancel.',
                asr_receipt: 'I want to cancel.',
              },
              ...(listenerPolls > 1 ? [{
                sequence: 2,
                kind: 'message',
                speaker: 'Agent',
                text: 'I can help with that.',
                direction: 'target_to_tester',
                llm_output: 'I can help with that.',
                asr_receipt: 'I can help with that.',
              }] : []),
            ],
          },
        ],
      }),
    });
  });

  await page.goto('/listeners/listener-token?api_base=http%3A%2F%2Fapi.example.test');
  await expect(page.getByRole('heading', { name: 'Read-only browser listener' })).toBeVisible();
  await expect(page.getByLabel('Observed live exchange')).toContainText('I want to cancel.');
  await expect(page.getByLabel('Observed live exchange')).toContainText('I can help with that.', { timeout: 5000 });
  await expect(page.getByLabel('Observed live exchange')).toContainText('tester → target');
  await expect(page.getByLabel('Observed live exchange')).toContainText('target → tester');
  await expect(page.getByLabel('Observed live exchange')).toContainText('LLM output: I can help with that.');
  await expect(page.getByLabel('Observed live exchange')).toContainText('ASR receipt: I can help with that.');
  await page.getByRole('button', { name: 'Start WebRTC listener' }).click();
  await expect(page.getByLabel('WebRTC listener status')).toHaveText('WebRTC · listening');
  expect(webrtcOffers).toBe(1);
  expect(iceCandidates).toBe(1);
  await page.getByRole('button', { name: 'Reconnect WebRTC listener' }).click();
  await expect.poll(() => webrtcOffers).toBe(2);
  await expect(page.getByLabel('WebRTC listener status')).toHaveText('WebRTC · listening');
  expect(iceCandidates).toBe(2);
  expect(listenerStops).toBe(1);
  expect(await page.evaluate(() => (window as Window & { __getUserMediaCalls: number }).__getUserMediaCalls)).toBe(0);
  expect(listenerPolls).toBeGreaterThan(1);
});

test('voice page blocks before queueing when real dependencies are unavailable', async ({ page }) => {
  await page.route('**/api/agents', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agents: [{
          id: 'generalist-voice-agent',
          name: 'Built-in generalist voice agent',
          channel: 'voice',
          target: 'builtin_sample_voice',
        }],
      }),
    });
  });
  await page.route('**/api/execution/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        audio: { transports: [{ id: 'pipecat_small_webrtc', available: true }] },
        reference_voice: {
          ready: false,
          llm_mode: 'real',
          dependencies: [
            { id: 'openai', label: 'OpenAI API key or Codex OAuth', ready: false, detail: 'Set OPENAI_API_KEY or connect OpenAI/Codex OAuth.' },
            { id: 'shared_token', label: 'Shared reference token', ready: false, detail: 'Set REFERENCE_AGENT_INTERNAL_TOKEN in API and Pipecat.' },
            { id: 'pipecat', label: 'Pipecat service', ready: false, detail: 'Pipecat is unreachable at http://localhost:8110.' },
            { id: 'rtc_asr', label: 'rtc-asr', ready: false, detail: 'Set RTC_ASR_BASE_URL.' },
            { id: 'kokoro', label: 'Kokoro TTS', ready: false, detail: 'Set KOKORO_BASE_URL.' },
          ],
        },
      }),
    });
  });

  await page.goto('/voice?api_base=http://api.example.test');
  await expect(page.getByLabel('Voice preflight blocked')).toContainText('Set OPENAI_API_KEY');
  await expect(page.getByLabel('Voice preflight blocked')).toContainText('Set RTC_ASR_BASE_URL');
  await expect(page.getByRole('button', { name: 'Run evaluation' })).toBeDisabled();
  await expect(page.getByText('Current-run duplex capture')).toBeVisible();
  await expect(page.getByText('Sample-based capture')).toHaveCount(0);
});
