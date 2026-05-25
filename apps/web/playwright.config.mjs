import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const repoRoot = path.resolve(__dirname, '../..');
const port = process.env.PORT ?? 3012;
const baseUrl = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  webServer: {
    command: 'node apps/web/tests/mock-api-server.js',
    cwd: repoRoot,
    port: 3003,
    timeout: 120_000,
    reuseExistingServer: !process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER,
  },
  use: {
    baseURL,
    headless: true,
  },
});
