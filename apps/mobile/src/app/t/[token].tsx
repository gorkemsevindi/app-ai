import { Redirect, router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Platform, View } from 'react-native';

import { Body, Button, Screen, Title } from '@/components/ui';
import { api } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { claimPendingAttribution, rememberShareToken } from '@/lib/referral';
import { isShareToken } from '@/lib/shareToken';

type Resolved = { status: 'ok' | 'unavailable'; template: { id: string; title: string } | null };

// Universal link / app link / custom scheme target: https://<host>/t/<token> or aivideo://t/<token>.
export default function ShareLinkScreen() {
  const { token } = useLocalSearchParams<{ token: string }>();
  const { t } = useTranslation();
  const { me, loading } = useAuth();
  const [res, setRes] = useState<Resolved | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!isShareToken(token)) { setFailed(true); return; }
    rememberShareToken(token).catch(() => {});
    api<Resolved>(`/share-links/${encodeURIComponent(token)}?source=app&platform=${Platform.OS}`, { auth: false })
      .then(setRes).catch(() => setFailed(true));
  }, [token]);

  useEffect(() => {
    if (me && res?.status === 'ok' && res.template) {
      claimPendingAttribution().finally(() =>
        router.replace({ pathname: '/template/[id]', params: { id: res.template!.id } }));
    }
  }, [me, res]);

  if (failed || res?.status === 'unavailable') {
    return (
      <Screen style={{ gap: 16 }}>
        <Title>{t('share.unavailable')}</Title>
        <Button title={t('share.browse')} onPress={() => router.replace('/')} />
      </Screen>
    );
  }
  if (!loading && !me && res?.status === 'ok') return <Redirect href="/onboarding" />;  // token kept for after sign-up
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 }}>
      <ActivityIndicator />
      {res?.template ? <Body>{res.template.title}</Body> : null}
    </View>
  );
}
