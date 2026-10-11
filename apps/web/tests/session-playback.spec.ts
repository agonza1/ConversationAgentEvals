import { expect, test } from '@playwright/test';

test('replay ends at the selected session and changing cases cancels queued audio', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('conversation-evals-demo-user', 'demo-user');
    const runtime = window as Window & {
      played: string[]; paused: number; currentAudio: EventTarget | null; finishAudio: () => void;
    };
    runtime.played = [];
    runtime.paused = 0;
    runtime.currentAudio = null;
    runtime.finishAudio = () => runtime.currentAudio?.dispatchEvent(new Event('ended'));
    class AudioStub extends EventTarget {
      constructor(private url: string) { super(); }
      play() { runtime.currentAudio = this; runtime.played.push(this.url); return Promise.resolve(); }
      pause() { runtime.paused += 1; }
    }
    Object.defineProperty(window, 'Audio', { value: AudioStub });
  });
  await page.route('**/api/execution/runs/exec-session-boundaries**', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      execution_run_id: 'exec-session-boundaries', user_id: 'demo-user',
      agent_id: 'generalist-voice-agent', status: 'needs_review', mode: 'pipecat_webrtc',
      conversations: ['first', 'second'].map((id) => ({
        conversation_id: id, scenario_title: id, status: 'completed', mode: 'pipecat_webrtc',
        turns: [], latency_marks: [],
        live_events: [1, 2].map((sequence) => ({
          sequence, kind: 'audio', speaker: 'Caller', text: `${id} turn ${sequence}`,
          media_url: `/recordings/${id}/${sequence}.wav`,
        })),
      })),
    }),
  }));
  await page.goto('/runs/exec-session-boundaries');
  const feedback = page.getByLabel('Live run feedback');
  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (window as Window & { played: string[] }).played.length)).toBe(1);
  await page.evaluate(() => (window as Window & { finishAudio: () => void }).finishAudio());
  await expect.poll(() => page.evaluate(() => (window as Window & { played: string[] }).played.length)).toBe(2);
  await page.evaluate(() => (window as Window & { finishAudio: () => void }).finishAudio());
  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible();
  expect(await page.evaluate(() => (window as Window & { played: string[] }).played))
    .toEqual(expect.arrayContaining([expect.stringContaining('/first/1.wav'), expect.stringContaining('/first/2.wav')]));
  expect(await page.evaluate(() => (window as Window & { played: string[] }).played.length)).toBe(2);

  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (window as Window & { played: string[] }).played.length)).toBe(3);
  await page.getByLabel('Conversation', { exact: true }).selectOption('second');
  await expect(feedback.getByRole('button', { name: 'Play recorded conversation' })).toBeVisible();
  await feedback.getByRole('button', { name: 'Show conversation evidence' }).click();
  await expect(feedback).toContainText('second turn 1');
  await expect(feedback).not.toContainText('first turn');
  await expect.poll(() => page.evaluate(() => (window as Window & { paused: number }).paused)).toBeGreaterThan(0);
  await feedback.getByRole('button', { name: 'Play recorded conversation' }).click();
  await expect.poll(() => page.evaluate(() => (window as Window & { played: string[] }).played.length)).toBe(4);
  expect((await page.evaluate(() => (window as Window & { played: string[] }).played)).at(-1))
    .toContain('/second/1.wav');
});
