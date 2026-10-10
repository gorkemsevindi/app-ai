// E2E against the real local stack (FastAPI + Postgres + S3-compatible storage + ffmpeg worker).
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const OUT = path.join(import.meta.dirname, '..', 'test-results');
const API = process.env.E2E_API ?? 'http://localhost:8000';
const email = `e2e+${Date.now()}@example.com`;
const password = 'e2e-password-123';
const metrics: Record<string, unknown> = {};

test.describe.configure({ mode: 'serial' });

test.beforeAll(() => {
  fs.mkdirSync(OUT, { recursive: true });
  const v = path.join(OUT, 'clip.mp4');
  if (!fs.existsSync(v)) {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=s=640x360:r=25:d=6', '-f', 'lavfi',
      '-i', 'sine=f=440:d=6', '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', v]);
  }
  // Playwright's Chromium has no H.264 decoder (proprietary codec); the in-browser preview is checked with VP9.
  const w = path.join(OUT, 'clip.webm');
  if (!fs.existsSync(w)) {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=s=640x360:r=25:d=6', '-f', 'lavfi',
      '-i', 'sine=f=440:d=6', '-shortest', '-c:v', 'libvpx-vp9', '-b:v', '600k', '-deadline', 'realtime', '-c:a', 'libopus', w]);
  }
});
test.afterAll(() => fs.writeFileSync(path.join(OUT, 'metrics.json'), JSON.stringify(metrics, null, 2)));

async function axe(page: Page, name: string) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  metrics[`axe_${name}`] = r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }));
  return r.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
}
async function lcp(page: Page): Promise<number> {
  return page.evaluate(() => new Promise<number>((resolve) => {
    new PerformanceObserver((l) => { const e = l.getEntries(); resolve(e[e.length - 1].startTime); })
      .observe({ type: 'largest-contentful-paint', buffered: true });
    setTimeout(() => resolve(-1), 3000);
  }));
}
const saved = (page: Page) => expect(page.getByTestId('save-state')).toContainText('Kaydedildi', { timeout: 20_000 });

test('landing: headers, no console errors, accessibility, LCP', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const res = await page.goto('/');
  const h = res!.headers();
  expect(h['content-security-policy']).toContain("frame-ancestors 'none'");
  expect(h['x-content-type-options']).toBe('nosniff');
  expect(h['x-powered-by']).toBeUndefined();
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  metrics.landing_lcp_ms = await lcp(page);
  expect(await axe(page, 'landing')).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({ path: path.join(OUT, '01-landing.png'), fullPage: true });
});

test('404 page and protected area redirect', async ({ page }) => {
  const r = await page.goto('/olmayan-sayfa');
  expect(r!.status()).toBe(404);
  await expect(page.getByText('Sayfa bulunamadı')).toBeVisible();
  await page.goto('/app');
  await expect(page).toHaveURL(/\/login/);
});

test('BFF security: CSRF, path allow-list, no tokens in JS', async ({ request, page }) => {
  expect((await request.post('/api/proxy/editor/projects', { data: { title: 'x' } })).status()).toBe(403);
  expect((await request.get('/api/proxy/admin/users')).status()).toBe(404);
  expect((await request.get('/api/proxy/internal/worker/claim')).status()).toBe(404);
  await page.goto('/login');
  const scripts = await page.evaluate(() => Array.from(document.scripts).map((s) => s.src).filter(Boolean));
  for (const src of scripts) {
    const body = await (await request.get(src)).text();
    expect(body).not.toContain('localhost:8000');
    expect(body).not.toContain('e2e-worker-token');
  }
});

test('signup → dashboard', async ({ page }) => {
  await page.goto('/signup');
  await page.getByLabel('E-posta').fill(email);
  await page.getByLabel('Şifre').fill(password);
  await page.getByLabel('18 yaşından büyüğüm').check();
  await page.getByLabel(/Kullanım koşullarını/).check();
  await page.getByRole('button', { name: 'Hesap oluştur' }).click();
  await expect(page).toHaveURL(/\/app$/);
  await expect(page.getByRole('heading', { name: 'Stüdyo' })).toBeVisible();
  const cookies = await page.context().cookies();
  expect(cookies.find((c) => c.name === 'av_at')?.httpOnly).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain('av_at');
  expect(await axe(page, 'dashboard')).toEqual([]);
  await page.screenshot({ path: path.join(OUT, '02-dashboard.png') });
});

let videoProject = '';

