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
  tester_id: 'fixture_replay',
  executor_id: 'evidence_replay',
  provenance: {
    target_id: null,
    target_kind: 'saved_voice_replay',
    target_channel: 'voice',
    tester_id: 'fixture_replay',
    executor_id: 'evidence_replay',
    evidence_source: 'saved_replay',
    live_external_connection: false,
    saved_evidence: true,
    synthetic_media: true,
    honesty_label: 'Saved conversation replay · evidence evaluation · no live call',
  },
  execution_snapshot: {
    agent: { target: 'voice_fixture', environment: 'local' },
  },
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
  await expect(page.getByRole('link', { name: /ACC voice fixture agent/ })).toBeVisible();

  await page.getByRole('link', { name: /ACC voice fixture agent/ }).click();
  await expect(page.getByRole('heading', { name: 'ACC voice fixture agent' })).toBeVisible();
  const participants = page.getByLabel('Run participants and executor');
  await expect(participants).toContainText('Saved Conversation Replay');
  await expect(participants).toContainText('Evidence Replay');
  await expect(participants).toContainText('saved evidence replay');
  await expect(page.getByRole('button', { name: /Interruption Detection/ }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /^Latency/ }).first()).toBeVisible();
  await expect(page.getByLabel('Two-agent conversation timeline')).toBeVisible();
  await expect(page.getByLabel('Conversation turn sequence')).toContainText('I can help with that.');
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.');
});

test('active run analysis recovers after a transient polling error', async ({ page }) => {
  let requests = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });

  await page.route('**/api/execution/runs/exec-demo123**', async (route) => {
    requests += 1;
    if (requests === 1) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...runFixture, status: 'running', conversations: [] }),
      });
      return;
    }
    if (requests === 2) {
      await route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: 'temporary upstream error' }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runFixture) });
  });

  await page.goto('/runs/exec-demo123');
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.', { timeout: 10_000 });
  expect(requests).toBeGreaterThanOrEqual(3);
  await expect(page.locator('.scenarios-error')).toHaveCount(0);
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

test('runs list exposes readable status filtering and run metadata', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });

  const reviewRun = {
    ...runFixture,
    execution_run_id: 'exec-review456',
    status: 'needs_review',
    agent_name: 'Billing support staging',
    tester_id: 'scenario_simulator',
  };
  await page.route('**/api/execution/runs**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([runFixture, reviewRun]) });
  });

  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Billing support staging/ })).toContainText('needs review');
  await expect(page.getByRole('link', { name: /Billing support staging/ })).toContainText('scenario simulator');
  await page.getByLabel('Filter runs by status').selectOption('completed');
  await expect(page.getByRole('link', { name: /ACC voice fixture agent/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Billing support staging/ })).toHaveCount(0);
});

test('runs list refreshes active executions until their terminal status is visible', async ({ page }) => {
  let requests = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  });
  await page.route('**/api/execution/runs**', async (route) => {
    requests += 1;
    const status = requests === 1 ? 'running' : 'completed';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{
        ...runFixture,
        status,
        conversations: status === 'running' ? [] : runFixture.conversations,
      }]),
    });
  });

  await page.goto('/runs');
  const runLink = page.getByRole('link', { name: /ACC voice fixture agent/ });
  await expect(runLink).toContainText('running');
  await expect(runLink).toContainText('completed', { timeout: 10_000 });
  expect(requests).toBeGreaterThanOrEqual(2);
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

test('text agent analysis hides the voice conversation timeline', async ({ page }) => {
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
  await expect(page.getByLabel('Two-agent conversation timeline')).toHaveCount(0);
  await expect(page.getByLabel('Transcript')).toContainText('I want to cancel today.');
});

