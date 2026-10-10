# V8 Durum Raporu: AI Cinema Studio (Web + iOS + Android, Vercel)

Tarih: 2026-10-10 · Dal: `claude/practical-dirac-zcl83d` · Commit'ler: `9cdb2d8` (çekirdek), `439e5bb` (web), `e289d40` (mobil)

Etiketler:
- **DONE:** Kod yazıldı ve gerçek testle doğrulandı.
- **PARTIAL:** Çalışıyor, ancak kapsamı veya doğrulaması eksik.
- **BLOCKED:** Senden bir erişim, anahtar ya da karar gerekiyor.

Bu sürümde hiçbir AI sağlayıcısı kullanılmadı. Dışa aktarma gerçek ffmpeg ile yapılıyor; bu bir mock değil.

## 1. Özet

| Faz | Durum | Not |
|---|---|---|
| A: Denetim ve eşleşme matrisi | DONE | `docs/V8_AUDIT_AND_PLAN.md`, ADR-1…6 |
| B: Web temeli | DONE (yerel) / BLOCKED (Vercel) | Next.js 16.3.8. Yerelde E2E ile doğrulandı; Vercel'e dağıtım yapılamadı |
| C: Ortak editör çekirdeği | DONE | `packages/shared` (TS) ve Python portunun birebir eşleştiği fixture'larla kanıtlandı |
| D: Canva benzeri tasarım editörü | PARTIAL | Katmanlar, metin, şekil, şablon ve PNG/JPEG/WebP çıktısı var. Filtre, kırpma arayüzü ve hizalama kılavuzları yok |
| E: CapCut benzeri video editörü | PARTIAL | Çok izli zaman çizelgesi, kırpma/bölme/taşıma, anahtar kare, ses ve altyazı var. Sunucu tarafı proxy önizleme ve dalga formu yok |
| F: Film Fabrikası + sahne planı | PARTIAL | Web'de yapım listesi, bütçe ve bölümden kurgu projesi var. Sahne planı 2D üstten görünüm; 3D yok |
| G: Sağlayıcı, TTS, dudak senkronu, faturalama, gözlem, yük testi | BLOCKED / yapılmadı | Bölüm 4'e bak |

## 2. Yapılanlar

### 2.1 Ortak çekirdek: `packages/shared` (DONE)

- **Kanonik proje şeması `cp1`:**
  - Tek proje türü video, fotoğraf, sosyal, film ve bölüm için kullanılıyor.
  - Projede izler, klipler, varlıklar, anahtar kareler ve geçişler var.
  - Diyalog ve karakter referansları da şemada.
  - Sahne grafiğinde oyuncu, kamera (lens mm), ışık ve çekim listesi var.
- **Komut motoru:**
  - 24 komut ve her biri için tam ters komut (geri al/yinele).
  - Çakışma, kilit ve değer aralığı kuralları.
  - Hız değişince süre de değişiyor.
- **Render manifesti `rm1`** ve SRT/VTT altyazı çıktısı.
- **`preview.ts`:** Web ve mobil önizleme, renderer ile aynı yerleşim kurallarını kullanıyor.
- **`sync.ts`:** Web ve mobil ortak.
  - Otomatik kayıt.
  - Aynı Idempotency-Key ile tekrar deneme: kaybolan bir yanıt iki kez uygulanmaz.
  - 409 durumunda yeniden temellendirme (rebase); uygulanamayan düzenlemeler kullanıcıya raporlanır.
  - Çevrimdışı taslak.
- **Eşleşme kanıtı:** TS motoru 14 senaryodan fixture üretiyor. Python portu (`services/api/app/services/editor_engine.py`) aynı proje JSON'unu, manifesti, hata kodlarını ve geri alma sonuçlarını birebir üretiyor.

### 2.2 API ve worker (DONE)

- **`/editor/*` uç noktaları:**
  - Projeler, komut grupları ve revizyonlar.
  - Optimistic concurrency: farklı öğelere dokunan eşzamanlı düzenlemeler sunucuda rebase edilir; aynı öğeye dokunanlar 409 döner.
  - Restore, manifest, altyazı.
  - Varlıklar: presigned PUT ile doğrudan depolamaya yükleme ve nesne düzeyinde ACL.
  - Fiyat teklifi, onaylı render ve dışa aktarmalar.
- **Kredi:** Önce ayrılır (reserve), başarılı olunca kesinleşir (settle), başarısız olunca iade edilir (release). Kayıtlar yalnızca eklenir (append-only).
- **Worker (`editor_renderer`):**
  - MP4/H.264, HEVC, WebM/VP9, PNG, JPEG ve WebP çıktısı.
  - Anahtar kareler, renderer'da önizlemedeki interpolasyonla aynı ifadelerle uygulanıyor.
  - Ücretsiz planda filigran var.
- **Bu fazda bulunup düzeltilen hatalar:**
  - Şekiller tuvali kaplayacak şekilde büyütülüyordu. Düzeltildi ve test eklendi.
  - Bir istemci `X-Client: restore` göndererek sunucunun ayrılmış etiketini taklit edebiliyordu (bu etiket çakışma kurallarını değiştiriyor). Düzeltildi ve test eklendi.

