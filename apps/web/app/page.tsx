import Link from 'next/link';

const FEATURES = [
  { t: 'Film & Dizi Fabrikası', d: 'Fikirden senaryoya, bölüm planından sahne listesine; hikâye hafızası ve bütçe yönetimiyle.' },
  { t: 'Video Editörü', d: 'Çok katmanlı zaman çizelgesi, kırpma, bölme, hız, anahtar kare, metin, ses ve altyazı.' },
  { t: 'Tasarım Editörü', d: 'Afiş, bölüm kapağı ve sosyal medya görselleri; katmanlar, metin, şekiller ve şablonlar.' },
  { t: 'Dijital Oyuncular', d: 'Rızaya dayalı karakter kimlikleri, kilitli görünüm ve oyuncu kadrosu.' },
  { t: 'Sahne Planı', d: 'Oyuncu, kamera ve ışık yerleşimini üstten görünümde planlayın; kamera önizlemesi alın.' },
  { t: 'Her Cihazda Aynı Proje', d: 'Web, Android ve iOS aynı proje dosyasını ve aynı düzenleme komutlarını kullanır.' },
];

export default function Landing() {
  return (
    <main>
      <header className="topbar">
        <Link href="/" className="brand">AI Cinema <span>Studio</span></Link>
        <div className="spacer" />
        <Link className="btn ghost" href="/login">Giriş yap</Link>
        <Link className="btn primary" href="/signup">Hesap oluştur</Link>
      </header>
      <section className="hero">
        <h1>Kendi filmini, dizini ve videonu tek stüdyoda üret</h1>
        <p>Projeni web’de başlat, telefonda düzenle, aynı dosyadan dışa aktar. Yapay zekâ özellikleri yalnızca bağlı
          sağlayıcılar etkin olduğunda çalışır; her işlemden önce kredi maliyetini görürsün.</p>
        <div className="row" style={{ justifyContent: 'center', marginTop: 24 }}>
          <Link className="btn primary" href="/signup">Ücretsiz başla</Link>
          <Link className="btn" href="/app">Stüdyoyu aç</Link>
        </div>
      </section>
      <section className="container">
        <h2 className="sr-only">Özellikler</h2>
        <div className="tiles">
          {FEATURES.map((f) => (
            <article key={f.t} className="card">
              <h3 style={{ margin: '0 0 6px' }}>{f.t}</h3>
              <p className="muted" style={{ margin: 0 }}>{f.d}</p>
            </article>
          ))}
        </div>
        <p className="muted" style={{ marginTop: 32, fontSize: 13 }}>
          Gerçek kişilerin benzerliği yalnızca doğrulanmış rıza ile kullanılabilir. Üretilen içerikler denetimden geçer
          ve yapay zekâ ile üretildiği belirtilir.
        </p>
      </section>
    </main>
  );
}
