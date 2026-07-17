import { defineConfig } from '@playwright/test';

const port = Number(process.env.PLAYWRIGHT_WEB_PORT ?? process.env.PORT ?? 3012);
const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? process.env.API_PORT ?? 8425);
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${port}`;
const apiBase = process.env.PLAYWRIGHT_API_BASE_URL ?? `http://127.0.0.1:${apiPort}`;
// Reuse the canonical local dev stack by default. CI has no server on this port,
// so Playwright still starts an isolated production server there. This prevents
// local test builds from replacing .next while `npm run dev` is using it.
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER !== '0';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  webServer: [
    {
      command: [
        'test -x apps/api/.venv/bin/python ||',
        '(./scripts/ensure-venv.sh apps/api/.venv apps/api/requirements.txt);',
        `OPENAI_CODEX_IMPORT_HOME=0 apps/api/.venv/bin/python -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port ${apiPort}`,
      ].join(' '),
      cwd: '../..',
      url: `${apiBase}/health`,
      timeout: 120_000,
      reuseExistingServer,
    },
    {
      command: [
        'rm -rf apps/web/.next-build;',
        `API_BASE_URL=${apiBase} NEXT_PUBLIC_API_BASE_URL=${apiBase} NEXT_DIST_DIR=.next-build npm --workspace apps/web run build &&`,
        'node scripts/restore-web-next-env.mjs &&',
        `PORT=${port} API_BASE_URL=${apiBase} NEXT_PUBLIC_API_BASE_URL=${apiBase} NEXT_DIST_DIR=.next-build npm --workspace apps/web run start`,
      ].join(' '),
      cwd: '../..',
      url: baseURL,
      timeout: 120_000,
      reuseExistingServer,
    },
  ],
  use: {
    baseURL,
    headless: true,
  },
});
