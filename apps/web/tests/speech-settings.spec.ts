import { expect, test, type Page } from '@playwright/test';

const selection = {
  stt_provider: 'rtc-asr', tts_provider: 'kokoro', stt_model: 'service-selected', tts_model: 'kokoro',
  tester_voice: 'af_heart', target_voice: 'af_bella', language: 'en', cartesia_version: '2026-03-01',
};
const defaults = {
  local: { stt_model: 'service-selected', tts_model: 'kokoro', tester_voice: 'af_heart', target_voice: 'af_bella' },
  deepgram: { stt_model: 'nova-3', tts_model: 'aura-2', tester_voice: 'aura-2-asteria-en', target_voice: 'aura-2-orion-en' },
  cartesia: { stt_model: 'ink-2', tts_model: 'sonic-3.6', tester_voice: '', target_voice: '' },
};

async function mockSpeech(page: Page, { failRead = false, failSave = false } = {}) {
  let saved = { ...selection };
  const writes: { path: string; body: typeof selection }[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') writes.push({ path, body: request.postDataJSON() });
    if (path.endsWith('/speech-settings')) {
      if (request.method() === 'PUT') {
        if (failSave) return route.fulfill({ status: 422, json: { detail: [{ msg: 'Choose different caller and agent voices.' }] } });
        saved = request.postDataJSON();
      } else if (failRead) return route.fulfill({ status: 503, json: { detail: 'Unavailable' } });
      return route.fulfill({ json: { selection: saved, defaults, scope: 'builtin_sample_voice', credentials: 'server_environment' } });
    }
    if (path.endsWith('/agents')) return route.fulfill({ json: { agents: [
      { id: 'built-in-voice', target: 'builtin_sample_voice', name: 'Built-in voice', channel: 'voice' },
      { id: 'remote-voice', target: 'pipecat_public_demo', name: 'Remote voice', channel: 'voice' },
    ] } });
    if (path.endsWith('/suites')) return route.fulfill({ json: [{ id: 'call-center-voice-ai', title: 'Voice cases', scenarios: [{ id: 'billing-address-change', title: 'Address change' }] }] });
    if (path.endsWith('/execution/health')) return route.fulfill({ json: { reference_voice: { ready: true, dependencies: [] } } });
    if (path.endsWith('/product/config')) return route.fulfill({ json: { pricing: [], usage_rules: [], auth: { enabled: false, providers: [] } } });
    if (path.endsWith('/openai/models')) return route.fulfill({ json: { models: [{ id: 'gpt-6-luna' }], default_model: 'gpt-6-luna' } });
    if (path.endsWith('/openai/status')) return route.fulfill({ json: { status: 'connected' } });
    if (path.endsWith('/generation-settings')) return route.fulfill({ json: { effective_model: 'gpt-6-luna', available: true } });
    if (path.endsWith('/chatgpt/status')) return route.fulfill({ json: { enabled: false, profiles: [] } });
    return route.fulfill({ json: [] });
  });
  return { writes, recover: () => { failRead = false; }, update: (next: Partial<typeof selection>) => { saved = { ...saved, ...next }; } };
}