test('text latency excludes tester generation and missing timing marks', async ({ page }) => {
  const textRun = {
    ...runFixture,
    execution_run_id: 'exec-text-latency',
    mode: 'text_callable',
    agent_name: 'Text latency agent',
    conversations: [{
      ...runFixture.conversations[0],
      conversation_id: 'exec-text-latency-1',
      execution_run_id: 'exec-text-latency',
      mode: 'text_callable',
      latency_marks: [
        { label: 'exchange 1 target response', latency_ms: 400 },
        { label: 'exchange 2 tester response', latency_ms: 900 },
        { label: 'exchange 2 target response', elapsed_ms: null },
      ],
    }],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });
  await page.route('**/api/execution/runs/exec-text-latency**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(textRun) });
  });

  await page.goto('/runs/exec-text-latency');
  await expect(page.getByRole('button', { name: /Target Response Latency 400ms/ })).toBeVisible();
  await page.getByRole('button', { name: /Target Response Latency 400ms/ }).click();
  await expect(page.getByText(/count 1 · avg 400ms/)).toBeVisible();
  await expect(page.getByText('900ms')).toHaveCount(0);
});

test('voice analysis reports target first audio byte and excludes legacy exchange duration', async ({ page }) => {
  const accurateVoiceRun = {
    ...runFixture,
    execution_run_id: 'exec-voice-timing',
    mode: 'pipecat_webrtc',
    tester_id: 'pipecat_tester',
    executor_id: 'cae_local_audio_loop',
    conversations: [
      {
        ...runFixture.conversations[0],
        conversation_id: 'exec-voice-timing-1',
        execution_run_id: 'exec-voice-timing',
        mode: 'pipecat_webrtc',
        turns: [
          {
            turn_index: 1,
            speaker: 'caller',
            text: 'Please update my billing address.',
            direction: 'tester_to_target',
            frame_metadata: { bytes: 96000, sample_rate: 24000, channels: 1, duration_ms: 2000 },
          },
          {
            turn_index: 2,
            speaker: 'agent',
            text: 'I can help with that.',
            direction: 'target_to_tester',
            frame_metadata: { bytes: 72000, sample_rate: 24000, channels: 1, duration_ms: 1500 },
          },
        ],
        latency_marks: [
          {
            label: 'Missing normalized evidence',
            kind: 'target_first_audio_byte',
            participant: 'target',
            elapsed_ms: null,
          },
          {
            label: 'Target first audio byte · exchange 1',
            kind: 'target_first_audio_byte',
            participant: 'target',
            latency_ms: 640,
            exchange_elapsed_ms: 9120,
            stage_metrics: {
              asr_finalize_ms: 120,
              llm_ttft_ms: 180,
              llm_total_ms: 610,
              tts_ttfb_ms: 250,
            },
          },
        ],
      },
    ],
  };
  const legacyVoiceRun = {
    ...accurateVoiceRun,
    execution_run_id: 'exec-legacy-timing',
    conversations: [
      {
        ...accurateVoiceRun.conversations[0],
        conversation_id: 'exec-legacy-timing-1',
        execution_run_id: 'exec-legacy-timing',
        latency_marks: [
          { label: 'Two Pipecat graphs over local duplex frames', latency_ms: 17_534 },
        ],
      },
    ],
  };

  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
  });
  await page.route('**/api/execution/runs/**', async (route) => {
    const fixture = route.request().url().includes('exec-legacy-timing') ? legacyVoiceRun : accurateVoiceRun;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) });
  });

  await page.goto('/runs/exec-voice-timing');
  await expect(page.getByRole('button', { name: /Target Response Latency 640ms/ })).toBeVisible();
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('Target first audio byte');
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('LLM TTFT 180ms');
  await expect(page.getByLabel('Per-mark latency bars')).toContainText('LLM complete 610ms');
  await expect(page.getByLabel('Per-mark latency bars')).not.toContainText('LLM callback TTFB');
  await expect(page.getByLabel('Conversation turn sequence')).toContainText('first audio byte 640ms');

  await page.goto('/runs/exec-legacy-timing');
  await expect(page.getByRole('button', { name: /Target Response Latency n\/a/ })).toBeVisible();
  await expect(page.getByText(/legacy marks measured a complete two-agent exchange/)).toBeVisible();
  await expect(page.getByText('17534ms')).toHaveCount(0);
});

