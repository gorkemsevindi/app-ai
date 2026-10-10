# V7 Durum Raporu — AI Cinema & Short Drama Factory

- **Kaynak:** `docs/MASTER_SPEC_V7_FILM_DRAMA_FACTORY.docx`
- **Denetim ve plan:** `docs/V7_AUDIT_AND_PLAN.md`
- **Dal:** `claude/practical-dirac-zcl83d`

V7 mevcut uygulamanın içine eklendi: aynı FastAPI backend, aynı worker, aynı Expo uygulaması. Ayrı bir uygulama, ayrı bir backend ya da ayrı bir sağlayıcı yönlendiricisi kurulmadı. YourStars/`drama/` projesine dokunulmadı. V3–V6 regresyon testleri yeşil (sayılar §6'da).

> **Dürüstlük notu:** Bu ortamda gerçek bir AI video, ses (TTS) veya dudak senkronu sağlayıcısı çalışmadı. Tüm uçtan uca testler mock sağlayıcılarla yapıldı: `mock_t2v` sentetik video, montaj ise gerçek ffmpeg. Mock çıktılar dışa aktarımda `test_mode: true` olarak işaretleniyor; hiçbiri "üretim başarısı" sayılmıyor.

---

## 1. Mimari özet: neyin üzerine kuruldu?

| V7 parçası | Nasıl uygulandı |
|---|---|
| Dört giriş akışı (Dizi, Film, Yıldız, Hayat Hikâyem) | Hepsi tek `productions` tablosu ve tek altyapı. **Her bölüm bir Studio projesi:** V4'ün render, içerik özeti (hash) ile tekrar kullanım, düzenleme, geri alma; V6'nın karakter kimliği kilidi ve V5'in yönlendirmesi aynen kullanılıyor. |
| Senaryo → sezon/bölüm → sahne → çekim | Yeni **senaryo ayrıştırıcı ve çekim planlayıcı** (`screenplay.py`). Kural tabanlı, AI değil; replikleri birebir kopyalıyor. |
| 10–30 dk bölümler | Bölüm başına format sınırı (en çok 30 dk, %10 tolerans). Bir bölüm 200+ kısa çekimden ve tek montajdan oluşuyor. 30 dk'dan uzun filmler otomatik olarak parçalara bölünüyor. |
| Exact Dialogue | Replik nesneleri kalıcı kimlikli; sürümlü storyboard içinde saklanıyor. Kurmaca içerik politikası var; replik editörü önce etki analizini gösteriyor. |
| Seçili aralık düzenleme | 01:12–01:17 gibi bir aralık etkilediği çekimlere eşleniyor. Yalnızca o çekimler yeniden üretiliyor, diğerleri tekrar kullanılıyor. |
| Hikâye hafızası | Proje İncili (bible), olaylar, dünya durumu, hikâye dalları, süreklilik denetimi, etki önizlemesi (`continuity.py`). |
| Budget Director | Profiller, çekim bazlı maliyet kırılımı, maliyet aralığı, para birimi (Decimal, kuruş cinsinden), fizibilite ve alternatifler, onay ve **kesin tavan** (`budget.py`). |
| Önce önizleme | Ücretsiz animatic (storyboard kartları) → 30–60 sn ücretli pilot → tam render. Pilot çekimleri tam render'da ücretsiz tekrar kullanılıyor. |
| Olaylar | Transactional outbox (`production_events`) ve zamanlayıcıdaki `production_outbox` görevi. |

---

## 2. V7 DONE / PARTIAL / BLOCKED matrisi

**Durum anahtarı:**
- **DONE:** kodda var ve testle kanıtlandı (mock sağlayıcıyla).
- **PARTIAL:** çalışıyor ama bir kısmı eksik.
- **BLOCKED:** gerçek sağlayıcı, yasal inceleme ya da kullanıcı kararı bekliyor.

| # | Özellik | Durum | Kanıt / not |
|---|---|---|---|
| 1 | Dört giriş akışı tek altyapıda; ana ekranda "Hangi hikâyeyi anlatmak istersin?" ve 4 kart | **DONE** | `GET /productions/entry`, mobil `StoryEntry`; test A |
| 2 | Formatlar: mikro, kısa dizi, bölüm, uzun bölüm, kısa film, uzun film (parçalı) | **DONE** | format sınırları ve kotalar remote config'te; test A, B, C |
| 3 | 10–30 dk bölüm planlama ve parçalı render | **DONE** (mock) | 30 dk senaryo → 200+ çekim, ~30 dk (test C); 10 dk film (test B) |
| 4 | Görsel stiller (7 stil) + sahne bazlı stil ve yetenek matrisi | **DONE** (mock) | `STYLE_*` yetenek etiketleri; stili desteklemeyen sağlayıcıya render reddediliyor. Gerçek sağlayıcıların stil desteği doğrulanmadı. |
| 5 | Sahne sınıflandırma (konuşma/aksiyon/manzara/montaj/geçiş) + sınıfa göre maliyet | **DONE** | planlayıcı sınıflandırıyor; aksiyon çarpanı fiyata yansıyor |
| 6 | Exact Dialogue: kalıcı replik kimliği, duygu, söyleyiş, yoğunluk, telaffuz, ses kimliği, dil, kilit | **DONE** | test D, test E |
| 7 | Argo ve küfürlü kurmaca konuşma (yaş derecesine bağlı); hiçbir derecede izin verilmeyenler ayrı | **DONE** | kurmaca politika matrisi testi; argo altyazıda birebir korunuyor (test E) |
| 8 | Replik editörü: etki analizi, revizyon geçmişi, A/B varyantı, geri al/yinele | **DONE** | test D; geri al ve yinele (redo) Studio'ya da eklendi |
| 9 | Replik sesi (TTS), birden çok dil, dublaj | **BLOCKED** | TTS/dublaj sağlayıcısı yok. Replikler **altyazı** olarak oynuyor; "ses yok" etki analizinde ve notlarda açıkça yazıyor. |
| 10 | Gerçek dudak senkronu | **BLOCKED** | sağlayıcı yok. V3'ün template dudak senkronu bu akışa bağlı değil. |
| 11 | Seçili aralık düzenleme (01:12–01:17): ifade, kamera, hareket, altyazı | **DONE** (mock) | değişecek çekimler, maliyet, süre ve riskler gösteriliyor; yalnızca etkilenen çekim yeniden üretiliyor (test D) |
| 12 | Değişmez sürümler, bağımlılık geçersizleştirme, ücretsiz tekrar kullanım | **DONE** | V4 içerik özeti; test B (pilot tekrar kullanımı), test D |
| 13 | Ayrı izler + sürümlü zaman çizelgesi JSON'u (dışa/içe aktarma) | **DONE** | şema `v7-timeline-1`; eski sürüme içe aktarma reddediliyor |
| 14 | Proje İncili, olaylar, bölüm anlık görüntüleri, hikâye dalları, süreklilik denetimi, etki önizlemesi | **DONE** (kural tabanlı) | test F: "4. bölümde ölmesin" → dal; eski dal değişmiyor |
| 15 | Budget Director: profiller, kırılım, aralık, para birimi, fizibilite, alternatifler | **DONE** | test G ve para birimi hassasiyet testi |
| 16 | Onay + kesin tavan + aşımda yeni onay; iptal ve iade | **DONE** | test C, G, H |
| 17 | Gerçek sağlayıcı fiyatlarıyla tahmin | **BLOCKED** | fiyatlar ops tarafından girilmeli; varsayılan sağlayıcı ücretsiz mock |
| 18 | Önce önizleme: ücretsiz animatic, 30–60 sn pilot, aşamalı üretim | **DONE** (mock) | test A, B, C |
| 19 | İş durumları (draft → … → ready / partial_failed / failed / cancelled), ilerleme, harcanan ve iade edilen kredi | **DONE** | `status()`; testler A, B, H |
| 20 | Bölüm/sahne/çekim ilerlemesi, hata ve tekrar deneme raporu | **DONE** | episode endpoint'i ve `/budget` raporu |
| 21 | Olaylar (outbox) | **DONE** | test A |
| 22 | V6 karakter UUID'si, kimlik kilidi ve lisans entegrasyonu | **DONE** | yapım kadrosu V6 karakterleri; her bölüm V6 cast snapshot alıyor (test: zaman çizelgesi + V6 kadro) |
| 23 | Hayat Hikâyem: rehberli sorular, kronoloji, mahremiyet taraması, anonimleştirme, gerçek/kurmaca etiketi, senaryo önerisi, onay | **DONE** (kural tabanlı) | life-story testi |
| 24 | Paylaşım ve yayın | **BLOCKED** (bilinçli) | varsayılan olarak kapalı; `publish-check` engelleri listeliyor. Herkese açık yapım akışı yok. |
| 25 | Mobil: 4 kart, sihirbaz, bölüm listesi, senaryo, replik editörü, bütçe onayı, ilerleme, hayat hikâyesi | **PARTIAL** | ekranlar var; tip kontrolü ve çeviri testleri geçti. Cihazda denenmedi. Yatay profesyonel zaman çizelgesi ve çevrimdışı taslak kuyruğu yok. |
| 26 | Yaş derecesi ve yetişkin içerik (yalnızca yetişkinler) | **PARTIAL** | yaş beyanıyla korunuyor; gerçek yaş doğrulaması yok; bölgesel kapatma anahtarı var (`allow_mature`) |
| 27 | Önbellek, tekrar kullanım, sağlayıcı kapatma anahtarı, harcama tavanları | **PARTIAL** (V4–V5'ten) | İçerik özeti ile tekrar kullanım, kapatma anahtarı, hata oranına göre otomatik askıya alma (circuit breaker benzeri) ve günlük GPU bütçe tavanı var. Sağlayıcı başına rate limit ve üstel geri çekilmeli (exponential backoff) yeniden deneme **yok**: işler sıraya hemen geri dönüyor. |
| 28 | Gerçek sağlayıcıyla kontrollü smoke test | **BLOCKED** | anahtar ve doğrulanmış model kimlikleri yok |
| 29 | Kalite metrikleri (kimlik kayması, titreme, dudak senkronu sapması, diyalog WER) | **PARTIAL** | V6 kimlik ölçümü (proxy) ve süreklilik var; titreme, WER ve dudak senkronu ölçümü gerçek ses/video olmadan yok |

---

## 3. Kabul kontrol listesi (V7 §son)

- ☑ Dört giriş akışı tek proje altyapısına bağlı
- ☑ 10–30 dakikalık bölüm planlama ve parçalı render (mock sağlayıcı)
- ☑ Tüm görsel stiller yetenek matrisiyle sunuluyor (gerçek sağlayıcı stil desteği doğrulanmadı)
- ☑ Replik bazlı exact dialogue, argo ve duygu kontrolü (ses yok, altyazı var)
- ☑ Seçili sahne/çekim yeniden üretimi, geri al/yinele
- ☑ Series Memory olay ve karakter tutarlılığını izliyor (kural tabanlı)
- ☑ Bütçe tahmini, kullanıcı onayı, kesin tavan ve iade
- ☑ 30–60 saniyelik önizleme ve aşamalı render
- ☑ V6 karakter UUID'si, kimlik kilidi ve lisans entegrasyonu korunuyor
- ☑ Mock test ayrı raporlanıyor; **gerçek sağlayıcı testi: BLOCKED**
- ☐ App Store/Google Play, telif, rıza ve yaş derecelendirmesi kontrolü: aşağıdaki engellere bakın

## 4. Kabul senaryoları (V7 §13): sonuçlar

| Senaryo | Sonuç (mock sağlayıcı, gerçek worker + ffmpeg) |
|---|---|
| **A** — 10 bölümlük, 2 dk'lık dizi | ✅ 10 bölüm oluşturuldu. Bölüm 1: senaryo → plan → ücretsiz animatic → tahmin ve onay → render → hazır → dışa aktarma (`test_mode`) |
| **B** — 10 dk fotogerçekçi film | ✅ ~10 dk plan. 30–60 sn pilot üretildi ve montajlandı; tam render tahmininde pilot çekimler ücretsiz tekrar kullanıldı |
| **C** — 30 dk animasyon, aşamalı | ✅ 200+ çekim / ~30 dk plan. Profil farkları görüldü. Tavan küçük olduğu için tam render reddedildi, pilot tavan içinde üretildi |
| **D** — 01:12'de replik değişikliği | ✅ Etki: altyazı + "ses yok" + "dudak senkronu yok", 0 kredi, çekim yeniden üretimi yok. Revizyon geçmişi, kilit, geri al/yinele çalışıyor. 01:12–01:17 aralığı yalnızca ilgili çekimi yeniden üretiyor |
| **E** — Argo repliğin birebir korunması | ✅ "Siktir git lan, seni şerefsiz!" metni storyboard'da ve altyazıda birebir duruyor. Genel derecede reddedildi (`rating_required:teen`); hiçbir derecede izin verilmeyen içerik reddedildi |
| **F** — 4. bölümde hikâye değişikliği | ✅ Ölüm olayı 5. bölümde süreklilik hatası verdi. Onaylanmış 4. bölüm değiştirilemedi; dal açıldı. Eski dal korunurken yeni dalda hata çözüldü |
| **G** — 15 USD kesin tavan | ✅ 15 USD = 300 kredi (0,05 USD/kredi). "Fizibil değil" cevabı alternatiflerle geldi. Render rezervasyondan önce reddedildi; bakiye değişmedi |
| **H** — Kullanıcı iptali ve kredi iadesi | ✅ Sıradaki ve çalışan işler iptal edildi, krediler tam iade edildi, olay kaydı yazıldı |

---

## 5. Sağlayıcı durumu

| Yetenek | Mock testi | Gerçek sağlayıcı |
|---|---|---|
| Video çekimi (metinden videoya, karakter referanslı) | ✅ `mock_t2v` | Veo adaptörü var ama `VEO_MODEL`/anahtar ve fiyat girilmedi. Karakter referansı ve stil yetenekleri doğrulanmadı → **BLOCKED** |
| Karakter görselleri (V6) | ✅ `mock_image` | Gemini görsel adaptörü yazıldı, kapalı → **BLOCKED** |
| TTS / dublaj / dudak senkronu | — | **YOK** (sağlayıcı seçilmedi). Ürün altyazıyla çalışıyor ve bunu açıkça söylüyor |
| Montaj, altyazı, animatic | ✅ gerçek ffmpeg | gerçek |
| Çıktı moderasyonu (NSFW/çocuk) | null skorlayıcı (dev) | **BLOCKED**: üretimde lisanslı bir sınıflandırıcı şart |

## 6. Testler

- **API: 142/142 geçti.** 129 mevcut test (V3–V6 regresyonu) + 13 yeni V7 testi. V7 için 13 yeni test: 3 birim testi (senaryo ayrıştırıcı, kurmaca politika matrisi, para birimi hassasiyeti), A–H senaryoları, hayat hikâyesi + erişim kontrolü, zaman çizelgesi + V6 kadro.
- **Worker:** 25/25 geçti; yeni test: animatic kartları + Türkçe/argo altyazı.
- **Mobil:** `tsc` temiz, 6/6 geçti (en/tr çeviri eşitliği yeni anahtarları da kapsıyor).
- **Geçiş dosyası 0016:** ileri, geri ve tekrar ileri test edildi; `alembic check` temiz.

## 7. Mağaza yayını önündeki engeller (App Store / Google Play)

1. **Gerçek sağlayıcılar ve çıktı moderasyonu.** Üretimde NSFW/çocuk sınıflandırıcısı zorunlu; şu an null skorlayıcı var.
2. **Yaş derecesi.**
   - Yetişkin kurmaca diyalog için mağaza yaş sınıfı 17+ / Mature olmalı.
   - Gerçek yaş doğrulaması yok, yalnızca beyan var.
   - Bazı bölgelerde yetişkin içerik `allow_mature` ile kapatılmalı.
3. **Kullanıcı üretimli içerik kuralları.** Şikâyet ve engelleme template ve karakterlerde var, ama yapımlar için şikâyet uç noktası yok. Yayın kapalı olduğu sürece bu bir risk değil; yayın açılmadan önce eklenmeli.
4. **Hukuk metinleri.** Gizlilik politikası, kullanım şartları ve AI üretim beyanı metinleri, ayrıca karakter lisans koşullarının hukuk incelemesi gerekiyor.
5. **Gerçek kişiler.** Hayat hikâyesinde kişi onayı (`people_confirmed`) var; ses/görüntü yalnızca V6 rızalı karakterlerle kullanılabiliyor. Ünlü taklidi metin taramasıyla engelleniyor ama görsel benzerlik denetimi zayıf (dHash).
6. **Ödemeler.** Mağaza içi satın alma V4'te var; dış ödeme bağlantısı yok (uyumlu).
7. **Bildirimler.** Push bildirimi yok. Olaylar outbox'ta bekliyor; bildirim adaptörü eklenmeli.

## 8. İlk 100 / 1.000 / 10.000 kullanıcı için altyapı ve maliyet planı

**Varsayımlar** (doğrulanmamış, yalnızca planlama içindir):
- Aktif kullanıcı başına ayda 3 dk final video (pilotlar dahil yaklaşık 3,5 dk üretim).
- %15 tekrar deneme oranı.
- Video sağlayıcı fiyatı `p` USD/sn. **Gerçek fiyatlar ops tarafından güncel sağlayıcı dokümanlarından girilmeli.**
- Örnek olarak iki değer kullanıldı: p = 0,10 ve p = 0,40.

| | 100 kullanıcı | 1.000 kullanıcı | 10.000 kullanıcı |
|---|---|---|---|
| Aylık üretim (sn) | ~24.000 | ~240.000 | ~2.400.000 |
| Video sağlayıcı maliyeti (p = 0,10) | ~2.400 USD | ~24.000 USD | ~240.000 USD |
| Video sağlayıcı maliyeti (p = 0,40) | ~9.700 USD | ~97.000 USD | ~970.000 USD |
| API | 1 kapsayıcı (2 vCPU) | 2–3 kapsayıcı | 6–10, otomatik ölçeklenen |
| PostgreSQL | yönetilen, 2 vCPU / 4 GB | 4 vCPU / 16 GB + yedek | 8 vCPU / 32 GB + okuma kopyası + PgBouncer; iş/olay tablolarında arşivleme |
| Montaj worker'ı (CPU, ffmpeg) | 1 | 3–4, kuyruk derinliğine göre | 20+, kuyruk derinliğine göre |
| Depolama (4 Mbps, çekimler + final ≈ 3×) | ~30 GB/ay | ~300 GB/ay | ~3 TB/ay; yaşam döngüsü kuralları şart |
| Redis (rate limit) | isteğe bağlı | gerekli | gerekli (HA) |
| GPU | yok (API sağlayıcıları) | yok | Kendi modelimizi barındırmak yalnızca V5 model kayıt kapısından geçerse ve hacim gerekçelendirirse |

**Ekonomi:**
- Kullanıcı fiyatı kredi; kredi başına perakende fiyat ve vergi remote config'ten geliyor.
- Brüt marj = kredi geliri − sağlayıcı maliyeti − mağaza payı.
- Marj, ekonomi panosunda yapım bazında izleniyor.
- Kesin tavan ve çekim başı maliyet sınırları zarar riskini sınırlıyor.

## 9. Bilinen sınırlamalar ve sıradaki adımlar

1. Gerçek video sağlayıcısını (stil ve karakter referansı yetenekleriyle) ve fiyatlarını yapılandırıp kontrollü smoke test yapmak.
2. TTS ve dudak senkronu sağlayıcısı seçmek: metni değiştirmeyen, "exact" uyumlu bir sağlayıcı. Ardından replik sesi ve dudak senkronu entegrasyonu.
3. Çıktı moderasyon sınıflandırıcısı ve yetişkin içerik için yaş doğrulaması.
4. Planlayıcıyı AI ile zenginleştirmek. Şu an kural tabanlı; replikleri asla değiştirmeyen bir LLM planlayıcı eklenebilir.
5. Mobilde yatay profesyonel zaman çizelgesi, çevrimdışı taslak kuyruğu, push bildirimleri ve cihaz testleri.
6. Yapımlar için şikâyet uç noktası ve yayın akışı (şu an bilinçli olarak kapalı).
