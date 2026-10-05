import { Image } from 'expo-image';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Alert, ScrollView, View } from 'react-native';

import { Body, Button, Card, Chip, Title } from '@/components/ui';
import { api, ApiError, newIdempotencyKey, type Generation, type Profile, type SourceVideo } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Assignments = Record<number, string | null>;

export default function PersonPicker() {
  const { videoId } = useLocalSearchParams<{ videoId: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [video, setVideo] = useState<SourceVideo | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [assign, setAssign] = useState<Assignments>({});
  const [quotes, setQuotes] = useState<{ full: number | null; preview: number | null }>({ full: null, preview: null });
  const [busy, setBusy] = useState(false);
  const keys = useRef({ full: newIdempotencyKey(), preview: newIdempotencyKey() });

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const v = await api<SourceVideo>(`/source-videos/${videoId}`);
      setVideo(v);
      if (v.status === 'analyzing' || v.status === 'pending_upload') timer = setTimeout(poll, 1500);
    };
    poll();
    api<Profile[]>('/identity-profiles').then((ps) => setProfiles(ps.filter((p) => p.status === 'ready')));
    return () => clearTimeout(timer);
  }, [videoId]);

  const chosen = Object.entries(assign).filter(([, p]) => p).map(([track, p]) => ({ track_id: Number(track), profile_id: p as string }));

  useEffect(() => {
    if (!chosen.length) { setQuotes({ full: null, preview: null }); return; }
    const body = (preview: boolean) => ({ assignments: chosen, resolution: '720x1280', preview });
    Promise.all([
      api<{ credits: number }>(`/source-videos/${videoId}/quote`, { body: body(false) }),
      api<{ credits: number }>(`/source-videos/${videoId}/quote`, { body: body(true) }),
    ]).then(([f, p]) => setQuotes({ full: f.credits, preview: p.credits })).catch(() => {});
    // assignment changes => new request identity
    keys.current = { full: newIdempotencyKey(), preview: newIdempotencyKey() };
  }, [JSON.stringify(chosen), videoId]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async (preview: boolean) => {
    setBusy(true);
    try {
      const job = await api<Generation>('/generations/multi', {
        body: { source_video_id: videoId, assignments: chosen, resolution: '720x1280', preview },
        idempotencyKey: preview ? keys.current.preview : keys.current.full,
      });
      router.push({ pathname: '/job/[id]', params: { id: job.id } });
    } catch (e) {
      if (e instanceof ApiError && e.code === 'insufficient_credits') router.push('/paywall');
      else Alert.alert(errorMessage(t, e));
    } finally {
      setBusy(false);
    }
  };

  if (!video || video.status === 'analyzing' || video.status === 'pending_upload') {
    return (
      <View style={{ flex: 1, backgroundColor: c.bg, alignItems: 'center', justifyContent: 'center', gap: spacing.md }}>
        <ActivityIndicator size="large" />
        <Body accessibilityLiveRegion="polite">{t('multi.analyzing')}</Body>
      </View>
    );
  }
  if (video.status !== 'ready') {
    return (
      <View style={{ flex: 1, backgroundColor: c.bg, padding: spacing.lg, gap: spacing.md }}>
        <Title>{t('multi.rejected')}</Title>
        <Body muted>{video.rejection_reason ?? ''}</Body>
      </View>
    );
  }
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title style={{ fontSize: 22 }}>{t('multi.people')}</Title>
      {video.persons.map((p) => (
        <Card key={p.track_id} style={{ gap: spacing.sm, opacity: p.selectable ? 1 : 0.6 }}>
          <View style={{ flexDirection: 'row', gap: spacing.md, alignItems: 'center' }}>
            <View style={{ width: 64, height: 96, borderRadius: radius.sm, overflow: 'hidden', backgroundColor: c.surfaceAlt }}>
              {p.thumbnail_url ? <Image source={{ uri: p.thumbnail_url }} style={{ flex: 1 }} contentFit="cover"
                accessibilityLabel={p.label} /> : null}
            </View>
            <View style={{ flex: 1 }}>
              <Body style={{ fontWeight: '800', fontSize: 18 }}>{p.label}</Body>
              {!p.selectable ? <Body muted>{t('multi.notSelectable')}</Body> : null}
            </View>
          </View>
          {p.selectable ? (
            <ScrollView horizontal contentContainerStyle={{ gap: spacing.sm }} showsHorizontalScrollIndicator={false}>
              <Chip label={t('multi.keep')} active={!assign[p.track_id]} onPress={() => setAssign((a) => ({ ...a, [p.track_id]: null }))} />
              {profiles.map((pr) => (
                <Chip key={pr.id} label={pr.name} active={assign[p.track_id] === pr.id}
                  onPress={() => setAssign((a) => ({ ...a, [p.track_id]: pr.id }))} />
              ))}
            </ScrollView>
          ) : null}
        </Card>
      ))}
      {quotes.preview != null ? (
        <Button title={t('multi.preview', { credits: quotes.preview })} variant="secondary" disabled={busy} onPress={() => start(true)} />
      ) : null}
      <Button title={t('multi.generate', { credits: quotes.full ?? 0 })} loading={busy} disabled={!chosen.length}
        onPress={() => start(false)} />
    </ScrollView>
  );
}
