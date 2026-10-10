'use client';

import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import type { Command } from '@shared/engine.ts';
import { EngineError } from '@shared/engine.ts';
import { EditorSession } from '@shared/session.ts';
import { demo, errorMessage, liveTransport, localDrafts, type Mode } from './client';
import { SyncController } from './sync';
import { listAssets } from './upload';

const AUTOSAVE_MS = 800;

export function useEditor(id: string, mode: Mode) {
  const ctl = useRef<SyncController | null>(null);
  const [version, bump] = useReducer((x: number) => x + 1, 0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [urls, setUrls] = useState<Record<string, string>>({});

  const refreshAssets = useCallback(async () => {
    if (mode === 'demo') return;
    try {
      const items = await listAssets();
      setUrls(Object.fromEntries(items.filter((a) => a.url).map((a) => [a.uri, a.url as string])));
    } catch { /* previews only */ }
  }, [mode]);

  useEffect(() => {
    let alive = true;
    const transport = mode === 'demo' ? demo.transport(id) : liveTransport(id);
    transport.get().then((cur) => {
      if (!alive) return;
      const session = new EditorSession(cur.project, cur.revision, `web-${crypto.randomUUID().slice(0, 8)}`);
      const c = new SyncController(session, transport, localDrafts, bump);
      ctl.current = c;
      if (c.restoreDraft() > 0) {
        setToast('Kaydedilmemiş yerel düzenlemeler bulundu ve sunucuya gönderiliyor.');
        c.flush();
      }
      bump();
    }).catch((e) => setLoadError(errorMessage(e)));
    refreshAssets();
    return () => { alive = false; };
  }, [id, mode, refreshAssets]);

  // autosave (debounced) + retry when back online
  useEffect(() => {
    const c = ctl.current;
    if (!c || c.state !== 'pending') return;
    const t = setTimeout(() => c.flush(), AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [version]);
  useEffect(() => {
    const retry = () => { const c = ctl.current; if (c && c.session.outbox.length) c.flush(); };
    window.addEventListener('online', retry);
    const iv = setInterval(() => { if (ctl.current?.state === 'offline') retry(); }, 10_000);
    const leave = (e: BeforeUnloadEvent) => { if (ctl.current?.session.outbox.length) e.preventDefault(); };
    window.addEventListener('beforeunload', leave);
    return () => { window.removeEventListener('online', retry); window.removeEventListener('beforeunload', leave); clearInterval(iv); };
  }, []);

  const exec = useCallback((...cmds: Command[]): boolean => {
    const c = ctl.current;
    if (!c) return false;
    try {
      c.exec(...cmds);
      return true;
    } catch (e) {
      setToast(e instanceof EngineError ? engineMessage(e) : errorMessage(e));
      return false;
    }
  }, []);

  return { ctl: ctl.current, version, exec, loadError, toast, setToast, urls, refreshAssets };
}

export function engineMessage(e: EngineError): string {
  const m: Record<string, string> = {
    overlap: 'Bu konumda başka bir klip var.', locked: 'Bu katman kilitli.', not_found: 'Öğe bulunamadı.',
    in_use: 'Bu varlık bir klipte kullanılıyor.', not_empty: 'Önce izdeki klipleri kaldır.',
  };
  return m[e.code] ?? `Geçersiz düzenleme: ${e.message}`;
}

export const uid = (prefix: string) => `${prefix}_${crypto.randomUUID().replace(/-/g, '').slice(0, 10)}`;