test('Speech section is discoverable, scoped and read-only on load at all viewport sizes', async ({ page }) => {
  const { writes } = await mockSpeech(page);
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/settings');
    await expect(page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Speech · STT / TTS' })).toHaveAttribute('href', '#speech');
    const panel = page.getByRole('region', { name: 'Speech provider settings' });
    await expect(panel.getByLabel('STT model', { exact: true })).toHaveValue('Managed by rtc-asr service');
    await expect(panel.getByLabel('STT model', { exact: true })).toBeDisabled();
    await expect(panel).toContainText('does not change Steven/Waylo');
    await expect(panel.getByRole('button', { name: 'Save speech settings' })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    if (width === 1440 || width === 320) await page.screenshot({ path: `/tmp/speech-settings-${width}.png`, fullPage: true });
  }
  expect(writes).toEqual([]);
});

test('Independent STT/TTS choices persist only on explicit save and do not launch runs', async ({ page }) => {
  const { writes } = await mockSpeech(page);
  await page.goto('/settings#speech');
  const panel = page.getByRole('region', { name: 'Speech provider settings' });
  await panel.getByLabel('STT provider').selectOption('deepgram');
  await expect(panel.getByLabel('STT model', { exact: true })).toHaveValue('nova-3');
  await panel.getByLabel('TTS provider').selectOption('cartesia');
  await expect(panel.getByLabel('TTS model', { exact: true })).toHaveValue('sonic-3.6');
  await panel.getByLabel('Caller voice ID').fill('caller-library-id');
  await panel.getByLabel('Agent voice ID').fill('agent-library-id');
  await expect(panel.locator('.settings-summary')).toContainText('kokoro');
  expect(writes).toEqual([]);
  await panel.getByRole('button', { name: 'Save speech settings' }).click();
  await expect(panel.getByRole('status')).toContainText('Existing runs and remote agents are unchanged');
  expect(writes).toEqual([{ path: '/api/execution/speech-settings', body: {
    ...selection, stt_provider: 'deepgram', tts_provider: 'cartesia', stt_model: 'nova-3', tts_model: 'sonic-3.6',
    tester_voice: 'caller-library-id', target_voice: 'agent-library-id',
  } }]);
  await page.reload();
  await expect(panel.getByLabel('Caller voice ID')).toHaveValue('caller-library-id');
});

test('Deepgram uses model-per-voice labels and reload explicitly discards unsaved edits', async ({ page }) => {
  const { writes } = await mockSpeech(page);
  await page.goto('/settings');
  const panel = page.getByRole('region', { name: 'Speech provider settings' });
  await panel.getByLabel('TTS provider').selectOption('deepgram');
  await expect(panel.getByLabel('Caller voice ID')).toHaveValue('aura-2-asteria-en');
  await expect(panel.getByLabel('TTS model', { exact: true })).toHaveCount(0);
  await expect(panel).toContainText('Aura voice ID selects the TTS model');
  await expect(panel).toContainText('Unsaved selection');
  await panel.getByRole('button', { name: 'Reload saved settings' }).click();
  await expect(panel.getByLabel('TTS provider')).toHaveValue('kokoro');
  await expect(panel.getByRole('button', { name: 'Save speech settings' })).toBeDisabled();
  expect(writes).toEqual([]);
});

test('Load failures recover and failed saves retain edits without claiming readiness or success', async ({ page }) => {
  const mock = await mockSpeech(page, { failRead: true, failSave: true });
  await page.goto('/settings');
  const panel = page.getByRole('region', { name: 'Speech provider settings' });
  await expect(panel.getByRole('alert')).toContainText('Speech settings unavailable');
  mock.recover();
  await panel.getByRole('button', { name: 'Reload saved settings' }).click();
  await expect(panel.getByLabel('TTS provider')).toHaveValue('kokoro');
  await panel.getByLabel('Agent voice ID').fill('af_heart');
  await panel.getByRole('button', { name: 'Save speech settings' }).click();
  await expect(panel.getByRole('alert')).toContainText('Choose different caller and agent voices');
  await expect(panel.getByRole('status')).toHaveCount(0);
  await expect(panel.getByLabel('Agent voice ID')).toHaveValue('af_heart');
  await expect(panel.locator('.settings-summary')).toContainText('kokoro');
  await expect(panel).toContainText('Saving does not verify provider access');
});

test('Built-in run shows a read-only summary linking to the same backend; remote run has no selector', async ({ page }) => {
  const { writes } = await mockSpeech(page);
  await page.goto('/runs?agent_id=built-in-voice&api_base=http%3A%2F%2Fapi.example.test');
  const summary = page.getByRole('region', { name: 'Speech settings summary' });
  await expect(summary).toContainText('rtc-asr');
  await expect(summary.getByRole('link', { name: 'Configure speech in Settings →' })).toHaveAttribute('href', '/settings?api_base=http%3A%2F%2Fapi.example.test#speech');
  await expect(page.getByLabel('TTS provider')).toHaveCount(0);
  await page.getByLabel('Execution agent target').selectOption('remote-voice');
  await expect(summary).toHaveCount(0);
  expect(writes).toEqual([]);
});

test('Returning to Run refreshes saved speech and readiness without placing a call', async ({ page }) => {
  const mock = await mockSpeech(page);
  let ready = true;
  await page.route('**/api/execution/health', (route) => route.fulfill({ json: { reference_voice: {
    ready, dependencies: [{ id: 'rtc_asr', label: 'STT', ready, detail: 'Saved speech provider is unavailable.' }],
  } } }));
  await page.goto('/runs?agent_id=built-in-voice');
  const summary = page.getByRole('region', { name: 'Speech settings summary' });
  await expect(summary).toContainText('rtc-asr');
  mock.update({ stt_provider: 'deepgram', stt_model: 'nova-3' });
  ready = false;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(summary).toContainText('nova-3');
  await expect(page.getByRole('alert', { name: 'Run Agent voice preflight blocked' })).toContainText('Saved speech provider is unavailable.');
  await expect(page.getByRole('button', { name: /^Run evaluation$|^Run 1 voice test$/ })).toBeDisabled();
  expect(mock.writes).toEqual([]);
});

test('Malformed successful settings responses stay recoverable without crashing the page', async ({ page }) => {
  await mockSpeech(page);
  await page.route('**/api/execution/speech-settings', (route) => route.fulfill({ json: {} }));
  await page.goto('/settings');
  const panel = page.getByRole('region', { name: 'Speech provider settings' });
  await expect(panel.getByRole('alert')).toContainText('Speech settings unavailable');
  await expect(panel.getByRole('button', { name: 'Reload saved settings' })).toBeEnabled();
  await expect(page.getByRole('heading', { name: 'Connections and models' })).toBeVisible();
});
