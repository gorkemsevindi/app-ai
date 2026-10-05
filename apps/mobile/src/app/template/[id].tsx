import { router, useLocalSearchParams } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput, View } from 'react-native';

import { Body, Button, Title } from '@/components/ui';
import { api, ApiError, newIdempotencyKey, type Generation, type Profile, type Template } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { formatEta } from '@/lib/progress';
import { radius, spacing, useColors } from '@/lib/theme';

export default function TemplateDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [tpl, setTpl] = useState<Template | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  // One key per screen visit: double taps / network retries can't create (or charge) twice.
  const idemKey = useRef(newIdempotencyKey());
  const player = useVideoPlayer(tpl?.preview_url?.startsWith('http') ? tpl.preview_url : null, (p) => {
    p.loop = true;
    p.muted = true;
    p.play();
  });

  useEffect(() => {
    api<Template>(`/templates/${id}`).then(setTpl);
    api<Profile[]>('/identity-profiles').then((ps) => setProfile(ps.find((p) => p.status === 'ready') ?? null));
  }, [id]);

  const generate = async () => {
    if (!profile) return router.push('/identity/new');
    setBusy(true);
    try {
      const job = await api<Generation>('/generations', {
        body: { template_id: id, profile_id: profile.id, text: text || null },
        idempotencyKey: idemKey.current,
      });
      router.replace({ pathname: '/job/[id]', params: { id: job.id } });
    } catch (e) {
      if (e instanceof ApiError && (e.code === 'insufficient_credits' || e.code === 'pro_required')) {
        router.push('/paywall');
      } else {
        Alert.alert(errorMessage(t, e));
      }
    } finally {
      setBusy(false);
    }
  };

  if (!tpl) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const eta = formatEta(tpl.est_seconds);
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <View style={{ aspectRatio: 9 / 16, maxHeight: 460, alignSelf: 'center', width: '70%', borderRadius: radius.lg,
                     overflow: 'hidden', backgroundColor: c.surfaceAlt }}>
        {tpl.preview_url?.startsWith('http') ? <VideoView player={player} style={{ flex: 1 }} nativeControls={false} /> : null}
      </View>
      <Title>{tpl.title}</Title>
      <Body muted>{tpl.description}</Body>
      <Body>{`${t('template.cost', { count: tpl.credit_cost })}${eta ? ` · ${t('template.eta', { eta })}` : ''}`}</Body>
      {tpl.accepts_text ? (
        <TextInput accessibilityLabel={t('template.addText')} placeholder={t('template.addText')} maxLength={200}
          placeholderTextColor={c.textMuted} value={text} onChangeText={setText}
          style={{ borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.md, color: c.text, minHeight: 50 }} />
      ) : null}
      <Button title={profile ? t('template.generate') : t('discover.createProfile')} loading={busy} onPress={generate} />
    </ScrollView>
  );
}
