import { expect, test } from '@playwright/test';

test('Waylo uses shared receive-only WebRTC controls and falls back visibly on publisher failure', async ({ page }) => {
  let failed = false;
  const runId = 'exec-waylo-monitor', conversationId = 'waylo-monitor-1';
  const events = () => failed ? [
    { sequence: 1, kind: 'message', speaker: 'Listener', text: 'Monitoring unavailable; call continues.',
      frame_metadata: { media_event: 'live_monitor_unavailable' } },
    { sequence: 2, kind: 'audio', speaker: 'Agent', text: 'New captured response.',
      media_url: '/api/test-response.wav', direction: 'target_to_tester' },
  ] : [];
  const conversation = () => ({ conversation_id: conversationId, execution_run_id: runId,
    scenario_id: 'case', scenario_title: 'Information only', mode: 'pipecat_webrtc', status: 'running',
    audio_session: { transport: 'waylo_livekit' }, turns: [], action_trace: [], final_state: {}, live_events: events() });
  const run = () => ({ execution_run_id: runId, mode: 'pipecat_webrtc', status: 'running',
    user_id: 'demo-user', executor_id: 'waylo_livekit', suite_id: 'user-scenarios', scenario_ids: ['case'],
    conversations: [conversation()] });
  await page.addInitScript(() => {
    localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & { __played: string[]; __micRequested: boolean };
    runtime.__played = [];
    runtime.__micRequested = false;
    Object.defineProperty(navigator, 'mediaDevices', { value: {
      getUserMedia: async () => { runtime.__micRequested = true; throw new Error('No microphone permitted'); },
    } });
    class TestAudio extends EventTarget {
      constructor(private readonly url: string) { super(); }
      play() { runtime.__played.push(this.url); setTimeout(() => this.dispatchEvent(new Event('ended')), 10); return Promise.resolve(); }
      pause() {}
    }
    class Peer {
      connectionState = 'new';
      onconnectionstatechange: (() => void) | null = null;
      ontrack = null;
      onicecandidate = null;
      addTransceiver(_kind: string, options: { direction: string }) {
        if (options.direction !== 'recvonly') throw new Error('Listener must be read only');
      }
      async createOffer() { return { type: 'offer', sdp: 'read-only-test-offer' }; }
      async setLocalDescription() {}
      async setRemoteDescription() { this.connectionState = 'connected'; this.onconnectionstatechange?.(); }
      close() { this.connectionState = 'closed'; }
    }
    Object.defineProperty(window, 'Audio', { value: TestAudio });
    Object.defineProperty(window, 'RTCPeerConnection', { value: Peer });
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/listener-token')) return route.fulfill({ json: { listener: {
      token: 'read-only-test', expires_at: '2099-01-01T00:00:00Z', read_only: true,
      requires_microphone: false, can_inject_audio: false,
      listen_url: '/api/execution/listeners/read-only-test',
      webrtc_url: '/api/execution/listeners/read-only-test/webrtc',
      webrtc_stop_url: '/api/execution/listeners/read-only-test/webrtc/stop',
    } } });
    if (path.endsWith('/webrtc')) return route.fulfill({ json: { status: 'listening',
      answer: { type: 'answer', sdp: 'receive-only-answer' }, pre_attach_listener_media_keys: [], audio_published_during_attach: false } });
    if (path === '/api/execution/listeners/read-only-test') return route.fulfill({ json: {
      listener: { execution_run_id: runId, run_status: 'running', read_only: true,
        requires_microphone: false, can_inject_audio: false }, conversations: [conversation()],
    } });
    if (path === `/api/execution/runs/${runId}`) return route.fulfill({ json: run() });
    return route.fulfill({ json: {} });
  });
  await page.goto(`/runs/${runId}`);
  const feedback = page.getByLabel('Live run feedback');
  await expect(feedback.getByRole('button', { name: 'Create live listener link' })).toBeEnabled();
  await feedback.getByRole('button', { name: 'Listen to live WebRTC' }).click();
  await expect(feedback.getByLabel('WebRTC listener status')).toHaveText('WebRTC · listening');
  failed = true;
  await expect(feedback.getByLabel('WebRTC listener status')).toHaveText('Live audio · HTTP fallback');
  await expect(feedback.getByLabel('WebRTC listener status')).toHaveAttribute('data-transport-status', 'fallback');
  await expect.poll(() => page.evaluate(() => (window as Window & { __played: string[] }).__played
    .map((url) => new URL(url, location.href).pathname))).toEqual(['/api/test-response.wav']);
  expect(await page.evaluate(() => (window as Window & { __micRequested: boolean }).__micRequested)).toBe(false);
});
