import path from 'node:path';
import type { NextConfig } from 'next';

const root = path.join(import.meta.dirname, '../..');

// Content-Security-Policy: the browser only talks to this origin (the BFF) and to presigned object-storage
// URLs (uploads/previews). STORAGE_ORIGIN is a public host name, never a secret.
const storage = process.env.STORAGE_ORIGIN ?? '';
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'" + (process.env.NODE_ENV === 'development' ? " 'unsafe-eval'" : ''),
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${storage}`.trim(),
  `media-src 'self' blob: ${storage}`.trim(),
  `connect-src 'self' ${storage}`.trim(),
  "font-src 'self' data:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ');

const config: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  // packages/shared lives outside apps/web (monorepo without workspaces, ADR-1)
  turbopack: { root },
  outputFileTracingRoot: root,
  async headers() {
    return [{
      source: '/:path*',
      headers: [
        { key: 'Content-Security-Policy', value: csp },
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
      ],
    }];
  },
};

export default config;
