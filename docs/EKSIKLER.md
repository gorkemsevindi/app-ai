# AI Video App: Eksikler listesi

**Kapsam:** Bu liste sadece AI Video App'i (`apps/mobile`, `services/*`) kapsar. YourStars (`drama/`) ayrı bir projedir ve eksikleri `drama/OZET.md` dosyasındadır.

**Kullanım:** Her aşamada güncellenir. İstendiğinde bu dosya verilir.

**Son güncelleme:** 2026-10-10 (Aşama C sonrası). Aşama D'de yeni eksik çıkarsa en alttaki bölüme eklenecek.

**Etiketler:**
- **[SEN]** Senin işin: anahtar, hesap, sözleşme veya ticari karar.
- **[KOD]** Kod işi: henüz yapılmadı.
- **[DOĞRULAMA]** Kod hazır ama gerçek ortamda denenmedi.
- **[HUKUK]** Hukuk incelemesi gerekiyor.

---

## 1. Senden gereken anahtarlar, hesaplar ve kararlar [SEN]

**Anahtar ve hesaplar.** Hiçbirini sohbete yapıştırma; ortam ayarlarına gizli değişken (secret) olarak gir.

| # | Ne | Neden |
|---|---|---|
| 1 | `GEMINI_API_KEY` | AI yönetmen, AI düzenleyici ve Veo üretimi |
| 2 | Model adları: `studio.director_model`, `studio.editor_model`, `VEO_MODEL` | Hesabın güncel dokümanlarından doğrulanmış olmalı |
| 3 | Veo fiyatı (USD/saniye): `studio.provider_usd_per_second.veo` | Girilene kadar Veo ile üretim reddedilir |
| 4 | Apple Developer: App Apple ID (`APP_APPLE_APP_APPLE_ID`) ve Apple kök sertifikaları (`APP_APPLE_ROOT_CERT_PATHS`) | iOS ödemeleri |
| 5 | Google Play: servis hesabı JSON dosyası (`APP_GOOGLE_SERVICE_ACCOUNT_FILE`) ve RTDN token'ı (`APP_GOOGLE_RTDN_TOKEN`) | Android ödemeleri |
| 6 | Alan adı | Paylaşım linkleri ve universal/app links. Ek ayarlar: `APP_SHARE_BASE_URL`, `APP_IOS_APP_IDS`, `APP_ANDROID_SHA256_FINGERPRINTS`, mağaza URL'leri |
| 7 | Marka adı ve bundle id | Şu an yer tutucu: "AI Video (working title)", `com.example.aivideo` |

**Ticari kararlar.**

| # | Karar | Şu anki varsayılan |
|---|---|---|
| 8 | Ürün kataloğu: her paket ve abonelik kaç kredi verir (`billing.products`) | yok |
| 9 | Kredi başına net gelir, USD (`economics.usd_per_paid_credit`) | yok; gelir ve kâr boş gösteriliyor |
| 10 | Dudak senkronu fiyatı | saniye başı 3 kredi |
| 11 | Stüdyo fiyatı | standart saniye başı 10 kredi, premium 25 kredi |
| 12 | Creator gelir payı (%) | Aşama D: yer tutucu, uzaktan ayar |
| 13 | Davet komisyonu ve davet süreleri | Aşama D: yer tutucu, uzaktan ayar |
| 14 | Minimum ödeme tutarı, ödeme gecikmesi, blokaj kuralları | Aşama D: yer tutucu, uzaktan ayar |
| 15 | Ödeme altyapısı ve KYC sağlayıcısı (ör. Stripe Connect, Payoneer) | Seçilmedi; ödemeler manuel işaretleniyor |

**Sağlayıcı ve hukuk kararları.**

| # | Ne | Not |
|---|---|---|
| 16 | Dudak senkronu sağlayıcısı | sync.so (sözleşme gerekiyor, ağ politikası şu an engelliyor) ya da LatentSync/MuseTalk (lisans onayı gerekiyor) |
| 17 | AI aktör pazaryeri [HUKUK] | Hukuk incelemesi olmadan açılmayacak |
| 18 | Kullanıcı şablonlarında başka gerçek kişilerin yer alması [HUKUK] | Haklar ve rıza politikası belirlenmeli |
| 19 | Gizlilik politikası, kullanım şartları, mağaza metinleri [HUKUK] | Taslak yok |

## 2. Gerçek ortamda doğrulanmamış olanlar [DOĞRULAMA]

**GPU ve modeller**
- DreamID-V ve Wan2.2 adapter'ları, VRAM değerleri ve çok kişili yüz değiştirme gerçek GPU'da denenmedi; şimdiye kadar test modelleriyle (mock) doğrulandı.
- Üretim analiz bileşenleri gerçek görüntüde denenmedi: ONNX yüz algılayıcı, YuNet/SFace, SAM 2.1 maskeleri, reşit olmayan/NSFW sınıflandırıcısı. Maskeler şu an kutudan (elips) türetiliyor.
- Çıktı moderasyonu puanlayıcısı (`OUTPUT_SCORER`) sadece arayüz olarak var. Canlıda yapılandırılmalı; yapılandırılmazsa sistem çıktıyı reddediyor.

