import { useLocalSearchParams } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, View } from 'react-native';

import { Body, Button, Card, Title } from '@/components/ui';
import { api, ApiError, type StudioEstimate, type StudioProject } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

function newKey() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export default function StudioProjectScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [p, setP] = useState<StudioProject | null>(null);
  const [est, setEst] = useState<StudioEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const idem = useRef(newKey());
  const player = useVideoPlayer(p?.output?.video_url ?? null, (pl) => { pl.loop = true; });

  const load = useCallback(async () => {
    const proj = await api<StudioProject>(`/studio/projects/${id}`);
    setP(proj);
    if (proj.current_version) {
      setEst(await api<StudioEstimate>(`/studio/projects/${id}/estimate`, { body: { version_id: proj.current_version.id } }));
    }
    return proj;
  }, [id]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const tick = async () => {
      try {
        const proj = await load();
        if (alive && proj.status === 'rendering') timer = setTimeout(tick, 4000);
      } catch { if (alive) timer = setTimeout(tick, 8000); }
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [load]);

  const render = () => {
    if (!est) return;
    const go = async () => {
      setBusy(true);
      try {
        await api(`/studio/projects/${id}/render`, {
          body: { version_id: est.version_id, confirmed_credits: est.credits }, idempotencyKey: idem.current,
        });
        idem.current = newKey();
        await load();
      } catch (e) {
        if (e instanceof ApiError && e.code === 'confirmation_required') await load();  // price changed: re-confirm
        Alert.alert(errorMessage(t, e));
      } finally {
        setBusy(false);
      }
    };
    Alert.alert(t('studio.confirm', { credits: est.credits }), t('studio.confirmDetail', { n: est.new_shots, r: est.reused_shots }), [
      { text: t('job.cancel'), style: 'cancel' }, { text: 'OK', onPress: go },
    ]);
  };

  if (!p) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const sb = p.current_version?.storyboard;
  const statusOf = (k: string) => p.shots.find((s) => s.key === k)?.status;
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{sb?.title ?? p.title}</Title>
      <Body muted>{`${t(`studio.status.${p.status}`)} · ${p.current_version?.director?.label ?? ''}`}</Body>
      {p.output ? (
        <View style={{ aspectRatio: p.aspect_ratio === '16:9' ? 16 / 9 : p.aspect_ratio === '1:1' ? 1 : 9 / 16,
                       borderRadius: radius.lg, overflow: 'hidden', backgroundColor: '#000' }}>
          <VideoView player={player} style={{ flex: 1 }} fullscreenOptions={{ enable: true }} />
        </View>
      ) : null}
      {sb?.scenes.flatMap((sc) => sc.shots).map((s, i) => (
        <Card key={s.key} style={{ gap: 4 }}>
          <Body>{`${i + 1}. ${s.duration_s}s · ${s.camera ?? ''}${statusOf(s.key) ? ` · ${t(`studio.shot.${statusOf(s.key)}`)}` : ''}`}</Body>
          <Body muted>{s.prompt}</Body>
          {s.caption ? <Body>{`“${s.caption}”`}</Body> : null}
          {(s.dialogue ?? []).map((d, j) => <Body key={j}>{`${d.character ?? ''}: ${d.text}`}</Body>)}
        </Card>
      ))}
      {[...(est?.limitations ?? [])].map((l, i) => <Body key={i} muted>{`⚠︎ ${l}`}</Body>)}
      {est ? (
        <Body>{t('studio.estimate', { credits: est.credits, n: est.new_shots, r: est.reused_shots, balance: est.balance })}</Body>
      ) : null}
      {p.status !== 'rendering' && est ? (
        <Button title={t('studio.render', { credits: est.credits })} loading={busy}
          disabled={est.blocked.length > 0 || est.missing_capabilities.length > 0} onPress={render} />
      ) : null}
    </ScrollView>
  );
}
