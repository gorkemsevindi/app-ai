# YourStars: durum özeti (8 Ekim 2026)

**Tek cümleyle:** Platformun tamamı çalışıyor: Studio, iş sistemi, izleyici uygulaması, paywall, gelir paylaşımı ve admin. Gerçekçi video hattı da yazıldı ve testlerden geçti. **Gerçek, fotogerçekçi bir video üretmek için tek eksik bir Google Gemini API anahtarı.**

---

## 1. Şu an ne üretebiliyoruz?

| Ne | Durum | Nasıl |
|---|---|---|
| Hikâye bibliği + bölüm senaryoları (TR/EN) | ✅ Çalışıyor | Yerel şablon yazar. LLM değil, arayüzde "şablon" diye işaretli. Claude bağlantısı yazıldı, anahtar bekliyor |
| Karakter DNA'sı (görünüş, ses, kişilik), kimlik kilidi, sürümler | ✅ Çalışıyor | Studio'dan düzenle → kilitle |
| **2D taslak bölüm** (60 sn, konuşan karakterler, mimikler, lip-sync, müzik, altyazı) | ✅ Çalışıyor, ücretsiz | Yerel motor. Senaryo ve zamanlamayı kontrol etmek için |
| **Gerçekçi bölüm** (fotogerçekçi oyuncular, doğal konuşma, dudak senkronu, mimik) | 🟡 **Kod hazır ve testli; çalışması için `GEMINI_API_KEY` gerekli** | Nano Banana portre → anahtar kare → Veo 3.1 sesli video |
| Gerçekçi karakter portresi (kimlik kilidi) | 🟡 Aynı anahtarı bekliyor | Studio'da "📷 Gerçekçi portre üret" |
| Gerçek bir kişinin yüzüyle video (face swap) | ⛔ Bilerek kapalı | Rıza, inceleme ve geri çekme sistemi hazır. Sağlayıcı ve hukuki onay bekliyor |

**Gerçekçi bölüm maliyeti (60 sn, ≈10 klip):**

| Kalite | Maliyet |
|---|---|
| Önizleme (Veo Fast, 720p) | **≈ $8** |
| Final (Veo standart, 1080p) | **≈ $26** |

Bu tutarlar ilk denemenin maliyeti. Beğenilmeyen çekimin yeniden üretimi ayrıca ücretlenir; değişmeyen çekimler önbellekten ücretsiz gelir.

---

## 2. Neler yaptık?

### AI Studio (creator tarafı)
- **Dizi sihirbazı:** tür, dil, bölüm sayısı ve süre, karakter sayısı, yaş sınırı.
- **Karakter editörü:**
  - ten, saç, göz ve kıyafet rengi; saç stili; gözlük ve sakal
  - ses tipi, perde ve hız
  - kimlik kilidi ve sürüm geçmişi
  - 7 ifadeli mimik kütüphanesi
- **Senaryo editörü:** replik, konuşmacı ve duygu düzenleme; sürümleme ve geri alma.
- **Render rotası:** her bölümde **🎬 Gerçekçi (Veo)** ya da **✏️ 2D taslak (ücretsiz)** seçilebiliyor.
- **Maliyet tahmini:** render'dan önce kredi ve dolar karşılığı gösteriliyor.
- **İş takibi:** adım adım ilerleme, iptal ve kaldığı yerden devam.
- **Yayın akışı:** önizleme → kalite kontrol raporu → yayına gönder → admin onayı → yayın.

### Üretim hattı (arka plan)
- **İki rota:**
  - 2D taslak: ses → planlama → müzik → performans → miks → altyazı → birleştirme → QC → HLS.
  - Gerçekçi: portre → çekim planı → anahtar kare → Veo video → birleştirme → müzik → miks → altyazı → QC → HLS.
- **Yarıda kalan iş kaldığı adımdan devam eder ve hiçbir adım iki kez ücretlendirilmez.**
- **Para kontrolleri:**
  - her ücretli çağrıdan önce harcama limiti ve bakiye kontrolü
  - günlük üst sınır
  - başarısız işte kredi iadesi
- **Otomatik kalite kontrol:**
  - süre ve 9:16 formatı
  - siyah kare ve ses varlığı
  - ses seviyesi (−14 LUFS)
  - altyazı kayması
  - karakter kimlik tutarlılığı
  - dil, güvenlik ve rıza bayrakları
- **Ses ve altyazı:**
  - müzik konuşmada otomatik kısılıyor (ducking)
  - kelime zamanlamalı karaoke altyazı, konuşmacı renkleri, SRT/VTT
  - her videoya "AI ile üretildi" etiketi ve kaynak bilgisi (provenance)

### İzleyici uygulaması (web, mobil uyumlu)
- **Keşif:** dikey kaydırmalı feed (Sana Özel / Trend / Takip / Yeni), tür filtreleri ve arama.
- **Dizi sayfaları:** paylaşım önizlemesi (OG) ve uygulama linkleri.
- **Oynatıcı:** HLS, otomatik sonraki bölüm, kaldığın yerden devam, izleme geçmişi.
- **Etkileşim:** takip, yorum, şikâyet.

