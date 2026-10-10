import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Pressable, ScrollView, Text, View } from 'react-native';

import { Body, Button, Card, Chip, Title } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { spacing, useColors } from '@/lib/theme';

type Row = { id: string; type: string; title: string; revision: number; updated_at: string };

// V8: list of canonical editor projects shared with the web app; opening one edits the same document.
export default function EditorProjects() {
  const { t } = useTranslation();
  const c = useColors();
  const [items, setItems] = useState<Row[]>([]);
  const [disabled, setDisabled] = useState(false);
  const [type, setType] = useState<'video' | 'photo'>('video');

  useFocusEffect(useCallback(() => {
    api<{ items: Row[] }>('/editor/projects').then((r) => setItems(r.items)).catch(() => {});
    api('/editor/config').then((r: { enabled: boolean }) => setDisabled(!r.enabled)).catch(() => {});
  }, []));

  const create = async () => {
    try {
      const p = await api<{ id: string }>('/editor/projects', { body: { type, title: t('editor.new'),
        preset: type === 'photo' ? 'poster_a4' : 'vertical_1080' } });
      router.push({ pathname: '/editor/[id]', params: { id: p.id } });
    } catch (e) {
      Alert.alert(e instanceof ApiError && e.code === 'feature_disabled' ? t('editor.disabled') : errorMessage(t, e));
    }
  };

  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t('editor.title')}</Title>
      <Body muted>{t('editor.subtitle')}</Body>
      {disabled ? <Body muted>{t('editor.disabled')}</Body> : null}
      <View style={{ flexDirection: 'row', gap: spacing.sm }}>
        <Chip label={t('editor.video')} active={type === 'video'} onPress={() => setType('video')} />
        <Chip label={t('editor.photo')} active={type === 'photo'} onPress={() => setType('photo')} />
      </View>
      <Button title={t('editor.new')} onPress={create} disabled={disabled} />
      {items.length === 0 ? <Body muted>{t('editor.empty')}</Body> : null}
      {items.map((p) => (
        <Pressable key={p.id} accessibilityRole="button" accessibilityLabel={p.title}
                   onPress={() => router.push({ pathname: '/editor/[id]', params: { id: p.id } })}>
          <Card style={{ gap: 2 }}>
            <Text style={{ color: c.text, fontWeight: '700', fontSize: 16 }}>{p.title}</Text>
            <Text style={{ color: c.textMuted, fontSize: 13 }}>{p.type} · r{p.revision} · {new Date(p.updated_at).toLocaleString()}</Text>
          </Card>
        </Pressable>
      ))}
    </ScrollView>
  );
}
