import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput, View } from 'react-native';

import { Body, Button, Chip, Title } from '@/components/ui';
import { api } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

const FORMATS = { micro: 90, short_series: 180, standard_episode: 480, long_episode: 1200, short_film: 900, feature: 3600 } as const;
const STYLES = ['photoreal', 'cinematic', 'cartoon_2d', 'animation_3d', 'anime', 'stylized', 'mixed'] as const;
const RATINGS = ['general', 'teen', 'mature'] as const;
const PROFILES = ['economy', 'standard', 'cinema_pro'] as const;

// Production wizard (series / film): format & length, style, rating, budget profile. Plans are free.
export default function NewProduction() {
  const { kind = 'series' } = useLocalSearchParams<{ kind?: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [title, setTitle] = useState('');
  const [logline, setLogline] = useState('');
  const [format, setFormat] = useState<keyof typeof FORMATS>(kind === 'film' ? 'short_film' : 'short_series');
  const [style, setStyle] = useState<(typeof STYLES)[number]>('cinematic');
  const [rating, setRating] = useState<(typeof RATINGS)[number]>('general');
  const [profile, setProfile] = useState<(typeof PROFILES)[number]>('standard');
  const [episodes, setEpisodes] = useState('5');
  const [minutes, setMinutes] = useState(String(FORMATS[format] / 60));

  const create = async () => {
    try {
      const p = await api<{ id: string }>('/productions', { body: {
        kind, title, logline, format, visual_style: style, content_rating: rating, profile,
        episodes_per_season: kind === 'series' ? Math.max(1, Number(episodes) || 1) : 1,
        episode_duration_s: Math.round((Number(minutes) || 1) * 60),
      } });
      router.replace({ pathname: '/productions/[id]', params: { id: p.id } });
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };
  const input = { borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text };
  const row = { flexDirection: 'row' as const, flexWrap: 'wrap' as const, gap: 6 };
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t(`production.entry.${kind}`)}</Title>
      <TextInput accessibilityLabel={t('production.titleLabel')} placeholder={t('production.titleLabel')} value={title}
        onChangeText={setTitle} maxLength={120} style={input} placeholderTextColor={c.textMuted} />
      <TextInput accessibilityLabel={t('production.logline')} placeholder={t('production.logline')} value={logline}
        onChangeText={setLogline} maxLength={600} multiline style={[input, { minHeight: 70 }]} placeholderTextColor={c.textMuted} />
      <Body>{t('production.format')}</Body>
      <View style={row}>{(Object.keys(FORMATS) as (keyof typeof FORMATS)[]).map((f) => (
        <Chip key={f} label={t(`production.formats.${f}`)} active={format === f}
          onPress={() => { setFormat(f); setMinutes(String(FORMATS[f] / 60)); }} />))}</View>
      <View style={{ flexDirection: 'row', gap: spacing.sm }}>
        {kind === 'series' ? <TextInput accessibilityLabel={t('production.episodes')} keyboardType="number-pad" value={episodes}
          onChangeText={setEpisodes} style={[input, { flex: 1 }]} /> : null}
        <TextInput accessibilityLabel={t('production.minutes')} keyboardType="decimal-pad" value={minutes}
          onChangeText={setMinutes} style={[input, { flex: 1 }]} />
      </View>
      <Body muted>{t('production.lengthHint')}</Body>
      <Body>{t('production.style')}</Body>
      <View style={row}>{STYLES.map((s) => <Chip key={s} label={t(`production.styles.${s}`)} active={style === s} onPress={() => setStyle(s)} />)}</View>
      <Body>{t('production.rating')}</Body>
      <View style={row}>{RATINGS.map((r) => <Chip key={r} label={t(`production.ratings.${r}`)} active={rating === r} onPress={() => setRating(r)} />)}</View>
      <Body muted>{t(`production.ratingHint.${rating}`)}</Body>
      <Body>{t('production.profile')}</Body>
      <View style={row}>{PROFILES.map((p) => <Chip key={p} label={t(`production.profiles.${p}`)} active={profile === p} onPress={() => setProfile(p)} />)}</View>
      <Button title={t('production.create')} disabled={!title} onPress={create} />
    </ScrollView>
  );
}