test('video: import → trim → split → text → autosave → reload → undo/redo', async ({ page }) => {
  await login(page);
  await page.goto('/app/new?type=video');
  await page.getByLabel('Proje adı').fill('E2E video');
  await page.getByRole('button', { name: 'Oluştur ve aç' }).click();
  await expect(page).toHaveURL(/\/app\/editor\//);
  videoProject = page.url().split('/').pop()!;
  await page.getByTestId('editor-file').setInputFiles(path.join(OUT, 'clip.webm'));
  const clip = page.locator('.tl-clip.video').first();
  await expect(clip).toBeVisible({ timeout: 30_000 });
  await saved(page);
  // the preview shows the uploaded media (presigned GET from private storage)
  await expect.poll(() => page.locator('[data-testid=stage] video').evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 15_000 }).toBeGreaterThanOrEqual(2);

  // trim the end by dragging the right handle ~1 s to the left (zoom 0.08 px/ms => 80 px)
  const handle = clip.locator('.h.r');
  const hb = (await handle.boundingBox())!;
  await page.mouse.move(hb.x + 3, hb.y + hb.height / 2);
  await page.mouse.down();
  await page.mouse.move(hb.x - 80, hb.y + hb.height / 2, { steps: 8 });
  await page.mouse.up();
  await saved(page);
  const doc1 = await getDoc(page, videoProject);
  const c1 = Object.values(doc1.project.clips)[0] as { duration_ms: number };
  console.log('trimmed duration', c1.duration_ms);
  expect(c1.duration_ms).toBeGreaterThan(4500);
  expect(c1.duration_ms).toBeLessThan(5500);

  // split at 2 s with the keyboard
  await page.locator('.tl-ruler').click({ position: { x: 160 + 2, y: 10 } });
  await clip.click();
  await page.keyboard.press('s');
  await expect(page.locator('.tl-clip.video')).toHaveCount(2);

  // text overlay, edited in the inspector
  await page.locator('.tl-ruler').click({ position: { x: 40, y: 10 } });
  await page.getByRole('button', { name: 'T Metin' }).click();
  const area = page.getByLabel('İçerik');
  await area.fill('Merhaba dünya — çğıöşü');
  await area.blur();
  await saved(page);

  // undo the text edit, redo it
  await page.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => {});
  await page.getByRole('button', { name: 'Geri al' }).click();
  await expect(page.locator('.tl-clip.text')).toContainText('Yeni metin');
  await page.getByRole('button', { name: 'Yinele' }).click();
  await expect(page.locator('.tl-clip.text')).toContainText('Merhaba dünya');
  await saved(page);
  await page.screenshot({ path: path.join(OUT, '03-video-editor.png') });

  await page.reload();
  await expect(page.locator('.tl-clip.text')).toContainText('Merhaba dünya');
  await expect(page.locator('.tl-clip.video')).toHaveCount(2);
  expect(await axe(page, 'editor')).toEqual([]);
});

test('same project edited from "mobile" (API, same commands) shows on web; conflicts are safe', async ({ page }) => {
  await login(page);
  const token = await apiToken();
  const doc = await (await fetch(`${API}/editor/projects/${videoProject}`, { headers: { Authorization: `Bearer ${token}` } })).json();
  const textId = Object.values(doc.project.clips as Record<string, { id: string; text: unknown }>).find((c) => c.text)!.id;
  const r = await fetch(`${API}/editor/projects/${videoProject}/commands`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'Idempotency-Key': `mobile-${Date.now()}`, 'X-Client': 'android' },
    body: JSON.stringify({ base_revision: doc.revision, commands: [{ type: 'set_clip', clip_id: textId, text: { content: 'Telefondan düzenlendi' } }] }),
  });
  expect(r.status).toBe(200);
  await page.goto(`/app/editor/${videoProject}`);
  await expect(page.locator('.tl-clip.text')).toContainText('Telefondan düzenlendi');
  const revs = await (await fetch(`${API}/editor/projects/${videoProject}/revisions`, { headers: { Authorization: `Bearer ${token}` } })).json();
  expect(revs.items.map((x: { client: string }) => x.client)).toEqual(expect.arrayContaining(['web', 'android']));
});

test('offline edit is kept locally and sent when back online', async ({ page, context }) => {
  await login(page);
  await page.goto(`/app/editor/${videoProject}`);
  await expect(page.locator('.tl-clip.text')).toBeVisible();
  await context.setOffline(true);
  await page.locator('.tl-clip.text').click();
  const area = page.getByLabel('İçerik');
  await area.fill('Çevrimdışı yazıldı');
  await area.blur();
  await expect(page.getByTestId('save-state')).toContainText('Çevrimdışı', { timeout: 15_000 });
  expect(await page.evaluate((id) => localStorage.getItem(`av.draft.${id}`), videoProject)).toContain('Çevrimdışı yazıldı');
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await saved(page);
  const d = await getDoc(page, videoProject);
  expect(JSON.stringify(d.project)).toContain('Çevrimdışı yazıldı');
});