### Para ve gelir paylaşımı
- İlk 5 bölüm ücretsiz; sonra bölüm, 5'li paket, sezon veya abonelik.
- Sandbox mağaza: imzalı makbuz, tekrar koruması, iade bildirimi. Gerçek para alınmıyor.
- Çift taraflı muhasebe defteri: her işlem dengeli, hiçbir kayıt silinmiyor.
- Creator'a **net gelirin %60'ı**: vergi, mağaza payı ve iadeler düşüldükten sonra.
- 30 günlük bekletme → çekilebilir bakiye → KYC kontrolü → ödeme.
- Creator paneli: izlenme, nitelikli izlenme, tamamlama, paywall dönüşümü, bekleyen ve çekilebilir kazanç.

### Güvenlik, yönetim, marka
- **Admin paneli:** yayın onayı, moderasyon kuyruğu, yayından kaldırma, KYC, ödemeler, defter dengesi, sağlayıcı/anahtar durumu, kredi yükleme.
- **İçerik ve haklar:** yasaklı içerik filtresi, şikâyet önceliklendirme, gerçek-kişi rıza sistemi, denetim kaydı.
- **Marka:** **YourStars** altın/siyah tema. Logo şimdilik vektörle yeniden çizildi; orijinal dosyayı gönderirsen birebir onu kullanırım.

---

## 3. Kanıt: testler ve denemeler

| Kontrol | Sonuç |
|---|---|
| Otomatik testler (SQLite) | **23/23 geçti**: spec'teki tüm kabul kriterleri + gerçekçi hat |
| PostgreSQL | 17/17 geçti (sonradan eklenen 6 test orada henüz koşulmadı) |
| Gerçek render | 6 Türkçe 2D bölüm (63–64 sn), hepsi kalite kontrolden geçti |
| Tarayıcı testi | Feed → izle → paywall → satın al → 6. bölüm açıldı; Studio, kazanç ve admin sayfaları çalışıyor |
| Gerçekçi hat | Google SDK'sını taklit eden sahte istemciyle uçtan uca test edildi. **Gerçek Veo çağrısı henüz yapılmadı (anahtar yok)** |

Kullanıcı testinde bulunup düzeltilen hatalar:
- SQLite'ta saat dilimi yüzünden feed çöküyordu.
- Bellek kullanımı 2,8 GB'tan ~0,8 GB'a indi.
- Feed'de altyazılar kırpılıyordu.
- Yayındaki bölüm yeniden render edilince katalogdan kayboluyordu.

---

## 4. Gerçekçi videoya geçmek için gerekenler

1. **Google AI Studio'dan Gemini API anahtarı**, faturalandırma açık (aistudio.google.com).
   - `GEMINI_API_KEY` olarak eklenecek; bu ortamdan Google API'sine erişim var.
   - Anahtar gelince ilk gerçek bölümü üretip sonucu gösterebilirim.
2. **Admin panelinden kredi yükleme**: önizleme bölümü ≈ 850, final bölüm ≈ 2.700 kredi (1 kredi = $0,01).

Daha iyi ses tutarlılığı için ikinci aşama, ElevenLabs sesleri + sync.so lip-sync. Bunun için:
- bu iki sitenin ortamın ağ ayarlarında izinli olması (şu an engelli),
- bir R2/S3 depolama alanı.

---

## 5. Bilinen sınırlar (dürüst liste)

- **Gerçekçi rota canlı denenmedi.** Model adları değişmiş olabilir; sistem bunu ilk çağrıda kontrol edip mevcut modelleri listeliyor.
- **Ses tutarlılığı:** Veo sesi her klipte yeniden üretiyor, karakter sesi klipten klibe biraz değişebilir.
- **Türkçe konuşma kalitesi:** Veo için henüz doğrulanmadı.
- **Altyazı zamanlaması:** gerçekçi rotada yaklaşık (ses enerjisine göre). Kelime seviyesi için konuşma tanıma (ASR) eklenmeli.
- **Henüz yok:**
  - mobil uygulama (Expo)
  - Apple/Google ödemeleri ve coin sistemi
  - zaman çizelgesi editörü
  - dublaj
  - gerçek ödeme altyapısı

---

## 6. Senden beklenen kararlar

1. Gemini API anahtarı: **en önemlisi**, gerçekçi video bunu bekliyor.
2. Orijinal logo dosyası.
3. Coin modeli ve fiyatlar (şu an: $0,99 bölüm / $3,99 paket / $9,99 sezon, 5 bölüm ücretsiz).
4. %60/40 paylaşım; abonelik dağıtım kuralı; kredi fiyatlaması.
5. Şirket ve ödeme yapısı: TL için iyzico/PayTR, creator ödemeleri için ABD/AB şirketi + Stripe.
6. Face swap'in lansmanda kapalı kalması; "Originals" (platform fonlu creator) programı.
7. Ayrı GitHub reposu ve bulut hesapları.

Ayrıntılar: [`docs/DECISIONS-NEEDED.md`](docs/DECISIONS-NEEDED.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/TEST-REPORT.md`](docs/TEST-REPORT.md)