test('active voice listening uses WebRTC and completed playback restarts from the beginning', async ({ page }) => {
  let polls = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & {
      __playedVoiceUrls: string[];
      __liveWebrtcPlayCount: number;
    };
    runtime.__playedVoiceUrls = [];
    runtime.__liveWebrtcPlayCount = 0;
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        setTimeout(() => this.dispatchEvent(new Event('ended')), 20);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
    Object.defineProperty(HTMLMediaElement.prototype, 'play', {
      configurable: true,
      value() {
        runtime.__liveWebrtcPlayCount += 1;
        return Promise.resolve();
      },
    });
    class TestPeerConnection {
      connectionState = 'new';
      ontrack: ((event: { track: { kind: string }; streams: MediaStream[] }) => void) | null = null;
      onconnectionstatechange: (() => void) | null = null;
      onicecandidate: ((event: { candidate: null }) => void) | null = null;
      addTransceiver() {}
      async createOffer() {
        return { type: 'offer', sdp: 'test-offer' };
      }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.connectionState = 'connected';
        this.ontrack?.({
          track: { kind: 'audio' },
          streams: [new MediaStream()],
        });
        this.onconnectionstatechange?.();
      }
      close() {
        this.connectionState = 'closed';
      }
    }
    Object.defineProperty(window, 'RTCPeerConnection', { value: TestPeerConnection });
  });

  await page.route('**/api/execution/runs/exec-live-cursor**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'live-webrtc-token',
            expires_at: '2026-07-26T12:00:00Z',
            listen_url: '/api/execution/listeners/live-webrtc-token',
            webrtc_url: '/api/execution/listeners/live-webrtc-token/webrtc',
            webrtc_ice_url: '/api/execution/listeners/live-webrtc-token/webrtc/ice',
            webrtc_stop_url: '/api/execution/listeners/live-webrtc-token/webrtc/stop',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      return;
    }
    polls += 1;
    const hasNewAgentAudio = polls >= 3;
    const completed = polls >= 4;
    const liveEvents = [
      {
        sequence: 1,
        kind: 'audio',
        speaker: 'Caller',
        text: 'Please update my billing address.',
        direction: 'tester_to_target',
        media_url: '/api/execution/runs/exec-live-cursor/conversations/exec-live-cursor-1/audio/1?user_id=demo-user',
      },
      ...(hasNewAgentAudio ? [{
        sequence: 2,
        kind: 'audio',
        speaker: 'Agent',
        text: 'I can help with that.',
        direction: 'target_to_tester',
        media_url: '/api/execution/runs/exec-live-cursor/conversations/exec-live-cursor-1/audio/2?user_id=demo-user',
      }] : []),
    ];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-live-cursor',
        status: completed ? 'completed' : 'running',
        mode: 'pipecat_webrtc',
        tester_id: 'pipecat_tester',
        executor_id: 'cae_local_audio_loop',
        progress: {
          phase: completed ? 'completed' : 'executing',
          completed_conversations: completed ? 1 : 0,
          total_conversations: 1,
          percent: completed ? 100 : 50,
        },
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'exec-live-cursor-1',
          execution_run_id: 'exec-live-cursor',
          mode: 'pipecat_webrtc',
          status: completed ? 'completed' : 'running',
          live_events: liveEvents,
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/live-webrtc-token**', async (route) => {
    const url = route.request().url();
    if (url.endsWith('/webrtc')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          answer: { type: 'answer', sdp: 'test-answer' },
          status: 'listening',
        }),
      });
      return;
    }
    if (url.endsWith('/webrtc/ice') || url.endsWith('/webrtc/stop')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          run_status: polls >= 4 ? 'completed' : 'running',
        },
        conversations: [],
      }),
    });
  });

  await page.goto('/runs/exec-live-cursor');
  const feedback = page.getByLabel('Live run feedback');
  await expect(feedback.getByRole('button', { name: 'Listen to live WebRTC' })).toBeVisible();
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect(feedback.getByRole('button', { name: 'Stop live WebRTC' })).toBeVisible();
  await expect(feedback.getByText('Listening to the ongoing WebRTC audio stream. Earlier audio is not replayed.')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __liveWebrtcPlayCount: number }
  ).__liveWebrtcPlayCount)).toBe(1);
  expect(await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.length)).toBe(0);

  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible({ timeout: 10_000 });
  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/1?')).length)).toBe(1);
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.filter((url) => url.includes('/audio/2?')).length)).toBe(1);
});