### 2.3 Web: `apps/web` (yerelde DONE)

- **BFF (sunucu tarafı ara katman):**
  - Token'lar httpOnly çerezlerde tutuluyor; tarayıcıya hiç gitmiyor.
  - CSRF koruması: Origin kontrolü ve özel başlık.
  - Proxy yalnızca izin verilen yolları iletir; `admin` ve `internal` yollarına erişilemez.
  - `API_BASE_URL` yalnızca sunucu tarafında; `NEXT_PUBLIC_*` değişkeni kullanılmıyor.
- **DEMO modu:** API yoksa bunu açıkça belirten bir şerit gösteriliyor. Projeler yalnızca o tarayıcıda saklanıyor; yükleme ve dışa aktarma kapalı.
- **Sayfalar:**
  - Açılış (sahte kullanıcı sayısı yok), giriş, kayıt.
  - Panel: son projeler ve 8 başlangıç kutusu.
  - Yeni proje sihirbazı (şablonlu).
  - Varlık yöneticisi (sürükle-bırak, ilerleme göstergesi).
  - Film & Dizi ve Dijital oyuncular.
  - 404 ve hata sayfası.
- **Editör:**
  - Önizleme sahnesi: sürükleyerek konumlandırma.
  - Zaman çizelgesi:
    - Taşıma (uyumlu izler arasında da).
    - İki uçtan kırpma.
    - Kenarlara, oynatma başına ve sıfıra yapışma; yakınlaştırma.
    - Bölme ve boşluksuz silme.
    - İz gizleme, sessize alma ve kilitleme.
  - Denetçi paneli:
    - Metin, şekil, dönüşüm.
    - Anahtar kare ekleme ve yumuşatma seçimi.
    - Hız, ters oynatma, geçiş.
    - Ses seviyesi ve fade.
  - Klavye kısayolları.
  - Tasarım modu: katman sırası, gizleme ve tuval ayarları.
  - Dışa aktarma: önce fiyat, sonra açık onay, worker render ve indirme. SRT/VTT altyazı da indirilebilir.
  - Sahne planı (2D): oyuncu, kamera ve ışık sürüklenebilir; lense göre görüş açısı hesaplanır; kamera görünümü geometrik izdüşümle önizlenir; çekim listesi var. Kamera konumunun video sağlayıcısınca korunacağı **iddia edilmiyor**.
- **Güvenlik başlıkları:** CSP, `frame-ancestors 'none'`, nosniff, Referrer-Policy, Permissions-Policy. `X-Powered-By` gönderilmiyor.

### 2.4 Mobil: `apps/mobile` (PARTIAL)

- **Yapılan:**
  - Editör proje listesi ve editör ekranı, web ile aynı motor, oturum ve senkron kodunu kullanıyor.
  - Çevrimdışı taslaklar cihazın dosya sisteminde tutuluyor.
  - Medya, galeriden seçilip presigned PUT ile yükleniyor.
  - Klip işlemleri: bölme, kırpma, taşıma, silme, metin düzenleme.
  - Geri al/yinele ve onaylı dışa aktarma.
  - `X-Client` başlığı platforma göre `ios` ya da `android`.
- **Doğrulanan:**
  - `tsc` temiz; birim testleri 6/6.
  - `expo export` Android ve iOS paketlerini üretiyor ve paylaşılan motor bu paketlerin içinde (doğrulandı).
- **Eksik:**
  - Simülatörde ya da cihazda çalıştırılmadı.
  - Zaman çizelgesi sürükleme hareketleri yok; düzenleme ±0,5 sn düğmeleriyle yapılıyor.
  - Önizleme yaklaşık; oynatma yok, yalnızca konum seçilebiliyor.
  - `expo lint` çalışmadı: projede ESLint yapılandırması yok ve otomatik kurulum ağ politikası nedeniyle engellendi.

## 3. Test sonuçları (bu oturumda çalıştırıldı)

