import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput } from 'react-native';

import { Body, Button, Card, Checkbox, Title } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Profile = { handle: string; display_name: string; payout_status: string; status: string };
type Balances = { balance_micros: number; available_micros: number; pending_micros: number; paid_micros: number };
type Earnings = { balances: Balances; settlements: { id: string; amount_micros: number; status: string }[] };
type Tpl = { id: string; title: string; moderation_status: string; visibility: string };

const usd = (micros: number) => `$${(micros / 1_000_000).toFixed(2)}`;

// Creator hub: onboarding (terms), earnings (pending = inside the refund/fraud hold window) and template review
// status. Amounts come from the server's append-only creator ledger; nothing is computed on the device.
export default function CreatorScreen() {
  const { t } = useTranslation();
  const c = useColors();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [earn, setEarn] = useState<Earnings | null>(null);
  const [tpls, setTpls] = useState<Tpl[]>([]);
  const [state, setState] = useState<'loading' | 'none' | 'disabled' | 'ready'>('loading');
  const [handle, setHandle] = useState('');
  const [name, setName] = useState('');
  const [terms, setTerms] = useState(false);

  const load = useCallback(async () => {
    try {
      setProfile(await api<Profile>('/creator/profile'));
      setEarn(await api<Earnings>('/creator/earnings'));
      setTpls((await api<{ items: Tpl[] }>('/creator/templates')).items);
      setState('ready');
    } catch (e) {
      setState(e instanceof ApiError && e.code === 'feature_disabled' ? 'disabled' : 'none');
    }
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const join = async () => {
    try {
      await api('/creator/profile', { body: { handle, display_name: name, accept_terms: terms } });
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'feature_disabled') setState('disabled');
      else Alert.alert(errorMessage(t, e));
    }
  };

  const input = { borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text };
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t('creator.title')}</Title>
      {state === 'disabled' ? <Body muted>{t('creator.comingSoon')}</Body> : null}
      {state === 'none' ? (
        <Card style={{ gap: spacing.sm }}>
          <Body>{t('creator.join')}</Body>
          <TextInput accessibilityLabel={t('creator.handle')} placeholder={t('creator.handle')} autoCapitalize="none"
            value={handle} onChangeText={setHandle} style={input} placeholderTextColor={c.textMuted} />
          <TextInput accessibilityLabel={t('creator.name')} placeholder={t('creator.name')} value={name}
            onChangeText={setName} style={input} placeholderTextColor={c.textMuted} />
          <Checkbox checked={terms} onChange={setTerms} label={t('creator.terms')} />
          <Button title={t('creator.joinCta')} disabled={!terms || handle.length < 3 || !name} onPress={join} />
        </Card>
      ) : null}
      {state === 'ready' && profile && earn ? (
        <>
          <Body muted>{`@${profile.handle} · ${t(`creator.payout.${profile.payout_status}`)}`}</Body>
          <Card style={{ gap: 4 }}>
            <Body>{t('creator.balance', { v: usd(earn.balances.balance_micros) })}</Body>
            <Body muted>{t('creator.available', { v: usd(earn.balances.available_micros) })}</Body>
            <Body muted>{t('creator.pending', { v: usd(earn.balances.pending_micros) })}</Body>
            <Body muted>{t('creator.paid', { v: usd(earn.balances.paid_micros) })}</Body>
          </Card>
          {tpls.map((x) => (
            <Card key={x.id}><Body>{x.title}</Body><Body muted>{t(`creator.review.${x.moderation_status}`)}</Body></Card>
          ))}
        </>
      ) : null}
    </ScrollView>
  );
}