test('completed replay switches from listener-token audio to owner-scoped audio', async ({ page }) => {
  let runPolls = 0;
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & { __playedVoiceUrls: string[] };
    runtime.__playedVoiceUrls = [];
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) {
        super();
      }
      play() {
        runtime.__playedVoiceUrls.push(this.url);
        setTimeout(() => this.dispatchEvent(new Event('ended')), 10);
        return Promise.resolve();
      }
      pause() {
        this.dispatchEvent(new Event('ended'));
      }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
  });

  const audioEvents = (scope: 'owner' | 'listener') => [1, 2].map((sequence) => ({
    sequence,
    kind: 'audio',
    speaker: sequence === 1 ? 'Caller' : 'Agent',
    text: sequence === 1 ? 'Please update my address.' : 'I can help.',
    direction: sequence === 1 ? 'tester_to_target' : 'target_to_tester',
    media_url: scope === 'owner'
      ? `/api/execution/runs/exec-listener-replay/conversations/conversation-1/audio/${sequence}?user_id=demo-user`
      : `/api/execution/listeners/expiring-token/conversations/conversation-1/audio/${sequence}`,
  }));

  await page.route('**/api/execution/runs/exec-listener-replay**', async (route) => {
    if (route.request().url().includes('/listener-token')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          listener: {
            token: 'expiring-token',
            expires_at: '2026-07-26T12:00:00Z',
            listen_url: '/listeners/expiring-token',
            read_only: true,
            can_inject_audio: false,
            requires_microphone: false,
            media_transport: 'webrtc',
          },
        }),
      });
      return;
    }
    runPolls += 1;
    const completed = runPolls >= 3;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...runFixture,
        execution_run_id: 'exec-listener-replay',
        status: completed ? 'completed' : 'running',
        mode: 'pipecat_webrtc',
        conversations: [{
          ...runFixture.conversations[0],
          conversation_id: 'conversation-1',
          execution_run_id: 'exec-listener-replay',
          mode: 'pipecat_webrtc',
          status: completed ? 'completed' : 'running',
          live_events: audioEvents('owner'),
        }],
      }),
    });
  });
  await page.route('**/api/execution/listeners/expiring-token', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        listener: {
          read_only: true,
          can_inject_audio: false,
          requires_microphone: false,
          run_status: runPolls >= 3 ? 'completed' : 'running',
        },
        conversations: [{
          conversation_id: 'conversation-1',
          live_events: audioEvents('listener'),
        }],
      }),
    });
  });

  await page.goto('/runs/exec-listener-replay');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Create live listener link' }).click();
  await expect(feedback.getByText('Read-only live listener')).toBeVisible();
  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible({
    timeout: 10_000,
  });
  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls.length)).toBe(2);
  const playedUrls = await page.evaluate(() => (
    window as Window & { __playedVoiceUrls: string[] }
  ).__playedVoiceUrls);
  expect(playedUrls.every((url) => url.includes('/api/execution/runs/exec-listener-replay/'))).toBe(true);
  expect(playedUrls.some((url) => url.includes('/api/execution/listeners/'))).toBe(false);
});