**Dudak senkronu, ödeme ve üretim**
- Hiçbir gerçek dudak senkronu sağlayıcısı GPU'da denenmedi (LatentSync, MuseTalk, sync.so).
- App Store ve Google Play doğrulaması gerçek hesapla denenmedi. Testler Apple'ın resmi kütüphanesi ve sahte bir Play API ile yapıldı.
- Veo ile sahne üretimi ve Gemini yönetmen/düzenleyici gerçek anahtarla denenmedi.

**Altyapı**
- Docker imajları bu ortamda derlenmedi; CI'da derleniyor.

## 3. Yapılmamış işler [KOD]

### Mobil uygulama
- Gerçek satın alma arayüzü (StoreKit 2 / Play Billing modülü) yok; ödeme sayfası "yakında" diyor.
- Apple ve Google ile giriş düğmeleri bağlanmadı; API tarafı hazır.
- Push bildirimleri yok (video hazır oldu, ödeme yapıldı gibi).
- Dudak senkronunda konuşmacıyı elle eşleştirme ekranı ve kendi sesini yükleme ekranı yok; API hazır.
- Android'de uygulama yüklenirken davet bilgisini taşıyan Play Install Referrer okuması için yerel modül gerekiyor.
- iOS'ta uygulamayı ilk kez yükleyen kişide davet kayboluyor. Kullanıcı yüklemeden sonra linki tekrar açmalı; gizlilik gereği cihaz parmak izi kullanılmıyor.
- Universal links ve app links için `app.json` içinde `associatedDomains` ve `intentFilters` ayarları eksik (alan adı bekleniyor).
- Stüdyo zaman çizelgesi düğmelerle çalışıyor; sürükle-bırak yok.
- Uzun videolar için kesintiye dayanıklı yükleme yok.

### Admin ve operasyon
- Admin paneli arayüzü yok; sadece admin API'si var.
- Zamanlanmış işler (cron) yok:
  - şablon metriklerini yenileme;
  - süresi dolmuş kredileri toplu temizleme (şu an kullanıcının bir sonraki kredi işleminde temizleniyor);
  - saklama süresi dolan dosyaları silme;
  - Google Play onay (acknowledge) denemesini yeniden yapma.
- Yük testi, yedekten geri dönüş otomasyonu, altyapı-kod (Terraform) ve operasyon belgeleri yok: INFRASTRUCTURE, DR, RUNBOOKS, CAPACITY.
- STORE_RELEASE belgesi, mağaza ekran görüntüleri ve mağaza metinleri yok.

### Ürün ve özellikler
- Şablon önizleme videoları ve küçük resimleri admin'in girdiği URL'lerle çalışıyor; yükleme akışı yok.
- `paid_conversion` metriği hâlâ Pro aboneliği ile tahmin ediliyor. Gerçek mağaza verisine bağlanması gerekiyor.
- Konuşmacı tespiti bir sinyal sezgisiyle yapılıyor, gerçek bir ASD modeli yok. TalkNet-ASD lisansı ticari kullanıma izin vermiyor.
- **Stüdyo:**
  - seslendirme (TTS) ve dublaj yok; diyaloglar sadece altyazı olarak gösteriliyor;
  - Stüdyo sahnelerine dudak senkronu bağlanmadı;
  - referans fotoğrafla karakter tutarlılığı ve kimlik kayması (drift) skoru yok;
  - sahne uzatma (extend/prepend) sadece test sağlayıcısında çalışıyor; Veo'da doğrulanmadı ve kapalı;
  - nesne düzenleme, arka plan değiştirme, ışık düzenleme ve inpainting yok; destekleyen sağlayıcı bağlı değil;
  - kural tabanlı düzenleyici sadece yaygın komutları anlıyor; serbest anlatım için Gemini gerekiyor;
  - kesme ve bölme tam saniye hassasiyetinde;
  - Gemini yönetmen çağrısının maliyeti kredi olarak ölçülmüyor;
  - tekil şablon işlerinde üretimden önce USD maliyet tahmini yok;
  - 20–50 rıza alınmış test klibiyle sağlayıcı karşılaştırma (benchmark) düzeneği yok (Aşama E).
- Model yönlendirme tablosu (iş türüne, kaliteye ve maliyete göre sağlayıcı seçimi) ve kendi GPU'muzda çalıştırma adaptörleri Aşama E'de.
- Şablon kaynak klipleri admin hesabına ait. Bir servis hesabına taşınmalı; aksi halde admin hesabı silinirse klipler de silinir.

### Güvenlik ve uyum
- Yedekten geri dönüş testi yapılmadı; RPO/RTO hedefleri belirlenmedi.
- Kırmızı takım (red team) moderasyon senaryoları için manuel test seti yok.
- C2PA içerik kaynağı bilgisi şu an sadece bir bayrak; gerçek C2PA imzası yok.

## 4. Aşama D sonrası eklenecekler

(Aşama D tamamlanınca buraya yazılacak.)
