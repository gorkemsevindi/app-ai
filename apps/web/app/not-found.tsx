import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="container" style={{ textAlign: 'center', paddingTop: 80 }}>
      <h1>Sayfa bulunamadı</h1>
      <p className="muted">Aradığın sayfa taşınmış ya da hiç var olmamış olabilir.</p>
      <Link className="btn primary" href="/app">Stüdyoya dön</Link>
    </main>
  );
}
