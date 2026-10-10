# AI Video App: Şimdiye kadar yapılanlar

**Tarih:** 2026-10-10. **Dal:** `claude/practical-dirac-zcl83d`.

**Kapsam:** Sadece AI Video App (`apps/mobile`, `services/api`, `services/worker`). YourStars (`drama/`) ayrı bir projedir; özeti `drama/OZET.md` dosyasındadır.

**Durum:** Kod yazıldı ve testleri geçiyor. Gerçek AI sağlayıcıları, mağaza hesapları ve ödeme altyapısıyla henüz canlıda denenmedi; bunlar senin anahtarlarını, hesaplarını ve kararlarını bekliyor. Eksiklerin tam listesi: `docs/EKSIKLER.md`.

---

## 1. Kısaca: uygulama ne yapabiliyor?

| Özellik | Durum |
|---|---|
| Kendi yüzünle viral şablon videosu (tek kişi) | ✅ Çalışıyor (test modelleriyle) |
| Çok kişili yüz değiştirme: videodaki her kişiye ayrı biri | ✅ Çalışıyor (test modelleriyle) |
| Şablon remix: "arkadaşınla başrol" | ✅ |
| Viral şablon akışı, sıralama, kategoriler | ✅ |
| Müzikle dudak senkronu, kim konuşuyor eşleştirmesi, kalite ölçümü | ✅ Altyapı hazır; gerçek sağlayıcı lisans kararını bekliyor |
| Kredi sistemi: kovalar, rezerve et → kesinleştir → iade et | ✅ |
| App Store / Google Play satın alma doğrulaması | ✅ Sunucu tarafı hazır; mobil satın alma ekranı yok |
| Paylaşım linki, davet sistemi, şablon şikâyeti | ✅ |
| Maliyet ve kâr paneli (admin) | ✅ |
| AI Stüdyo: hikâye → storyboard → fiyat → onay → üretim → kurgu | ✅ Test sağlayıcısıyla; Veo için `GEMINI_API_KEY` gerekiyor |
| Sohbetle ve zaman çizelgesiyle düzenleme, sahne uzatma, ücretsiz kesme | ✅ |
| Creator pazaryeri: şablon gönderme, gelir paylaşımı, ödemeler | ✅ Gerçek ödeme altyapısı yok; ödemeler admin'in girdiği dış referansla elle işaretleniyor |
| Lisanslı AI aktör pazaryeri | ✅ Kodlandı; hukuk onayı olmadan açılmıyor |
| Akıllı sağlayıcı seçimi, benchmark, zamanlanmış görevler | ✅ |

## 2. Aşama aşama yapılanlar

### V3: Şablonlar ve dudak senkronu
- **Faz 1: Şablonlar**
  - Şablonlarda kişi yuvaları (slot): her yuvaya kimin yüzü geleceği seçiliyor.
  - Admin şablon yükleme akışı (önce hak belgesi), yayınlanmış sürümlerin değiştirilemezliği.
  - Sıralanmış keşif akışı, maliyet tahmini ve pahalı işlerde onay.
  - Mobilde her yuvaya profil seçme ekranı.
- **Faz 2: Ses ve dudak senkronu**
  - Ses modları: orijinal, kendi sesin (haklarını beyan ettiğin) veya sessiz. Önceden videolar sessiz çıkıyordu; bu düzeltildi.
  - Videodaki konuşmacıyı otomatik bulma; emin olunamazsa sistem tahmin etmiyor, kullanıcıya soruyor.
  - Dudak senkronu ücreti ayrı hesaplanıyor; maliyet tavanı var.
  - Lisans kilidi: lisansı netleşmemiş modeller açık onay olmadan çalışmıyor.
  - Kalite ölçümü: ses-dudak kayması (ms), senkron puanı, hareket koruma.

### V4 Aşama A: Ticari kullanıma hazırlık
- **A1: Kredi kovaları.** Promosyon, abonelik, satın alınan ve diğer krediler ayrı tutuluyor; her kovanın kendi bitiş tarihi var. Krediler önce rezerve ediliyor, iş başarılıysa kesinleşiyor, başarısızsa alındığı kovaya geri dönüyor.
- **A2: Mağaza ödemeleri.** Apple'ın resmi kütüphanesiyle iOS doğrulaması, Google Play doğrulaması, abonelik yenileme/iade/iptal bildirimleri. Aynı makbuz iki kez kredi vermiyor.
- **A3: Paylaşım.** İmzalı paylaşım linkleri, uygulamayı doğrudan açan derin linkler, davet takibi, şablon şikâyetleri.
- **A4: Ekonomi paneli.** Özellik, model ve şablon bazında maliyet ve kâr; iş geçmişi; destek ekibi için kayıtlı manuel iade.

### V4 Aşama B: AI Stüdyo
- Proje oluşturma ve değiştirilemez sürüm geçmişi.
- Yönetmen: şu an kural tabanlı (açıkça "AI değil" diye etiketli); Gemini yönetmen anahtar girilince devreye girer.
- Önce fiyat, sonra onay, sonra üretim. Sadece değişen sahneler yeniden üretilip ücretlendiriliyor.
- Karakter ve rıza kaydı; rıza geri çekilince sırada bekleyen sahneler de üretimden önce engelleniyor.
- Sahnelerin birleştirilmesi: geçişler, altyazı, müzik, ses seviyesi dengeleme, filigran.
- Mobilde Stüdyo sekmesi.

