# AI Video App: Eksikler listesi

**Kapsam:** Bu liste sadece AI Video App'i (`apps/mobile`, `services/*`) kapsar. YourStars (`drama/`) ayrı bir projedir ve eksikleri `drama/OZET.md` dosyasındadır.

**Kullanım:** Her aşamada güncellenir. İstendiğinde bu dosya verilir.

**Son güncelleme:** 2026-10-10 (V5 Aşama B sonrası).

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
| 12 | Creator gelir payı (%) | Gelir politikası (revenue policy) oluşturulana kadar hiç kazanç birikmez |
| 13 | Davet komisyonu ve davet süreleri | Aynı politikadan gelir; aynı kural geçerli |
| 14 | Minimum ödeme tutarı, ödeme gecikmesi, blokaj kuralları | Aynı politikadan gelir; aynı kural geçerli |
| 14b | AI aktör lisans gelir payı (`actor_share_rate`) | Aynı politikadan gelir; aynı kural geçerli |
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
- Zamanlanmış işler ✅ Aşama E'de yazıldı (`app/scheduler.py`). Yalnızca canlı ortamda tetikleyici kurulumu kaldı:
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

## 4. Aşama D'den kalan eksikler

**Ödeme ve vergi altyapısı**
- Gerçek ödeme ve KYC entegrasyonu yok. Ödemeler admin tarafından dış referansla "ödendi" diye işaretleniyor. KYC durumu da elle giriliyor; webhook bağlanmadı.
- Vergi ve ülke kuralları yok: stopaj, fatura, ödeme yapılabilecek ülke listesi. [SEN] [HUKUK]
- Ödeme dönemi kapanışları otomatik çalışmıyor (cron yok). Admin `/admin/settlements/run` uç noktasını elle çalıştırıyor.

**Dolandırıcılık tespiti**
- Sinyaller basit kurallarla çalışıyor: iade oranı, tek bir ödeyene bağımlılık, açık şikâyetler. Cihaz ve hesap çiftliği tespiti, otomatik kullanım ve sahte etkileşim modeli yok.
- Davet kötüye kullanımı için kapsamlı kontrol yok. IP özetiyle tekil tıklama sayımı ve "kendini davet edemezsin" kuralı var; daha fazlası yok.

**Admin, mobil ve analitik**
- Admin panelinde creator moderasyonu, ödeme dönemi kapanışları ve risk blokajı ekranları yok; sadece API var.
- Mobil uygulamada:
  - creator şablon yükleme sihirbazı yok (API hazır; mobilde şu an sadece kazanç, bakiye ve şablon durumu görünüyor);
  - AI aktör pazaryeri ekranları yok (API hazır);
  - itiraz (appeal) ekranı yok.
- Creator analitiğinde görüntülenme ve paylaşım verisi şablon metrik tablosundan geliyor; bu tablo zamanlanmış görev (cron) olmadan güncellenmiyor.

**AI aktör pazaryeri** [HUKUK]
- Hukuk incelemesi yapılmadan açılmamalı. Kod, inceleme referansı (`legal_review_ref`) girilmeden zaten çalışmıyor.
- Yasaklı kullanım alanları (siyaset, yetişkin içerik vb.) sadece anahtar kelimeyle kontrol ediliyor; anlam düzeyinde bir sınıflandırıcı yok.
- "Ticari kullanım" şartının yayınlanan videolarda uygulanması teknik olarak takip edilmiyor.
- Ünlü veya başka bir kişiyi taklit tespiti, sadece "kendi doğrulanmış yüz profilin" kuralına dayanıyor. Yüz benzerliğiyle ünlü taraması yok.
- İtiraz akışı sadece admin notu olarak var; kullanıcı arayüzü yok.

**Hesap silme ve çakışma riski**
- Creator hesabı silinirse para kayıtları (değiştirilemez kazanç kaydı) korunuyor ama profil temizleme politikası kesinleşmedi. [HUKUK]
- Kullanıcı ödeme dönemi kapanışı sırasında aynı anda kazanç oluşturursa ne olacağı (eşzamanlılık) yük testinde denenmedi.

