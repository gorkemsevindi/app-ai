import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput } from 'react-native';

import { Body, Button, Card, Checkbox, Title } from '@/components/ui';
import { api } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Q = { key: string; prompt: string };
type Privacy = { possible_people: string[]; warnings: string[] };

// "Your life. Your story.": optional guided questions → chronology → privacy check → approval. Private by default.
export default function LifeStory() {
  const { t } = useTranslation();
  const c = useColors();
  const [sid, setSid] = useState<string | null>(null);
  const [qs, setQs] = useState<Q[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [privacy, setPrivacy] = useState<Privacy | null>(null);
  const [title, setTitle] = useState('');
  const [ok, setOk] = useState(false);

  useEffect(() => {
    api<{ id: string; questions: Q[] }>('/life-stories', { method: 'POST' })
      .then((r) => { setSid(r.id); setQs(r.questions); }).catch((e) => Alert.alert(errorMessage(t, e)));
  }, [t]);

  const draft = async () => {
    try {
      for (const [k, v] of Object.entries(answers)) if (v.trim()) await api(`/life-stories/${sid}/answers`, { method: 'PUT', body: { key: k, text: v } });
      setPrivacy((await api<{ privacy: Privacy }>(`/life-stories/${sid}/chronology`, { body: {} })).privacy);
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };
  const approve = async () => {
    try {
      const r = await api<{ production: { id: string } }>(`/life-stories/${sid}/approve`, { body: { confirm_privacy: ok, title } });
      router.replace({ pathname: '/productions/[id]', params: { id: r.production.id } });
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };
  const input = { borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text };
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t('production.entry.life_story')}</Title>
      <Body muted>{t('life.private')}</Body>
      {qs.map((q) => (
        <TextInput key={q.key} accessibilityLabel={t(`life.q.${q.key}`)} placeholder={t(`life.q.${q.key}`)} multiline
          value={answers[q.key] ?? ''} onChangeText={(v) => setAnswers({ ...answers, [q.key]: v })}
          style={[input, { minHeight: 60 }]} placeholderTextColor={c.textMuted} />
      ))}
      <Button title={t('life.draft')} variant="secondary" onPress={draft} />
      {privacy ? (
        <Card style={{ gap: 4 }}>
          {privacy.warnings.map((w, i) => <Body key={i}>{`⚠︎ ${w}`}</Body>)}
          {privacy.possible_people.length ? <Body muted>{t('life.people', { names: privacy.possible_people.join(', ') })}</Body> : null}
          <TextInput accessibilityLabel={t('production.titleLabel')} placeholder={t('production.titleLabel')} value={title}
            onChangeText={setTitle} style={input} placeholderTextColor={c.textMuted} />
          <Checkbox checked={ok} onChange={setOk} label={t('life.confirm')} />
          <Button title={t('life.approve')} disabled={!ok || !title} onPress={approve} />
        </Card>
      ) : null}
    </ScrollView>
  );
}
