import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput, View } from 'react-native';

import { Body, Button, Card, Checkbox, Chip, Title } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Char = { id: string; handle: string; display_name: string; status: string; locked: boolean };
const AESTHETICS = ['photorealistic', 'stylized', 'animation'] as const;
const AGES = ['young_adult', 'adult', 'middle_aged', 'senior'] as const;

// Character Creator (V6): describe an original, adult, fictional actor; rights declaration is explicit.
// Everything else (previews, master, multi-angle sheet, lock) happens on the character screen.
export default function CharactersScreen() {
  const { t } = useTranslation();
  const c = useColors();
  const [items, setItems] = useState<Char[]>([]);
  const [state, setState] = useState<'loading' | 'disabled' | 'ready'>('loading');
  const [name, setName] = useState('');
  const [handle, setHandle] = useState('');
  const [desc, setDesc] = useState('');
  const [aesthetic, setAesthetic] = useState<(typeof AESTHETICS)[number]>('photorealistic');
  const [age, setAge] = useState<(typeof AGES)[number]>('adult');
  const [rights, setRights] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems((await api<{ items: Char[] }>('/characters')).items);
      await api('/characters/search');  // feature flag probe
      setState('ready');
    } catch (e) {
      setState(e instanceof ApiError && e.code === 'feature_disabled' ? 'disabled' : 'ready');
    }
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const create = async () => {
    try {
      const ch = await api<Char>('/characters', { body: {
        handle: handle.toLowerCase(), description: desc,
        spec: { display_name: name, aesthetic, age_appearance: age },
        rights: { original_creation: rights, no_real_person_likeness: rights, no_third_party_ip: rights,
                  adult_appearance: rights, accept_terms: rights },
      } });
      router.push(`/characters/${ch.id}`);
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    }
  };

  const input = { borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text };
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t('character.title')}</Title>
      {state === 'disabled' ? <Body muted>{t('character.comingSoon')}</Body> : null}
      {items.map((x) => (
        <Card key={x.id} style={{ gap: 4 }}>
          <Body onPress={() => router.push(`/characters/${x.id}`)}>{`${x.display_name} · ${x.handle}`}</Body>
          <Body muted>{t(`character.status.${x.status}`, { defaultValue: x.status })}{x.locked ? ` · ${t('character.lockedBadge')}` : ''}</Body>
        </Card>
      ))}
      {state === 'ready' ? (
        <Card style={{ gap: spacing.sm }}>
          <Body>{t('character.create')}</Body>
          <TextInput accessibilityLabel={t('character.name')} placeholder={t('character.name')} value={name}
            onChangeText={setName} maxLength={60} style={input} placeholderTextColor={c.textMuted} />
          <TextInput accessibilityLabel={t('character.handle')} placeholder={t('character.handle')} value={handle}
            onChangeText={setHandle} autoCapitalize="none" maxLength={32} style={input} placeholderTextColor={c.textMuted} />
          <TextInput accessibilityLabel={t('character.description')} placeholder={t('character.descriptionHint')}
            value={desc} onChangeText={setDesc} multiline maxLength={2000} placeholderTextColor={c.textMuted}
            style={[input, { minHeight: 90 }]} />
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {AESTHETICS.map((a) => <Chip key={a} label={t(`character.aesthetic.${a}`)} active={aesthetic === a} onPress={() => setAesthetic(a)} />)}
          </View>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {AGES.map((a) => <Chip key={a} label={t(`character.age.${a}`)} active={age === a} onPress={() => setAge(a)} />)}
          </View>
          <Checkbox checked={rights} onChange={setRights} label={t('character.rights')} />
          <Button title={t('character.createCta')} disabled={!rights || !name || handle.length < 2 || desc.length < 10}
            onPress={create} />
        </Card>
      ) : null}
    </ScrollView>
  );
}
