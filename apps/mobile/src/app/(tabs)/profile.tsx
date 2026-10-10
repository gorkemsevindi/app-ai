import { Image } from 'expo-image';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, View } from 'react-native';

import { Body, Button, Card, Chip, Title } from '@/components/ui';
import { api, type Profile } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import i18n, { SUPPORTED } from '@/lib/i18n';
import { radius, spacing, useColors } from '@/lib/theme';

export default function ProfileTab() {
  const { t } = useTranslation();
  const c = useColors();
  const { me, signOut, refreshMe } = useAuth();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [consent, setConsent] = useState<Record<string, boolean> | null>(null);
  useFocusEffect(useCallback(() => {
    refreshMe();
    api<Profile[]>('/identity-profiles').then(setProfiles).catch(() => {});
    api<{ consents: Record<string, boolean> }>('/me/learning-consent').then((r) => setConsent(r.consents)).catch(() => {});
  }, [refreshMe]));

  const deleteProfile = (p: Profile) =>
    Alert.alert(t('identity.delete'), undefined, [
      { text: '✕', style: 'cancel' },
      { text: t('identity.delete'), style: 'destructive',
        onPress: async () => { await api(`/identity-profiles/${p.id}`, { method: 'DELETE' }); setProfiles((x) => x.filter((y) => y.id !== p.id)); } },
    ]);

  const deleteAccount = () =>
    Alert.alert(t('profile.deleteAccount'), t('profile.deleteConfirm'), [
      { text: '✕', style: 'cancel' },
      { text: t('profile.deleteAccount'), style: 'destructive', onPress: async () => {
        await api('/account', { method: 'DELETE' });
        await signOut();
        Alert.alert(t('profile.deleteDone'));
        router.replace('/onboarding');
      } },
    ]);

  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.lg }}>
      <Card style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <View>
          <Body muted>{t('profile.credits')}</Body>
          <Title>{me?.credits ?? 0}</Title>
          <Body muted>{`${t('profile.plan')}: ${me?.plan ?? 'free'}`}</Body>
        </View>
        <Button title="+" onPress={() => router.push('/paywall')} />
      </Card>
      <Title style={{ fontSize: 20 }}>{t('profile.faceProfiles')}</Title>
      {profiles.map((p) => (
        <Card key={p.id} style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.md }}>
          <View style={{ width: 56, height: 56, borderRadius: radius.pill, overflow: 'hidden', backgroundColor: c.surfaceAlt }}>
            {p.thumbnail_url ? <Image source={{ uri: p.thumbnail_url }} style={{ flex: 1 }} /> : null}
          </View>
          <View style={{ flex: 1 }}>
            <Body style={{ fontWeight: '700' }}>{p.name}</Body>
            <Body muted>{p.status}</Body>
          </View>
          <Body style={{ color: c.danger }} onPress={() => deleteProfile(p)} accessibilityRole="button">✕</Body>
        </Card>
      ))}
      <Button title={t('profile.newProfile')} variant="secondary" onPress={() => router.push('/identity/new')} />
      <Button title={t('creator.title')} variant="secondary" onPress={() => router.push('/creator')} />
      <Title style={{ fontSize: 20 }}>{t('profile.language')}</Title>
      <View style={{ flexDirection: 'row', gap: spacing.sm }}>
        {SUPPORTED.map((l) => (
          <Chip key={l} label={l.toUpperCase()} active={i18n.language === l} onPress={() => {
            i18n.changeLanguage(l);
            api('/me', { method: 'PATCH', body: { locale: l } }).catch(() => {});
          }} />
        ))}
      </View>
      {consent ? (
        <Card style={{ gap: spacing.sm }}>
          <Body>{t('learning.title')}</Body>
          {(['technical_improvement', 'content_training', 'personalization'] as const).map((k) => (
            <Chip key={k} label={`${t(`learning.${k}`)}: ${consent[k] ? t('learning.on') : t('learning.off')}`} active={consent[k]}
              onPress={() => api<{ consents: Record<string, boolean> }>('/me/learning-consent', { method: 'PUT', body: { [k]: !consent[k] } })
                .then((r) => setConsent(r.consents)).catch(() => {})} />
          ))}
          <Body muted>{t('learning.note')}</Body>
          <Button title={t('learning.delete')} variant="secondary" onPress={() => api<{ consents: Record<string, boolean> }>('/me/learning-data', { method: 'DELETE' })
            .then((r) => { setConsent(r.consents); Alert.alert(t('learning.deleted')); }).catch(() => {})} />
        </Card>
      ) : null}
      <Button title={t('profile.signOut')} variant="secondary" onPress={async () => { await signOut(); router.replace('/onboarding'); }} />
      <Button title={t('profile.deleteAccount')} variant="danger" onPress={deleteAccount} />
    </ScrollView>
  );
}
