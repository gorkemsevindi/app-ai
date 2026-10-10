# V8 — Unified Creative Studio (Web + iOS + Android, Vercel): Faz A denetimi ve plan

Kaynak: `docs/MASTER_SPEC_V8_VERCEL.docx`. V3–V7 korunur; YourStars/`drama/` değiştirilmez.

## 1. Envanter (denetim anı)

| Katman | Durum |
|---|---|
| **Backend (FastAPI)** | 234 API işlemi (OpenAPI'den sayıldı). Dağılım: admin 56, productions 33, studio 31, characters 23, creator 12, generations 10, source-videos 7, internal 6, auth 6, identity-profiles 6, life-stories 6, actors 6, me 5, … |
| **Worker** | Mock/gerçek adaptörler, Studio çekim + montaj (ffmpeg), animatic kartları, karakter görselleri, çok kişilik pipeline |
| **Mobil (Expo SDK 57, Router)** | 24 ekran: sekmeler (keşfet, video, studio, kütüphane, profil), şablon, iş, çok kişi, kimlik, yaratıcı, karakterler, yapımlar, diyalog, hayat hikâyesi, ödeme, giriş |
| **Web** | **Yok.** `drama/` bağımsız YourStars projesi; kullanılmaz. |
| **Ortak paket** | **Yok.** Mobil, API istemcisini kendi içinde barındırıyor. |
| **Genel amaçlı video/foto editörü** | **Yok.** Studio storyboard düzenlemesi ve V7 replik editörü var. Çok izli timeline, katmanlı tasarım tuvali ve keyframe yok. |
| **Vercel** | Repo bağlantısı yok. Ortamda `VERCEL_TOKEN` yok. `api.vercel.com` ağ politikası tarafından reddediliyor (CONNECT 403) → **BLOCKED** |

`EKSIKLER.md` ve `V7_DURUM_RAPORU.md` bulguları doğrulandı:
- TTS, dudak senkronu ve dublaj sağlayıcısı yok.
- Gerçek video sağlayıcı doğrulaması yok.
- Çıktı moderasyonu dev ortamında null.
- Yayın akışı kapalı.

Yukarıdaki maddeler V8'de de **BLOCKED** kalır.

## 2. Parite matrisi (hedef ve bu faz sonundaki durum)

| Yetenek | API | Web | Mobil |
|---|---|---|---|
| Kimlik doğrulama (kayıt/giriş/çıkış) | var | **V8** (BFF, httpOnly çerez) | var |
| Dashboard (son projeler + giriş kartları) | var | **V8** | var (V7 kartları) |
| Kanonik editör projesi (video/foto/sosyal) aç/kaydet, revizyon, geri al/yinele | **V8** | **V8** | **V8** |
| Varlık yöneticisi (imzalı yükleme) | **V8** | **V8** | **V8** (resim/video seç) |
| Video editörü: çok iz, trim, split, ripple, taşı, keyframe, metin, ses | **V8** (komut motoru) | **V8** | **V8** (temel komutlar, mobil arayüz) |
| Tasarım editörü: tuval, katman, metin, şekil, hizala | **V8** | **V8** | **V8** (katman listesi ve özellikler) |
| Final render: MP4/H.264, PNG/JPEG/WebP, SRT/VTT | **V8** (worker ffmpeg) | tetikleme | tetikleme |
| Film Factory (V7) | var | **V8** ekranları | var |
| 3D set / storyboard (2D üstten görünüm + 3D önizleme) | **V8** (şema → çekim kamerası) | **V8** | yalnızca okuma (PARTIAL) |
| AI araçları: inpaint, arka plan silme, upscale, STT, TTS, dudak senkronu | capability flag | flag'e bağlı, kapalı | flag'e bağlı, kapalı |

## 3. Mimari kararları (ADR)

1. **ADR-1, Monorepo yapısı.**
   - `apps/web` (Next.js App Router, TS) ve `packages/shared` (saf TS, bağımlılıksız) eklendi.
   - Mobil, `packages/shared` paketini tsconfig yolu ve Metro `watchFolders` ile kullanır.
   - Kök npm workspaces **açılmadı**: mobilin kendi kilit dosyası ve EAS derlemesi bozulmasın.
2. **ADR-2, Sunucu otoritesi.**
   - Kanonik proje JSON'unun tek doğruluk kaynağı API'dir. İstemciler komut gönderir: `base_revision` + `idempotency_key`.
   - Sunucu komutları Python'daki **aynı motorla** uygular.
   - TS ve Python motorları, ortak `packages/shared/fixtures` sözleşme testleriyle eşit tutulur (snapshot/contract).
3. **ADR-3, Çakışma çözümü.**
   - `base_revision` eskiyse sunucu, aradaki komutlar istemcinin komutlarıyla aynı nesneye dokunmadığı sürece yeniden uygular (rebase).
   - Dokunuyorsa 409 `revision_conflict` döner. İstemci yeni sürümü alır, kendi bekleyen komutlarını yeniden uygular ya da kullanıcıya sorar.
   - Çok kullanıcılı canlı ortak çalışma opsiyonel ve sonraki faza bırakıldı.
4. **ADR-4, Render.**
   - Final render deterministik olarak worker'da ffmpeg ile yapılır (yeni iş türü `editor_render`, mevcut kuyruk, kiralama/lease ve kredi akışı).
   - Vercel Functions'ta render, GPU veya uzun iş yok.
   - Web önizlemesi tarayıcıda DOM/Canvas/HTML5 video ile yapılır: yaklaşık önizlemedir ve öyle etiketlenir.
5. **ADR-5, Web güvenliği.**
   - Next.js yalnızca frontend + BFF'dir. Erişim/yenileme tokenları httpOnly, Secure, SameSite=Lax çerezlerde tutulur.
   - BFF API'ye sunucu tarafında `API_BASE_URL` ile bağlanır.
   - Değiştiren isteklerde Origin kontrolü ve özel başlık zorunludur (CSRF).
   - `NEXT_PUBLIC_*` yalnızca herkese açık değerler içerir.
   - Backend'e ulaşılamazsa arayüz **DEMO** etiketi gösterir; sahte render veya ödeme yapılmaz.
6. **ADR-6, AI araçları.** Her AI aracı capability flag ile gerçek bir sağlayıcıya bağlanır. Sağlayıcı yoksa araç "not available" olarak görünür; asla taklit edilmez.

## 4. Vercel — engel ve gerekenler

- **Ağ:** `api.vercel.com` (ve `vercel.com`) ortamın izinli alan adlarına eklenmeli.
- **Kimlik bilgisi:** deploy yetkili bir Vercel token'ı ortam değişkeni olarak eklenmeli (`VERCEL_TOKEN`).
- **Alternatif (önerilen):** kullanıcı Vercel panelinden GitHub reposunu bağlar, Root Directory `apps/web` seçer. Her push otomatik Preview deploy üretir.
  - Ortam değişkenleri: `API_BASE_URL` (server-only, gerçek FastAPI adresi) ve `NEXT_PUBLIC_APP_NAME`.
- **API tarafı:** API'de `CORS_ORIGINS` ve çerez alan adı ayarı gerekir. BFF kullandığımız için tarayıcı API'ye doğrudan gitmez; CORS yalnızca BFF olmayan istemciler için önemlidir.
