import { expect, test, type Page } from '@playwright/test';

async function mockConnection(page: Page, { enabled = true, sharing = true, failSave = false } = {}) {
  let active = 'profile-a';
  let model: string | null = null;
  const writes: Array<{ path: string; body: unknown; header: string | undefined }> = [];
  await page.route('**/api/product/providers/chatgpt/**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.split('/').pop() || '';
    if (req.method() === 'POST') {
      const body = req.postDataJSON();
      writes.push({ path, body, header: req.headers()['x-cae-local-control'] });
      if (path === 'judge-model') {
        if (failSave) return route.fulfill({ status: 409, json: { detail: 'That model is no longer listed for this account.' } });
        model = body.model ? `chatgpt_plan/${body.model}` : null;
      }
      if (path === 'account') { active = body.profile_id; model = 'chatgpt_plan/select-model'; }
      if (path === 'oauth' || path === 'start') return route.fulfill({ json: { authorize_url: 'http://127.0.0.1:3312/#synthetic-oauth' } });
    }
    if (path === 'models') return route.fulfill({ json: { models: active === 'profile-a'
      ? [{ id: 'gpt-exact', display_name: 'Exact account model' }]
      : [{ id: 'gpt-other', display_name: 'Other account model' }] } });
    return route.fulfill({ json: { enabled, sharing, pending: false,
      status: sharing ? 'connected' : 'disconnected', active_profile_id: active, judge_model: model,
      profiles: [{ id: 'profile-a', label: 'Account A' }, { id: 'profile-b', label: 'Account B' }],
      message: enabled ? sharing ? 'ChatGPT plan use authorized.' : 'Authorize plan use; identity alone cannot judge.' : 'Enable CHATGPT_PLAN_LOCAL_ENABLED=1 locally.' } });
  });
  return writes;
}

test('ChatGPT account model selects the same ASSERT judge for upload and live reviews', async ({ page }) => {
  const writes = await mockConnection(page);
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'ChatGPT plan judge settings' });
  await panel.getByRole('button', { name: 'Load account models' }).click();
  await panel.getByLabel('ChatGPT judge model').selectOption('gpt-exact');
  await panel.getByRole('button', { name: 'Use ChatGPT model for ASSERT reviews' }).click();
  await expect(panel).toContainText('Active judge: chatgpt_plan/gpt-exact');
  expect(writes).toContainEqual({ path: 'judge-model', body: { model: 'gpt-exact' }, header: '1' });
  await panel.getByRole('button', { name: 'Restore deployment judge' }).click();
  await expect(panel).toContainText('Deployment default (not using this ChatGPT connection)');
});

test('switching ChatGPT accounts clears the model catalog and requires a new explicit selection', async ({ page }) => {
  await mockConnection(page);
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'ChatGPT plan judge settings' });
  await panel.getByRole('button', { name: 'Load account models' }).click();
  await expect(panel.getByLabel('ChatGPT judge model')).toContainText('Exact account model');
  await panel.getByLabel('ChatGPT account').selectOption('profile-b');
  await expect(panel.getByRole('button', { name: 'Use ChatGPT model for ASSERT reviews' })).toBeDisabled();
  await expect(panel.getByLabel('ChatGPT judge model')).not.toContainText('Exact account model');
  await panel.getByRole('button', { name: 'Load account models' }).click();
  await expect(panel.getByLabel('ChatGPT judge model')).toContainText('Other account model');
});

test('failed saves do not claim a ChatGPT judge was activated', async ({ page }) => {
  await mockConnection(page, { failSave: true });
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'ChatGPT plan judge settings' });
  await panel.getByRole('button', { name: 'Load account models' }).click();
  await panel.getByLabel('ChatGPT judge model').selectOption('gpt-exact');
  await panel.getByRole('button', { name: 'Use ChatGPT model for ASSERT reviews' }).click();
  await expect(panel.getByRole('alert')).toContainText('no longer listed');
  await expect(panel).not.toContainText('Saved.');
  await expect(panel).toContainText('Deployment default');
});

test('identity-only and disabled connections cannot select a judge model', async ({ page }) => {
  await mockConnection(page, { sharing: false });
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'ChatGPT plan judge settings' });
  await expect(panel).toContainText('identity alone cannot judge');
  await expect(panel.getByRole('button', { name: 'Load account models' })).toHaveCount(0);
  await mockConnection(page, { enabled: false, sharing: false });
  await page.reload();
  await expect(panel).toContainText('CHATGPT_PLAN_LOCAL_ENABLED');
  await expect(panel.getByRole('button', { name: 'Continue with ChatGPT' })).toHaveCount(0);
});

test('deployment judge can be explicitly restored without an active plan grant', async ({ page }) => {
  const writes = await mockConnection(page, { sharing: false });
  await page.goto('/benchmarks');
  const panel = page.getByRole('region', { name: 'ChatGPT plan judge settings' });
  await panel.getByRole('button', { name: 'Restore deployment judge' }).click();
  await expect(panel).toContainText('Restored deployment judge settings. No model was called.');
  expect(writes).toContainEqual({ path: 'judge-model', body: { model: null }, header: '1' });
});
