import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, TextInput, View } from 'react-native';

import { Body, Button, Card, Chip } from '@/components/ui';
import { api } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Member = { id: string; alias: string; handle: string; display_name: string; lock_mode: string;
  identity_version: { version: string }; license: { grant_id: string } | null };
type Resolution = { mention: string; status: string; candidates: { id: string; handle: string; display_name: string }[] };
const LOCKS = ['standard', 'strong', 'strict'] as const;

// Project cast (V6): @mentions resolve to frozen identity versions; ambiguous names are never resolved silently.
export function CastPanel({ projectId, onChange }: { projectId: string; onChange?: () => void }) {
  const { t } = useTranslation();
  const c = useColors();
  const [items, setItems] = useState<Member[]>([]);
  const [mention, setMention] = useState('');
  const [lock, setLock] = useState<(typeof LOCKS)[number]>('standard');
  const [choices, setChoices] = useState<Resolution | null>(null);

  const load = useCallback(async () => {
    try { setItems((await api<{ items: Member[] }>(`/studio/projects/${projectId}/cast`)).items); } catch { /* flag off */ }
  }, [projectId]);
  useEffect(() => { load(); }, [load]);

  const add = async (body: Record<string, unknown>) => {
    try {
      await api(`/studio/projects/${projectId}/cast`, { body: { lock_mode: lock, ...body } });
      setMention(''); setChoices(null); await load(); onChange?.();
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };
  const resolve = async () => {
    try {
      const r = await api<Resolution>(`/characters/resolve?mention=${encodeURIComponent(mention)}&project_id=${projectId}`);
      if (r.status === 'needs_cast' && r.candidates.length === 1) await add({ character_id: r.candidates[0].id });
      else setChoices(r);
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };

  return (
    <Card style={{ gap: spacing.sm }}>
      <Body>{t('cast.title')}</Body>
      {items.map((m) => (
        <Body key={m.id} muted>{`@${m.alias} → ${m.display_name} (${m.handle} v${m.identity_version.version}) · ${t(`cast.lock.${m.lock_mode}`)}${m.license ? ` · ${t('cast.licensed')}` : ''}`}</Body>
      ))}
      <TextInput accessibilityLabel={t('cast.mention')} placeholder="@name" value={mention} onChangeText={setMention}
        autoCapitalize="none" placeholderTextColor={c.textMuted}
        style={{ borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text }} />
      <View style={{ flexDirection: 'row', gap: 6 }}>
        {LOCKS.map((l) => <Chip key={l} label={t(`cast.lock.${l}`)} active={lock === l} onPress={() => setLock(l)} />)}
      </View>
      <Body muted>{t(`cast.lockHint.${lock}`)}</Body>
      {choices ? (
        <View style={{ gap: 4 }}>
          <Body>{t(`cast.resolution.${choices.status}`, { defaultValue: choices.status })}</Body>
          {choices.candidates.map((x) => <Chip key={x.id} label={`${x.display_name} ${x.handle}`} onPress={() => add({ character_id: x.id })} />)}
        </View>
      ) : null}
      <Button title={t('cast.add')} variant="secondary" disabled={!mention.startsWith('@') || mention.length < 3} onPress={resolve} />
    </Card>
  );
}
