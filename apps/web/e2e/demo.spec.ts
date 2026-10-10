// DEMO mode (deployment without API_BASE_URL, e.g. a Vercel Preview before the API is connected).
import { expect, test } from '@playwright/test';
import path from 'node:path';

const OUT = path.join(import.meta.dirname, '..', 'test-results');
test.use({ baseURL: 'http://localhost:3001' });

test('demo: banner, template project edited locally and persisted in this browser', async ({ page }) => {
  await page.goto('/app');
  await expect(page.getByText(/DEMO modu/)).toBeVisible();
  await page.goto('/app/new');
  await page.getByRole('button', { name: /Dikey hikâye/ }).click();
  await page.getByRole('button', { name: 'Oluştur ve aç' }).click();
  await expect(page.getByTestId('timeline')).toBeVisible();
  await page.getByRole('button', { name: 'T Metin' }).click();
  await expect(page.getByTestId('save-state')).toContainText('Kaydedildi');
  const n = await page.locator('.tl-clip').count();
  await page.reload();
  await expect(page.locator('.tl-clip')).toHaveCount(n);
  await page.screenshot({ path: path.join(OUT, '09-demo-editor.png') });
  const login = await page.request.post('/api/auth/login', { headers: { 'x-av-csrf': '1', origin: 'http://localhost:3001' }, data: { email: 'a@b.c', password: 'x' } });
  expect(login.status()).toBe(503);
});
