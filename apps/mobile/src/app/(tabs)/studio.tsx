import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Pressable, ScrollView, TextInput } from 'react-native';

import { StoryEntry } from '@/components/StoryEntry';
import { Body, Button, Card, Chip, Title } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Item = { id: string; title: string; status: string; aspect_ratio: string };
const ASPECTS = ['9:16', '16:9', '1:1'] as const;
const MODES = ['faithful', 'balanced', 'experimental'] as const;

// AI Studio: brief -> storyboard (director) -> estimate -> confirm -> render. Plans are free; rendering needs
// the user's explicit confirmation of the exact credit amount (server-enforced).
export default function StudioTab() {
  const { t } = useTranslation();
  const c = useColors();
  const [items, setItems] = useState<Item[]>([]);
  const [disabled, setDisabled] = useState(false);
  const [brief, setBrief] = useState('');
  const [aspect, setAspect] = useState<(typeof ASPECTS)[number]>('9:16');
  const [mode, setMode] = useState<(typeof MODES)[number]>('balanced');
  const [busy, setBusy] = useState(false);

  useFocusEffect(useCallback(() => {
    api<{ items: Item[] }>('/studio/projects').then((r) => { setItems(r.items); setDisabled(false); })
      .catch((e) => { if (e instanceof ApiError && e.code === 'feature_disabled') setDisabled(true); });
  }, []));

  const create = async () => {
    setBusy(true);
    try {
      const p = await api<{ id: string }>('/studio/projects', { body: { title: brief.slice(0, 60), aspect_ratio: aspect } });
      await api(`/studio/projects/${p.id}/storyboard`, { body: { brief, aspect_ratio: aspect, target_duration_s: 24, creative_mode: mode } });
      setBrief('');
      router.push({ pathname: '/studio/[id]', params: { id: p.id } });
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    } finally {
      setBusy(false);
    }
  };

  if (disabled) {
    return <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg }}>
      <Title>{t('studio.title')}</Title><Body muted>{t('studio.comingSoon')}</Body></ScrollView>;
  }
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <StoryEntry />
      <Button variant="secondary" title={t('editor.title')} onPress={() => router.push('/editor')} />
      <Title>{t('studio.title')}</Title>
      <Body muted>{t('studio.subtitle')}</Body>
      <TextInput accessibilityLabel={t('studio.briefLabel')} placeholder={t('studio.briefPlaceholder')} multiline
        maxLength={4000} value={brief} onChangeText={setBrief} placeholderTextColor={c.textMuted}
        style={{ borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.md, color: c.text,
                 minHeight: 110, textAlignVertical: 'top' }} />
      <Card style={{ flexDirection: 'row', gap: spacing.sm }}>
        {ASPECTS.map((a) => <Chip key={a} label={a} active={aspect === a} onPress={() => setAspect(a)} />)}
      </Card>
      <Card style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
        {MODES.map((m) => <Chip key={m} label={t(`studio.mode.${m}`)} active={mode === m} onPress={() => setMode(m)} />)}
      </Card>
      <Body muted>{t(`studio.modeHint.${mode}`)}</Body>
      <Button title={t('studio.plan')} loading={busy} disabled={brief.trim().length < 10} onPress={create} />
      {items.map((p) => (
        <Pressable key={p.id} accessibilityRole="button" onPress={() => router.push({ pathname: '/studio/[id]', params: { id: p.id } })}>
          <Card><Body>{p.title}</Body><Body muted>{`${p.aspect_ratio} · ${t(`studio.status.${p.status}`)}`}</Body></Card>
        </Pressable>
      ))}
    </ScrollView>
  );
}
