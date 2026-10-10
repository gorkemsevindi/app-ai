import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: { default: 'AI Cinema Studio', template: '%s · AI Cinema Studio' },
  description: 'Film, dizi, video ve tasarım projelerini web ve mobilde aynı proje dosyasıyla düzenleyin.',
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { width: 'device-width', initialScale: 1, themeColor: '#0d0e12' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="tr">
      <body>{children}</body>
    </html>
  );
}
