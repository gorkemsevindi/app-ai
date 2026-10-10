import { defineConfig, devices } from '@playwright/test';

// Live stack: API on :8000 (+ worker + S3) must already run (see e2e/README.md). The web app is started twice:
// :3000 against the API (live) and :3001 without API_BASE_URL (DEMO mode, as on a preview with no backend).
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'test-results/report.json' }]],
  use: { baseURL: 'http://localhost:3000', screenshot: 'only-on-failure', trace: 'retain-on-failure',
         launchOptions: { executablePath: process.env.CHROMIUM_PATH || undefined } },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    { command: 'npx next start -p 3000', port: 3000, reuseExistingServer: true,
      env: { API_BASE_URL: process.env.E2E_API ?? 'http://localhost:8000' } },
    { command: 'npx next start -p 3001', port: 3001, reuseExistingServer: true, env: { API_BASE_URL: '' } },
  ],
});