| Paket | Sonuç |
|---|---|
| API (pytest, Postgres) | **160/160** (V3–V7'den 142 + V8 editör ve motor eşleşme testleri 18) |
| Worker (pytest, ffmpeg) | **32/32** |
| `packages/shared` (node:test + tsc) | **9/9**, tsc temiz |
| Web birim testleri (sync) | **4/4**, `tsc` ve `next build` temiz |
| Web E2E (Playwright, gerçek API + S3 uyumlu depolama + ffmpeg worker) | **12/12** |
| Mobil | `tsc` temiz, **6/6**, `expo export` Android/iOS OK |
| Ruff | Temiz |

**E2E'de doğrulanan senaryolar:**
- Kayıt ol → video projesi → video içe aktar → kırp → böl → metin ekle → otomatik kayıt → sayfayı yenile (veri korunuyor) → geri al/yinele.
- Aynı proje "Android" istemcisinden aynı komutlarla değiştiriliyor; değişiklik web'de görünüyor ve revizyonlarda `web` ile `android` istemcileri ayrı ayrı görünüyor.
- Çevrimdışı düzenleme yerelde tutuluyor; bağlantı gelince gönderiliyor.
- MP4 dışa aktarma: ffprobe ile h264 720×1280 + aac, 4,97 sn.
- Afiş şablonu → metin ve şekil → PNG dışa aktarma.
- Sahne planı projeye kaydediliyor.
- Telefon genişliğinde yatay taşma yok.
- DEMO modu.
- BFF güvenliği: CSRF koruması ve yol izin listesi; JS paketlerinde API adresi ya da token yok.

**Ölçümler** (yerel production build, masaüstü Chromium; Vercel ölçümü değil):
- Açılış sayfası LCP: 104 ms.
- axe (WCAG 2 A/AA): açılış, panel ve editörde kritik ya da ciddi ihlal **0**.
- Testte bulunup düzeltilen hatalar:
  - Kontrast: ana buton 3,92:1 idi (≥4,5:1 gerekli). Marka rengi ve açık temada yeşil durum yazısının kontrastı da yetersizdi.
  - Bir sürükleme hareketi iki kez işleniyordu; kırpma iki kat uygulanıyordu.
- Not: Playwright'ın Chromium'u H.264 çözemiyor. Tarayıcı önizlemesi VP9 WebM ile test edildi. MP4 yükleme ve H.264 dışa aktarma ayrıca doğrulandı.

## 4. BLOCKED: Senden gerekenler

1. **Vercel Preview dağıtımı.** İkisinden biri yeterli:
   - **(a) Bu ortamdan dağıtım:**
     - Ortamın ağ ayarlarında `api.vercel.com` ve `vercel.com` alan adlarına izin ver: oturum başlığındaki bulut ortamı menüsü → Edit → Network access → Allowed domains. Belgeler: https://code.claude.com/docs/en/cloud-environments#network-access
     - Dağıtım yetkili bir Vercel token'ını ortam ayarlarında `VERCEL_TOKEN` adıyla gizli değişken olarak ekle. Yeni bir oturum bunu okur.
     - Token'ı sohbete yapıştırma.
   - **(b) Vercel panelinden bağlama:**
     - Bu GitHub deposunu Vercel'e bağla. Root Directory: `apps/web`.
     - "Include files outside root directory" açık kalmalı, çünkü `packages/shared` kullanılıyor.
     - Ortam değişkenleri: `API_BASE_URL` (yalnız sunucu) ve `STORAGE_ORIGIN`.
     - API bağlanmadan da Preview DEMO modunda açılır.
2. **Yayındaki API ve depolama:**
   - Herkese açık HTTPS adresinde bir FastAPI.
   - Postgres, Redis ve S3 (bucket CORS ayarında web origin'i tanımlı).
   - ffmpeg worker.
   - Bunlar olmadan Vercel yalnızca DEMO modunda çalışır.
3. **AI araçları:** Konuşmadan altyazı, seslendirme/TTS, dudak senkronu, arka plan veya nesne silme, büyütme ve görselden videoya üretim kapalı (`/editor/config` yanıtında `not_available`). Her biri için sağlayıcı seçimi ve anahtar gerekiyor.
4. **Faz G:** Gerçek sağlayıcı testi, gerçek ödeme ve faturalama, gözlemlenebilirlik (Sentry/OTel hesapları), yük testi için hedef ortam ve kademeli yayın kararı.

## 5. Kalan eksikler (kod)

- **Video editörü:**
  - Sunucu tarafı proxy önizleme (düşük çözünürlüklü H.264/VP9 kopya) üretilmiyor. Safari ve Chrome orijinal dosyayı oynatabiliyor; ama ağır 4K dosyalar ve açık kaynak tarayıcılar için gerekli.
  - Dalga formu ve küçük resim şeridi yok.
  - Parçalı ve kaldığı yerden devam eden (multipart/resumable) yükleme yok; tek PUT ile en fazla 2 GB.
  - Ses kısma (ducking), ritme göre kesme, efekt ve LUT, chroma key yok.
- **Tasarım editörü:**
  - Kırpma arayüzü yok (şemada `crop` alanı var; renderer'da desteklenmiyor).
  - Filtreler, hizalama kılavuzları ve çoklu seçim/gruplama arayüzü yok. Gruplama motorda var.
- **3D sahne:** Gerçek 3D görünüm yok; 2D üstten plan ve izdüşüm var. Sağlayıcının kamera konumunu ne kadar koruduğunu ölçen bir sapma metriği yok.
- **Canlı ortak çalışma** (aynı anda imleç görme, CRDT) yok. Eşzamanlı düzenlemeler revizyon ve rebase ile güvenli, ama gerçek zamanlı değil.
- **Mobil:**
  - Cihaz ve simülatör testi yapılmadı.
  - Sürükleme hareketleri ve oynatmalı önizleme yok.
  - ESLint yapılandırması yok.
- **Web'deki Film Fabrikası:** Yalnızca özet, bütçe ve kurgu projesi açma var. Senaryo, replik ve süreklilik ekranları mobilde.
- **Lighthouse ve Vercel ortamında performans ölçümü:** Dağıtım engelli olduğu için yapılamadı.