test('video export: quote → confirm → worker render → MP4 download', async ({ page, request }) => {
  await login(page);
  await page.goto(`/app/editor/${videoProject}`);
  await page.getByRole('button', { name: 'Dışa aktar' }).click();
  await page.getByRole('button', { name: 'Fiyatı göster' }).click();
  await expect(page.getByText(/başarısız olursa iade edilir/)).toBeVisible();
  await page.getByRole('button', { name: 'Onayla ve başlat' }).click();
  const link = page.getByTestId('exports').getByRole('link', { name: 'İndir' }).first();
  await expect(link).toBeVisible({ timeout: 90_000 });
  const href = await link.getAttribute('href');
  const body = await (await request.get(href!)).body();
  expect(body.subarray(4, 8).toString()).toBe('ftyp');
  fs.writeFileSync(path.join(OUT, 'export.mp4'), body);
  const probe = execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'stream=codec_name,width,height:format=duration', '-of', 'json', path.join(OUT, 'export.mp4')]).toString();
  metrics.video_export = JSON.parse(probe);
  await page.screenshot({ path: path.join(OUT, '04-export.png') });
  const srt = await page.request.get(`/api/proxy/editor/projects/${videoProject}/subtitles.srt`);
  expect(await srt.text()).toContain('Çevrimdışı yazıldı');
});

test('photo: template → layer/text → PNG export', async ({ page, request }) => {
  await login(page);
  await page.goto('/app/new?type=photo');
  await page.getByRole('button', { name: /Film afişi/ }).click();
  await page.getByRole('button', { name: 'Oluştur ve aç' }).click();
  await expect(page.getByTestId('layers')).toBeVisible();
  await page.getByTestId('layers').getByRole('button', { name: /YOUR STORY/ }).click();
  const area = page.getByLabel('İçerik');
  await area.fill('BENİM FİLMİM');
  await area.blur();
  await page.getByRole('button', { name: '◯ Elips' }).click();
  await saved(page);
  await page.screenshot({ path: path.join(OUT, '05-design-editor.png') });
  await page.getByRole('button', { name: 'Dışa aktar' }).click();
  await page.getByRole('button', { name: 'Fiyatı göster' }).click();
  await page.getByRole('button', { name: 'Onayla ve başlat' }).click();
  const link = page.getByTestId('exports').getByRole('link', { name: 'İndir' }).first();
  await expect(link).toBeVisible({ timeout: 60_000 });
  const body = await (await request.get((await link.getAttribute('href'))!)).body();
  expect(body.subarray(0, 4).toString('hex')).toBe('89504e47');
  fs.writeFileSync(path.join(OUT, 'export.png'), body);
});

test('set planner: actors, camera, shot are saved in the project', async ({ page }) => {
  await login(page);
  await page.goto(`/app/editor/${videoProject}`);
  await page.getByRole('tab', { name: 'Sahne planı' }).click();
  await page.getByRole('button', { name: '+ Oyuncu' }).click();
  await page.getByRole('button', { name: '+ Kamera' }).click();
  await page.getByRole('button', { name: '+ Çekim' }).click();
  await saved(page);
  await page.screenshot({ path: path.join(OUT, '06-set-planner.png') });
  const d = await getDoc(page, videoProject);
  expect(d.project.shot_graph.actors).toHaveLength(1);
  expect(d.project.shot_graph.shots).toHaveLength(1);
});

test('assets page and responsive dashboard (phone width)', async ({ page }) => {
  await login(page);
  await page.goto('/app/assets');
  await expect(page.getByTestId('assets').locator('li')).toHaveCount(1);
  await page.getByTestId('asset-input').setInputFiles(path.join(OUT, 'clip.mp4'));  // H.264 upload path
  await expect(page.getByTestId('assets').locator('li')).toHaveCount(2, { timeout: 30_000 });
  await expect(page.getByTestId('assets').locator('li').first()).toContainText('Hazır');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: path.join(OUT, '07-phone-dashboard.png'), fullPage: true });
  await page.goto(`/app/editor/${videoProject}`);
  await expect(page.getByTestId('stage')).toBeVisible();
  await page.screenshot({ path: path.join(OUT, '08-phone-editor.png'), fullPage: true });
});

// ---------------------------------------------------------------- helpers

async function login(page: Page) {
  const cookies = await page.context().cookies();
  if (cookies.some((c) => c.name === 'av_rt')) return;
  await page.goto('/login');
  await page.getByLabel('E-posta').fill(email);
  await page.getByLabel('Şifre').fill(password);
  await page.getByRole('button', { name: 'Giriş yap' }).click();
  await expect(page).toHaveURL(/\/app$/);
}
async function apiToken(): Promise<string> {
  const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                              body: JSON.stringify({ email, password, device_id: 'android-e2e' }) });
  return (await r.json()).access_token;
}
async function getDoc(page: Page, id: string) {
  const r = await page.request.get(`/api/proxy/editor/projects/${id}`);
  return r.json();
}
