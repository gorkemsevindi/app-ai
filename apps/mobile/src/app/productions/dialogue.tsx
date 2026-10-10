import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput, View } from 'react-native';

import { Body, Button, Card, Chip, Title } from '@/components/ui';
import { api } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Line = { id: string; character: string; text: string; emotion?: string; delivery?: string; locked?: boolean; exact?: boolean };
type Episode = { project: { id: string } };
const DELIVERY = ['normal', 'shout', 'whisper', 'cry', 'sarcastic', 'angry', 'calm'] as const;

// Exact Dialogue editor: each line keeps its id; the text is never rewritten. Preview the impact, then apply.
export default function DialogueEditor() {
  const { id, ep } = useLocalSearchParams<{ id: string; ep: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [lines, setLines] = useState<Line[]>([]);
  const [sel, setSel] = useState<Line | null>(null);
  const [text, setText] = useState('');
  const [delivery, setDelivery] = useState<string | undefined>();
  const [projectId, setProjectId] = useState<string | null>(null);
  const base = `/productions/${id}/episodes/${ep}/dialogue`;

  const load = useCallback(async () => {
    setLines((await api<{ items: Line[] }>(base)).items);
    setProjectId((await api<Episode>(`/productions/${id}/episodes/${ep}`)).project.id);
  }, [base, id, ep]);
  useEffect(() => { load().catch(() => undefined); }, [load]);

  const save = async (apply: boolean, extra: Record<string, unknown> = {}, unlock = false) => {
    if (!sel) return;
    try {
      const r = await api<{ impact: { levels: string[]; credits_delta: number } }>(
        `${base}/${sel.id}?apply=${apply}&unlock=${unlock}`, { method: 'PATCH', body: { text, delivery, ...extra } });
      if (!apply) {
        Alert.alert(t('dialogue.impact', { levels: r.impact.levels.map((l) => t(`dialogue.level.${l}`, { defaultValue: l })).join(', '),
          credits: r.impact.credits_delta }), '', [{ text: t('job.cancel'), style: 'cancel' }, { text: 'OK', onPress: () => save(true, extra, unlock) }]);
      } else { setSel(null); await load(); }
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };
  const step = async (kind: 'undo' | 'redo') => {
    try { await api(`/studio/projects/${projectId}/${kind}`, { method: 'POST' }); await load(); }
    catch (e) { Alert.alert(errorMessage(t, e)); }
  };

  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t('dialogue.title')}</Title>
      <Body muted>{t('dialogue.exactHint')}</Body>
      <View style={{ flexDirection: 'row', gap: spacing.sm }}>
        <Button title={t('studio.edit.undo')} variant="secondary" onPress={() => step('undo')} />
        <Button title={t('dialogue.redo')} variant="secondary" onPress={() => step('redo')} />
      </View>
      {lines.map((l) => (
        <Card key={l.id} style={{ gap: 4 }}>
          <Body onPress={() => { setSel(l); setText(l.text); setDelivery(l.delivery); }}>{`${l.character}: ${l.text}`}</Body>
          <Body muted>{[l.delivery, l.emotion, l.locked ? '🔒' : null, l.exact ? t('dialogue.exact') : null].filter(Boolean).join(' · ')}</Body>
          {sel?.id === l.id ? (
            <View style={{ gap: spacing.sm }}>
              <TextInput accessibilityLabel={t('dialogue.text')} value={text} onChangeText={setText} multiline maxLength={600}
                style={{ borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text }} />
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                {DELIVERY.map((d) => <Chip key={d} label={t(`dialogue.delivery.${d}`)} active={delivery === d} onPress={() => setDelivery(d)} />)}
              </View>
              <Button title={t('dialogue.preview')} onPress={() => save(false, {}, !!l.locked)} />
              <Button title={l.locked ? t('dialogue.unlock') : t('dialogue.lock')} variant="secondary"
                onPress={() => save(true, { locked: !l.locked, text: l.text })} />
            </View>
          ) : null}
        </Card>
      ))}
    </ScrollView>
  );
}
