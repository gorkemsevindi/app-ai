import { Image } from 'expo-image';
import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Pressable, ScrollView, View } from 'react-native';

import { Body, Button, Card, Chip, Title } from '@/components/ui';
import { api } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Asset = { id: string; kind: string; view: string; status: string; creator_review: string | null;
  face_meaningful: boolean; url: string | null; qc: Record<string, number | string> };
type Report = { verdict: string; method: string; verified: boolean; metrics: { failed: string[]; missing: string[] } };
type Char = { id: string; handle: string; display_name: string; status: string; locked: boolean;
  current_version: { version: string; status: string; master_asset_id: string | null };
  locked_version: { version: string; package_checksum: string } | null; report: Report | null; assets: Asset[] };
type Quote = { credits: number; images: number };

const key = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

// Identity workflow: previews (reroll) -> approve master -> multi-angle build -> review each view (manual QC)
// -> repair only rejected/failed views -> lock. Consistency is measured, never promised.
export default function CharacterScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [ch, setCh] = useState<Char | null>(null);
  const idem = useRef(key());

  const load = useCallback(async () => {
    const x = await api<Char>(`/characters/${id}`);
    setCh(x);
    return x;
  }, [id]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const tick = async () => {
      try {
        const x = await load();
        if (alive && x.assets.some((a) => a.status === 'pending')) timer = setTimeout(tick, 3000);
      } catch { if (alive) timer = setTimeout(tick, 8000); }
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [load]);

  const paid = async (kind: 'preview' | 'build' | 'repair', path: string, extra: Record<string, unknown> = {}) => {
    try {
      const q = await api<Quote>(`/characters/${id}/quote?kind=${kind}&count=4`);
      Alert.alert(t('character.confirm', { credits: q.credits, n: q.images }), '', [
        { text: t('job.cancel'), style: 'cancel' },
        { text: 'OK', onPress: async () => {
          try {
            await api(`/characters/${id}/${path}`, { body: { confirmed_credits: q.credits, ...extra }, idempotencyKey: idem.current });
            idem.current = key();
            await load();
          } catch (e) { Alert.alert(errorMessage(t, e)); }
        } },
      ]);
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };
  const act = async (path: string, body: Record<string, unknown> = {}) => {
    try { await api(`/characters/${id}/${path}`, { body }); await load(); }
    catch (e) { Alert.alert(errorMessage(t, e)); }
  };

  if (!ch) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const v = ch.current_version;
  const previews = ch.assets.filter((a) => a.kind === 'seed_preview');
  const views = ch.assets.filter((a) => a.kind === 'view');
  const tile = (a: Asset, onPress?: () => void) => (
    <Pressable key={a.id} onPress={onPress} accessibilityLabel={a.view}
      style={{ width: '31%', gap: 2, borderRadius: radius.md, borderWidth: v.master_asset_id === a.id ? 2 : 0, borderColor: c.accent }}>
      <View style={{ aspectRatio: 1, borderRadius: radius.md, overflow: 'hidden', backgroundColor: c.surfaceAlt }}>
        {a.url ? <Image source={{ uri: a.url }} style={{ flex: 1 }} contentFit="cover" /> : null}
      </View>
      <Body muted>{`${a.view.replace('preview_', '#')} · ${t(`character.asset.${a.status}`, { defaultValue: a.status })}`}</Body>
      {a.kind === 'view' && !a.face_meaningful ? <Body muted>{t('character.notFaceView')}</Body> : null}
    </Pressable>
  );
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{`${ch.display_name} · ${ch.handle}`}</Title>
      <Body muted>{`v${v.version} · ${t(`character.version.${v.status}`, { defaultValue: v.status })}`}</Body>
      {ch.locked_version ? <Body muted>{t('character.lockedAs', { v: ch.locked_version.version })}</Body> : null}
      {v.status === 'draft' ? (
        <Card style={{ gap: spacing.sm }}>
          <Body>{t('character.previewsHint')}</Body>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {previews.map((a) => tile(a, a.status === 'ready' ? () => Alert.alert(t('character.approveMaster'), '', [
              { text: t('job.cancel'), style: 'cancel' }, { text: 'OK', onPress: () => act('approve-master', { asset_id: a.id }) },
            ]) : undefined))}
          </View>
          <Button title={t(previews.length ? 'character.reroll' : 'character.previews')} onPress={() => paid('preview', 'preview', { count: 4 })} />
        </Card>
      ) : null}
      {v.status === 'master_approved' && views.length === 0 ? (
        <Button title={t('character.build')} onPress={() => paid('build', 'identity-build')} />
      ) : null}
      {views.length ? (
        <Card style={{ gap: spacing.sm }}>
          <Body>{t('character.sheet')}</Body>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>{views.map((a) => tile(a))}</View>
          {v.status !== 'locked' ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
              {views.filter((a) => a.status === 'ready').map((a) => (
                <Chip key={a.id} label={`✓ ${a.view}`} onPress={() => act(`assets/${a.id}/review`, { decision: 'approved' })} />
              ))}
              {views.filter((a) => a.status === 'ready' || a.status === 'approved').map((a) => (
                <Chip key={`r-${a.id}`} label={`✕ ${a.view}`} onPress={() => act(`assets/${a.id}/review`, { decision: 'rejected' })} />
              ))}
            </View>
          ) : null}
        </Card>
      ) : null}
      {ch.report ? (
        <Card style={{ gap: 4 }}>
          <Body>{t('character.report', { verdict: t(`character.verdict.${ch.report.verdict}`), method: ch.report.method })}</Body>
          <Body muted>{ch.report.verified ? t('character.verified') : t('character.measuredOnly')}</Body>
          {ch.report.metrics.failed.length ? <Body muted>{t('character.failedViews', { v: ch.report.metrics.failed.join(', ') })}</Body> : null}
        </Card>
      ) : null}
      {views.some((a) => a.status === 'failed' || a.status === 'rejected') && v.status !== 'locked' ? (
        <Button title={t('character.repair')} variant="secondary" onPress={() => paid('repair', 'repair')} />
      ) : null}
      {v.status === 'built' ? <Button title={t('character.lock')} onPress={() => act('lock')} /> : null}
    </ScrollView>
  );
}
