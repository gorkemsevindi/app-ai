'use client';
/** Direct-to-storage upload: the API returns a presigned PUT URL, the browser uploads the file itself (no file
 * bytes pass through Vercel functions), then the API verifies the object exists. */

import type { AssetKind } from '@shared/schema.ts';
import { api } from './client';

export interface EditorAssetOut {
  id: string; kind: AssetKind; name: string; uri: string; status: string; mime: string; size_bytes: number;
  meta: { duration_ms?: number; width?: number; height?: number }; url: string | null;
}

export function kindOf(file: File): AssetKind | null {
  if (file.type.startsWith('video/')) return 'video';
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('audio/')) return 'audio';
  if (/font|ttf|otf|woff/.test(file.type) || /\.(ttf|otf|woff2?)$/i.test(file.name)) return 'font';
  return null;
}

export function probe(file: File, kind: AssetKind): Promise<{ duration_ms?: number; width?: number; height?: number }> {
  const url = URL.createObjectURL(file);
  const done = <T,>(v: T) => { URL.revokeObjectURL(url); return v; };
  return new Promise((resolve) => {
    if (kind === 'image') {
      const img = new Image();
      img.onload = () => resolve(done({ width: img.naturalWidth, height: img.naturalHeight }));
      img.onerror = () => resolve(done({}));
      img.src = url;
    } else if (kind === 'video' || kind === 'audio') {
      const el = document.createElement(kind);
      el.preload = 'metadata';
      el.onloadedmetadata = () => {
        const v = el as HTMLVideoElement;
        resolve(done({ duration_ms: Math.round(el.duration * 1000),
                       ...(kind === 'video' ? { width: v.videoWidth, height: v.videoHeight } : {}) }));
      };
      el.onerror = () => resolve(done({}));
      el.src = url;
    } else resolve(done({}));
  });
}

export async function uploadAsset(file: File, onProgress?: (pct: number) => void): Promise<EditorAssetOut> {
  const kind = kindOf(file);
  if (!kind) throw new Error('Bu dosya türü desteklenmiyor.');
  const meta = await probe(file, kind);
  const up = await api<{ asset_id: string; upload: { url: string; method: 'PUT'; headers: Record<string, string> } }>(
    '/editor/assets', { method: 'POST', body: { kind, mime: file.type || 'application/octet-stream', size_bytes: file.size, name: file.name } });
  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', up.upload.url);
    for (const [k, v] of Object.entries(up.upload.headers)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100)); };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Yükleme başarısız (${xhr.status})`)));
    xhr.onerror = () => reject(new Error('Yükleme sırasında bağlantı koptu.'));
    xhr.send(file);
  });
  return api<EditorAssetOut>(`/editor/assets/${up.asset_id}/complete`, { method: 'POST', body: meta });
}

export async function listAssets(): Promise<EditorAssetOut[]> {
  return (await api<{ items: EditorAssetOut[] }>('/editor/assets')).items;
}
