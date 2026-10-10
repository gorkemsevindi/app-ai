'use client';

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="container" style={{ textAlign: 'center', paddingTop: 80 }}>
      <h1>Bir şeyler ters gitti</h1>
      <p className="muted">Kaydedilmemiş düzenlemeler bu cihazda taslak olarak saklanır.</p>
      <button className="btn primary" onClick={reset}>Tekrar dene</button>
    </main>
  );
}
