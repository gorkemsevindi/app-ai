import * as ImagePicker from 'expo-image-picker';
import { router } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView } from 'react-native';

import { Body, Button, Checkbox, ProgressBar, Title } from '@/components/ui';
import { api, newIdempotencyKey, type Profile, uploadPresigned } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { spacing, useColors } from '@/lib/theme';

export default function NewIdentity() {
  const { t } = useTranslation();
  const c = useColors();
  const [consent, setConsent] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);

  const pickAndUpload = async () => {
    // The system photo picker needs no library permission on iOS 14+/Android 13+ (minimum permissions).
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'], allowsMultipleSelection: true, selectionLimit: 10, quality: 0.9, exif: false,
    });
    if (res.canceled) return;
    try {
      const p = profile ?? (await api<Profile>('/identity-profiles', { body: { name: 'Me', consent_own_likeness: true } }));
      setProfile(p);
      let last = p;
      setProgress({ done: 0, total: res.assets.length });
      for (const [i, a] of res.assets.entries()) {
        const mime = a.mimeType ?? 'image/jpeg';
        const signed = await api<{ asset_id: string; upload_url: string; fields: Record<string, string> }>('/uploads/sign', {
          body: { profile_id: p.id, mime, size_bytes: a.fileSize ?? 1 },
        });
        await uploadPresigned(signed.upload_url, signed.fields, { uri: a.uri, mimeType: mime, name: a.fileName ?? `${newIdempotencyKey()}.jpg` });
        last = await api<Profile>(`/identity-profiles/${p.id}/assets/${signed.asset_id}/complete`, { method: 'POST' });
        setProgress({ done: i + 1, total: res.assets.length });
      }
      setProfile(last);
      api('/events', { body: [{ name: 'identity_created', props: { status: last.status } }] }).catch(() => {});
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    } finally {
      setProgress(null);
    }
  };

  const photos = profile?.quality_report.photos ?? 0;
  const min = profile?.quality_report.min_photos ?? 5;
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t('identity.title')}</Title>
      <Body>{t('identity.explain')}</Body>
      <Checkbox checked={consent} onChange={setConsent} label={t('identity.consent')} />
      {progress ? (
        <>
          <Body>{t('identity.uploading', progress)}</Body>
          <ProgressBar value={progress.done / Math.max(1, progress.total)} />
        </>
      ) : null}
      {profile?.status === 'ready' ? (
        <>
          <Body style={{ color: c.success, fontWeight: '700' }}>{t('identity.ready')}</Body>
          <Button title="OK" onPress={() => router.back()} />
        </>
      ) : (
        <>
          {profile ? <Body muted>{t('identity.needMore', { count: Math.max(0, min - photos) })}</Body> : null}
          <Button title={t('identity.pick')} disabled={!consent || !!progress} onPress={pickAndUpload} />
        </>
      )}
    </ScrollView>
  );
}
