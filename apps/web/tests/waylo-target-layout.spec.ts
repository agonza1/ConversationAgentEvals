import { expect, test, type Page } from '@playwright/test';

const uuid = '11111111-1111-4111-8111-111111111111';

async function mockTargets(page: Page) {
  await page.route('**/api/**', async (route) => {
    expect(route.request().method()).toBe('GET');
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith('/config') ? { endpoint_url: 'https://api.poc.app.waylovoice.ai' }
      : path === '/api/agents' ? { agents: [
        { id: 'store-voice-fixture', name: 'Store voice fixture', channel: 'voice', target: 'waylo',
          environment: 'staging', description: 'A reusable store support voice agent.',
          connection: { endpoint_url: `https://api.poc.app.waylovoice.ai/admin/tenants/${uuid}`,
            workspace_id: uuid, waylo_agent_id: '22222222-2222-4222-8222-222222222222', auth_type: 'waylo_browser_session', secret_ref: null }, metadata: {} },
        { id: 'generalist-text-agent', name: 'Generalist text fixture', channel: 'text', target: 'openai_codex', description: 'A generalist test target.', metadata: { model_name: 'fixture-model', prompt_version: 'seed' } },
        { id: 'generalist-voice-agent', name: 'Generalist voice fixture', channel: 'voice', target: 'builtin_sample_voice', description: 'The local generalist voice target.', metadata: { model_name: 'fixture-model', prompt_version: 'seed' } },
        { id: 'http-fixture', name: 'HTTP endpoint fixture', channel: 'text', target: 'http_endpoint',
          description: 'A configured HTTP endpoint.', connection: { endpoint_url: 'https://support.fixture.test/chat', response_path: 'response' }, metadata: {} },
      ] } : {};
    await route.fulfill({ json: body });
  });
}

for (const width of [1280, 390, 320]) {
  test(`Waylo target controls preserve natural card layout at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockTargets(page);
    await page.goto('/targets');
    await expect(page.getByRole('article')).toHaveCount(4);
    const measures = await page.locator('.agents-grid').evaluate((grid) => {
      const cards = [...grid.querySelectorAll<HTMLElement>('.agents-card')];
      return {
        viewport: window.innerWidth, documentWidth: document.documentElement.scrollWidth,
        cards: cards.map((card) => ({ title: card.querySelector('h2')?.textContent,
          height: Math.round(card.getBoundingClientRect().height),
          buttonHeight: Math.round(card.querySelector('.agents-try-button')!.getBoundingClientRect().height),
          overflow: card.scrollWidth - card.clientWidth,
        })),
      };
    });
    console.log(`Waylo target layout ${width}px: ${JSON.stringify(measures)}`);
    expect(measures.documentWidth).toBeLessThanOrEqual(width);
    for (const card of measures.cards) {
      expect(card.buttonHeight).toBeGreaterThanOrEqual(44);
      expect(card.buttonHeight).toBeLessThanOrEqual(48);
      expect(card.overflow).toBeLessThanOrEqual(1);
    }
    if (width === 1280) expect(measures.cards[1].height).toBeLessThan(measures.cards[0].height - 80);

    const waylo = page.getByRole('article').filter({ hasText: 'Store voice fixture' });
    await expect(waylo.getByLabel('Waylo email')).not.toBeVisible();
    await waylo.locator('summary').filter({ hasText: /^Connect Waylo$/ }).click();
    await expect(waylo.getByLabel('Waylo email')).toBeVisible();
    await expect(waylo.getByLabel('Waylo password')).toBeEnabled();
    const field = await waylo.getByLabel('Waylo email').evaluate((input) => {
      const style = getComputedStyle(input);
      return { width: input.getBoundingClientRect().width, border: style.borderStyle, radius: style.borderRadius };
    });
    expect(field.border).toBe('solid');
    expect(field.radius).toBe('10px');
    const cardWidth = (await waylo.boundingBox())!.width;
    expect(field.width).toBeLessThan(cardWidth - 30);
    await expect(waylo.getByLabel('Confirm temporary Waylo sign-in')).not.toBeChecked();
    await expect(waylo.locator('button').filter({ hasText: /^Connect Waylo$/ })).toBeDisabled();
    const controlsOverflow = await waylo.evaluate((card) => ({ card: card.scrollWidth - card.clientWidth, document: document.documentElement.scrollWidth - window.innerWidth }));
    expect(controlsOverflow).toEqual({ card: 0, document: 0 });
    await waylo.getByLabel('Waylo password').fill('synthetic-layout-fixture');
    await waylo.getByLabel('Confirm temporary Waylo sign-in').check();
    await waylo.locator('summary').filter({ hasText: /^Connect Waylo$/ }).click();
    await expect(waylo.getByLabel('Waylo password')).not.toBeVisible();
    await waylo.locator('summary').filter({ hasText: /^Connect Waylo$/ }).click();
    await expect(waylo.getByLabel('Waylo password')).toHaveValue('');
    await expect(waylo.getByLabel('Confirm temporary Waylo sign-in')).not.toBeChecked();
    await waylo.locator('summary').filter({ hasText: /^Import existing human call$/ }).click();
    await expect(waylo.getByLabel('Session UUID for Store voice fixture')).toBeVisible();
  });
}