## 5. Sırada bekleyen iş planı

### 5.1 V4 Aşama E: tamamlandı, kalan eksikler

**Ortam ve bayraklar**
- Zamanlanmış görevler dışarıdan tetiklenmeli. Cron veya Kubernetes CronJob kurulumu ve `APP_CRON_TOKEN` gizli değişkeni gerekiyor. [SEN]
- Uyarlanabilir yönlendirme `routing` bayrağıyla açılır. Önce gerçek sağlayıcı fiyatları girilmeli ve benchmark çalıştırılmalı. [SEN]

**Kalite ve performans ölçümü**
- Benchmark kalite puanı şu an sadece insan değerlendirmesine dayanıyor; otomatik kalite metriği yok (V5'te gelecek).
- Yük testi tek makinede ve boş veritabanıyla yapıldı (yaklaşık 220 istek/sn, hata yok). Canlı ortam kapasitesi ölçülmedi.

**Altyapı**
- Altyapı-kod (Terraform), çoklu bölge yedeklemesi ve otomatik ölçekleme (autoscaling) yok.
- Kendi GPU'muzda çalıştırma adaptörleri hâlâ doğrulanmadı; GPU erişimi gerekiyor. [DOĞRULAMA]

**Diller**
- Sadece Türkçe ve İngilizce var. Yeni diller için çeviri gerekiyor; sağdan sola yazılan diller (RTL) test edilmedi.

### 5.2 Master Spec V5: kendi kendine öğrenen motor
Dosya: `docs/MASTER_SPEC_V5_SELF_LEARNING.docx`. Kullanıcı 2026-10-10'da ekledi; V4 tamamlanınca ele alınacak.

V5, V4'ün bütün kapsamını korur ve şunları ekler:

**İlkeler**
- Önceki kullanıcıların videolarının içeriği değil, üretim tekniği öğrenilir. Ezber yok.
- Önceliklendirme sırası: kullanıcının açık talimatı > seçilen şablon > seçilen yaratıcı mod > sistemin önerileri.
- Kullanıcının bilerek yaptığı şablon tekrarı serbesttir; cezalandırılmaz.

**Bağımsız servisler**
- Niyet ayrıştırıcı, prompt iyileştirici, yaratıcı planlayıcı.
- Teknik hafıza (model yeteneği, maliyet, güvenilirlik, kalite).
- Rızaya bağlı tercih hafızası; kullanıcılar arası veri sızıntısı yok.
- Model performans kaydı, uyarlanabilir yönlendirici.
- Yenilik ve kalite değerlendiricileri, geri bildirim toplayıcı.
- Çevrimdışı eğitici, deney kaydı, politika kapısı.

**Yaratıcı modlar ve özgünlük kontrolleri**
- Üç mod: Sadık (minimum yorum), Dengeli (varsayılan), Deneysel (daha geniş fikir üretimi).
- Kullanıcının orijinal prompt'u hiçbir zaman değiştirilmez; iyileştirilmiş prompt ayrı ve sürümlü tutulur.
- Ezber/kopya önleme: anlamsal benzerlik ve kategoriye göre ayarlanmış eşikler.
- Çeşitlilik takibi: anlatı, kompozisyon, kamera, mekân, tempo.
- Teknik yönlendirmede keşif/sömürü stratejisi (bandit); bütçe sınırlı, kullanıcıya gizli ek ücret yok.

**Veri ve öğrenme döngüsü**
- Geri bildirim ve veri yönetişimi: varsayılan olarak toplu ve sınırlı telemetri; eğitim için açık rıza (opt-in); silme ve vazgeçme yolu; zehirli geri bildirime karşı koruma.
- İstek başına model ağırlığı yeniden eğitilmez.
- Çevrimdışı döngü: önce gölge modda çalıştırma → sınırlı A/B testi → kademeli genişletme. Gerileme olursa otomatik geri alma.
- İnce ayar (fine-tuning) en sonda ve isteğe bağlı; lisansı uygun açık ağırlıklı modellerle.

**Değerlendirme**
- Ölçütler: talimata uyum, kimlik tutarlılığı, zamansal tutarlılık, dudak senkronu, yenilik, çeşitlilik, maliyet, şikâyet oranı.
- Kör insan değerlendirmesi.
- Sabit ve çeşitli bir değerlendirme seti.

**Yeni tablolar**
learning_events, prompt_strategies, model_performance_aggregates, creative_preferences, evaluation_datasets, evaluation_runs, experiment_assignments, learning_policy_versions, model_registry_versions, similarity_audits, consent_records.

**Kabul testleri**
- Aynı prompt 100 kez çalıştırıldığında çeşitlilik kontrollü olmalı.
- Viral bir şablon özgün üretimleri domine etmemeli.
- Zehirli geri bildirim, rıza eksikliği, kullanıcı silme ve model gerilemesi senaryoları test edilmeli.
- Kullanıcılar arası medya sızıntısı olmamalı.
- Kendi kendine devreye giren model güncellemesi olmamalı.

**Aşamalar**
- A: denetim ve eksik analizi.
- B: ölçümleme, rıza kontrolleri, teknik hafıza, değerlendirme düzeneği.
- C: prompt iyileştirici, üç mod, yenilik ölçümü, geri bildirim.
- D: çevrimdışı uyarlanabilir yönlendirme ve deneyler.
- E: isteğe bağlı ince ayar.

**Mevcut koddaki dayanaklar**
- Ekonomi ve telemetri: `economics.py`, `model_runs`.
- Kalite ölçümleri: dudak senkronu ve geçiş (seam) skorları.
- Sürümlü storyboard'lar ve düzenleme işlemleri.
- Şablon sıralaması.
- Sağlayıcı kapasite tablosu.

### 5.2a V5 ilerlemesi
- ✅ **Aşama A:** denetim ve plan (`docs/V5_AUDIT_AND_PLAN.md`).
- ✅ **Aşama B:** öğrenme altyapısı. Her biten iş için içerik içermeyen bir kayıt tutuluyor; öğrenme rızası, kullanıcı puanı ve geri bildirim, teknik hafıza (günlük özetler) ve öğrenme paneli var.
- **Aşama B'den kalan eksikler:**
  - ~~Mobilde puan verme ve öğrenme rızası ekranları yok~~ (Aşama C'de eklendi).
  - Otomatik kalite ölçümleri (kimlik kayması, titreme, talimata uyum) yok.
  - Model eğitimi için içerik saklama kapalı. Açılması için hukuk incelemesi gerekiyor. [HUKUK]
- ✅ **Aşama C:** prompt iyileştirici, üç yaratıcı mod (sadık, dengeli, deneysel), varyasyonlar, özgünlük ve benzerlik denetimi, çeşitlilik paneli. Mobilde mod seçimi, puan verme ve öğrenme rızası eklendi.
- ✅ **Aşama D:** yönlendirmede kısıtlı bandit, A/B atamaları, politika yaşam döngüsü (gölge → A/B → aktif) ve otomatik geri alma.
- ✅ **Aşama E:** model kayıt yönetimi. Lisans, veri hakları ve model kartı kontrol ediliyor; ardından kör değerlendirme, iki kişilik onay ve yayına alma/kaldırma geliyor.
- **V5'ten kalan eksikler:**
  - Gerçek ince ayar (fine-tuning) eğitimi, GPU altyapısı ve kendi sunucumuzda çalışan model yok. Lisanslı model ve veri seti gelince yapılacak. [KARAR/HUKUK]
  - Benzerlik denetimi şimdilik yalnızca metin karşılaştırıyor. Anlamsal ve görsel benzerlik için embedding sağlayıcısı ve kalibrasyon gerekiyor.
  - Bandit gerçek trafik verisi olmadan anlamlı karar veremez. Canlıda gölge modda başlatılmalı.
  - Politika ve model kaydı için yönetim arayüzü yok; yalnızca API var.
  - Otomatik kalite ölçümleri (kimlik kayması, titreme) yok.
  - Model eğitimi için içerik saklama kapalı ve hukuk incelemesi gerekiyor. [HUKUK]
- **Sırada:** V6 (aşağıda §5.4).

### 5.4 V6 — Karakter Kimlik Ekosistemi (ayrıntı: `docs/V6_PHASE_REPORTS.md`)
- ✅ **Aşama A:** denetim, eksik analizi ve plan (`docs/V6_AUDIT_AND_PLAN.md`).
- ✅ **Aşama B:** Character Creator, çok açılı kimlik, kalıcı karakter kimliği (UUID ve `@yaratıcı/ad`), Character Lock (standart/güçlü/katı), @Karakter ile senaryo çözümleme. Mobilde karakter ekranları ve oyuncu kadrosu paneli var.
- ✅ **Aşama C:** AI Casting Director (kural tabanlı), Character Marketplace (ilan, inceleme, lisans, takedown, şikâyet) ve Creator Royalty (kullanım başına lisans kredisi, tek seferlik telif, şelale kuralı, geri alma).
- **Kullanıcıdan gerekenler (V6):**
  - Karakter görselleri için gerçek görsel modeli seçimi ve doğrulaması: `GEMINI_API_KEY` ve `CHARACTER_IMAGE_MODEL`, ayrıca görsel başı USD fiyatı. [KARAR]
  - Veo 3.1'in referans görsel desteği hesabınızda doğrulanmalı; ardından `veo` sağlayıcısında `CHARACTER_REFERENCE` açılır. [KARAR]
  - Yüz eşleştirme kalite kontrolü için SFace/YuNet ONNX dosyaları worker'a eklenmeli: `MP_YUNET_ONNX`, `MP_SFACE_ONNX`.
  - Karakter lisans koşulları için hukuk incelemesi; bitince pazar yeri bayrağı `legal_review_ref` ile açılır. [HUKUK]
  - Telif oranı (`character_share_rate`) ile karakter kredisi fiyatları (önizleme/görünüm kredisi, güçlü kilit çarpanı, katı kilitte yeniden deneme hakkı). [KARAR]
  - TTS (seslendirme) sağlayıcısı seçimi ve lisanslı ses kataloğu. Ses klonlama kapalı kalacak. [KARAR]
- **Eksik / kısmi kalanlar:**
  - Gerçek kimlik kalitesi henüz kanıtlanmadı. Şu an mock sağlayıcılarla yalnızca akış doğrulandı. Gerçek sağlayıcıyla eşik kalibrasyonu (ayrı bir sentetik test setiyle, güven aralığıyla) yapılmalı.
  - Vekil (proxy) metrikler zayıf: arka görünüm renkten dolayı yüksek, ışık değişimi düşük skor alıyor. Bu metrikler hiçbir zaman "doğrulandı" demiyor.
  - Studio'da karakter başına seslendirme ve dudak senkronu yok; diyaloglar altyazı olarak kalıyor.
  - Casting Director kural tabanlı; AI (LLM) destekli seçim yok.
  - Benzerlik denetimi metin tabanlı. Görsel benzerlik yalnızca algısal özetle (dHash) ve sezgisel olarak yapılıyor.
  - Karakter işleri için yük testi, kimlik koşullandırmada öğrenen yönlendirme, en/tr dışındaki diller yok.
  - Mobil ekranlar cihazda denenmedi (tip kontrolü ve çeviri testleri geçti).

### 5.3 İş bitince: Master Spec güncellemesi
Kullanıcı talebi: Tüm aşamalar bitince Master Spec güncellenecek. V3, V4 ve V5'i tek belgede birleştiren, uygulanan durumu ("uygulandı / doğrulanmadı / eksik") ve gerçek mimariyi yansıtan güncel bir master spec hazırlanacak.
