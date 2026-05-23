import { test, expect, type Page } from '@playwright/test';

async function gotoWithRetry(page: Page, url: string, attempts = 3) {
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
      return;
    } catch (error) {
      lastError = error;
      if (attempt === attempts) {
        throw error;
      }
      await page.waitForTimeout(1000 * attempt);
    }
  }
  throw lastError;
}

test('default deck demo flow works end to end', async ({ page, baseURL }) => {
  page.on('console', (message) => console.log(`[browser:${message.type()}] ${message.text()}`));
  page.on('pageerror', (error) => console.log(`[pageerror] ${error.message}`));
  page.on('response', async (response) => {
    const url = response.url();
    if (url.includes('/api/sessions/') && /(start|pause|resume|end|next-slide|prev-slide|advance-autoplay)$/.test(url)) {
      console.log(`[api:${response.status()}] ${response.request().method()} ${url}`);
    }
  });

  expect(baseURL, 'Playwright baseURL must be configured').toBeTruthy();
  await gotoWithRetry(page, baseURL!);
  await expect(page.getByRole('heading', { level: 1 })).toContainText(/avatar-led ai sales presentations for pdf decks\.?/i);

  const defaultDeckButton = page.getByRole('button', { name: 'Use default attached deck' });
  await expect(defaultDeckButton.or(page.getByRole('button', { name: 'Upload PDF deck' }))).toBeVisible({ timeout: 30000 });
  await defaultDeckButton.click();
  await expect(page.getByText(/Default demo deck ready:/).or(page.getByText(/Session ready/))).toBeVisible({ timeout: 30000 });

  const createSessionButton = page.getByRole('button', { name: 'Create live demo session' });
  if (await createSessionButton.isVisible().catch(() => false)) {
    await createSessionButton.click();
    await expect(page.getByText(/Session ready/)).toBeVisible({ timeout: 15000 });
  }

  const href = (await page.getByRole('link', { name: /\/present\// }).getAttribute('href'))
    ?? await page.evaluate(() => (window as Window & { __pocSessionUrl?: string }).__pocSessionUrl ?? null);
  expect(href).toBeTruthy();

  const presentationUrl = new URL(href!, baseURL).toString();
  await gotoWithRetry(page, presentationUrl);
  await expect(page.getByRole('heading', { level: 1 })).toContainText(/ABC/i);

  const startButton = page.getByRole('button', { name: 'Start', exact: true });
  await expect(startButton).toBeEnabled();
  await startButton.click();
  await expect(page.getByRole('button', { name: 'Pause' }).or(page.getByText('presenting'))).toBeVisible({ timeout: 15000 });

  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByRole('button', { name: 'Previous' })).toBeEnabled({ timeout: 10000 });
  await expect(page.getByText('Slide 2 / 3').first()).toBeVisible({ timeout: 10000 });

  await page.getByPlaceholder('How do you compare to competitors?').fill('What is the main value proposition?');
  await page.getByRole('button', { name: 'Ask' }).click();
  await expect(page.getByText('Latest answer')).toBeVisible({ timeout: 10000 });

  await page.getByPlaceholder('How do you compare to competitors?').fill('How would this sound in a live voice handoff?');
  await page.getByRole('button', { name: 'Simulate voice question' }).click();
  await expect(page.getByText('Latest answer')).toBeVisible({ timeout: 10000 });

  await page.getByRole('button', { name: 'Start live voice' }).click();
  await expect(page.getByText(/Voice pipeline/)).toBeVisible({ timeout: 10000 });
  await expect(
    page.getByText(/Provider:/)
      .or(page.getByText(/No provider token yet/i))
      .or(page.getByText(/Ephemeral provider token returned/i))
      .first(),
  ).toBeVisible({ timeout: 10000 });

  const stopVoiceButton = page.getByRole('button', { name: 'Stop voice' });
  if ((await stopVoiceButton.isVisible().catch(() => false)) && (await stopVoiceButton.isEnabled().catch(() => false))) {
    await stopVoiceButton.click();
  }

  await page.getByRole('button', { name: 'End' }).click();
  await expect(page.getByText('Presentation ended.')).toBeVisible({ timeout: 10000 });
});