### V4 Aşama C: Düzenleme
- Sohbetle ve zaman çizelgesinden aynı düzenleme işlemleri.
- Her düzenleme önce önizleme ve maliyet farkı olarak geliyor, sonra uygulanıyor; geri alma var.
- Üretilmiş sahneyi kesme ve bölme ücretsiz. Sahne uzatma (sona veya başa) yapılıyor ve geçişin pürüzsüzlüğü ölçülüyor.
- Yapılamayan istekler sahte sonuç yerine "desteklenmiyor" yanıtı alıyor; belirsiz isteklerde soru soruluyor.

### V4 Aşama D: Creator ekonomisi
- Creator profili ve şablon gönderme: hak belgesi ve videodaki herkesin onayı zorunlu, sonra moderasyon.
- Sürümlü gelir politikası: **sen politika girene kadar hiç kazanç birikmiyor.**
- Değiştirilemez kazanç kaydı. Sadece gerçek parayla alınmış krediler kazandırıyor; promosyon ve test (sandbox) kredileri kazandırmıyor.
- İadede kazancın geri alınması, ödeme dönemi kapanışları, şüpheli durumlarda ödeme blokajı.
- Ödeme için doğrulanmış ödeme hesabı ve dış ödeme referansı zorunlu.
- Lisanslı AI aktör pazaryeri: hukuk kilidi, kişinin sadece kendi yüzü, lisans kontrolleri, onay geri çekilince kullanılmayan süre için iade.
- Mobilde Creator ekranı.

### V4 Aşama E: Operasyon
- Akıllı sağlayıcı seçimi: en ucuz uygun sağlayıcı seçiliyor; çok hata veren otomatik devre dışı kalıyor; kalite eşiği var.
- Benchmark: sabit test setleri ve kör insan değerlendirmesi; puanlar sağlayıcı seçimine kalite değeri olarak giriyor.
- Zamanlanmış görevler (aynı görev iki kez aynı anda çalışmıyor, her çalıştırma kayıt altında).
- Yük testi: yerelde hata yok, saniyede yaklaşık 220 istek (canlı ortam kapasitesi değil).
- Yedekten geri dönüş tatbikatı: OK.
- Dil anahtarlarının eşleştiğini kontrol eden test.

## 3. Test durumu (son çalıştırma)

| Bileşen | Sonuç |
|---|---|
| API (FastAPI, PostgreSQL) | **98/98** test geçti |
| Worker (ffmpeg/OpenCV ile gerçek video işleme) | **20/20** test geçti |
| Mobil (Expo) | Tip kontrolü (tsc) temiz, **6/6** test geçti |
| Veritabanı migration'ları | 10 adet; testlerde her seferinde geri alınıp yeniden uygulanıyor; şema kayması yok |
| Kod kalite kontrolü (lint) | Temiz |

**Uçtan uca testler gerçek worker ile çalışıyor:**
- çok kişili yüz değiştirme;
- şablon remix;
- dudak senkronu;
- Stüdyo kurgusu (altyazı ve müzik);
- kesme, bölme, uzatma;
- benchmark.

## 4. Güvenlik ve dürüstlük ilkeleri (bütün aşamalarda)
- Hiçbir anahtar veya şifre kodda yok; hepsi ortam ayarlarındaki gizli değişkenlerden okunuyor.
- Para hareketleri değiştirilemez kayıtlarda tutuluyor, aynı işlem iki kez işlenmiyor ve her şey denetlenebilir.
- Rıza, haklar, moderasyon ve şikâyet akışları baştan tasarıma dahil.
- Lisansı veya fiyatı doğrulanmamış sağlayıcılar kapalı; doğrulanmamış hiçbir özellik "çalışıyor" diye gösterilmiyor.
- Yeni özelliklerin hepsi bayrak arkasında; mevcut özellikler bozulmadı.

## 5. Senden beklenenler
Ayrıntısı `docs/EKSIKLER.md` §1'de. Hiçbir anahtarı sohbete yapıştırma; ortam ayarlarına gizli değişken (secret) olarak gir.

1. `GEMINI_API_KEY`, model adları ve Veo fiyatı.
2. Apple ve Google mağaza hesapları ve ürün kataloğu.
3. Gelir politikası:
   - creator, davet ve aktör payları (%);
   - kredi başına net gelir (USD);
   - bekleme süresi ve minimum ödeme tutarı.
4. Ödeme ve KYC sağlayıcısı; vergi kuralları.
5. Hukuk incelemeleri: AI aktör pazaryeri, kullanıcı şablonlarında başka gerçek kişiler, gizlilik politikası ve kullanım şartları.
6. Alan adı, marka adı ve bundle id.

## 6. Sırada ne var?
1. **Master Spec V5: kendi kendine öğrenen motor.**
   - Aşama A: denetim, eksik raporu ve plan.
   - Aşama B: ölçümleme, rıza kontrolleri, teknik hafıza, değerlendirme düzeneği.
   - Aşama C: prompt iyileştirici, üç yaratıcı mod, özgünlük ölçümü.
   - Aşama D: uyarlanabilir yönlendirme ve deneyler.
   - Aşama E: isteğe bağlı ince ayar (fine-tuning).
2. **İş bitince:** V3, V4 ve V5'i birleştiren güncel master spec.

## 7. Belgeler
| Dosya | İçerik |
|---|---|
| `docs/EKSIKLER.md` | Tüm eksikler ve senden beklenenler (Türkçe) |
| `docs/V3_AUDIT_AND_PLAN.md`, `docs/V3_PHASE_REPORTS.md` | V3 denetimi ve faz raporları |
| `docs/V4_STAGE_REPORTS.md` | V4 A–E raporları: değişen dosyalar, API'ler, testler, maliyet, eksikler |
| `docs/MASTER_SPEC_V4.docx`, `docs/MASTER_SPEC_V5_SELF_LEARNING.docx` | Senin gönderdiğin spesifikasyonlar |
