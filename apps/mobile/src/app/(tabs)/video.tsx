import * as ImagePicker from 'expo-image-picker';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView } from 'react-native';

import { Body, Button, Checkbox, Title } from '@/components/ui';
import { api, type MultiConfig, type SourceVideo, uploadPresigned } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { spacing, useColors } from '@/lib/theme';

export default function YourVideo() {
  const { t } = useTranslation();
  const c = useColors();
  const [cfg, setCfg] = useState<MultiConfig | null>(null);
  const [rights, setRights] = useState(false);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  useFocusEffect(useCallback(() => { api<MultiConfig>('/multi-person/config', { auth: false }).then(setCfg).catch(() => {}); }, []));

  const pick = async () => {
    if (!cfg) return;
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['videos'], videoMaxDuration: cfg.max_duration_s, quality: 1 });
    if (res.canceled) return;
    const a = res.assets[0];
    if (a.duration && (a.duration / 1000 > cfg.max_duration_s || a.duration / 1000 < cfg.min_duration_s)) {
      Alert.alert(t('multi.pick', { min: cfg.min_duration_s, max: cfg.max_duration_s }));
      return;
    }
    setBusy(true);
    try {
      const mime = a.mimeType ?? 'video/mp4';
      const v = await api<{ id: string; upload_url: string; fields: Record<string, string> }>('/source-videos', {
        body: { mime, size_bytes: a.fileSize ?? 1, owns_rights: rights, people_consented: consent },
      });
      await uploadPresigned(v.upload_url, v.fields, { uri: a.uri, mimeType: mime, name: a.fileName ?? 'video.mp4' });
      await api<SourceVideo>(`/source-videos/${v.id}/complete`, { method: 'POST' });
      router.push({ pathname: '/multi/[videoId]', params: { videoId: v.id } });
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{t('multi.title')}</Title>
      {cfg && !cfg.enabled ? <Body muted>{t('multi.disabled')}</Body> : null}
      {cfg?.enabled ? (
        <>
          <Body>{t('multi.intro', { max: cfg.max_persons })}</Body>
          <Checkbox checked={rights} onChange={setRights} label={t('multi.rights')} />
          <Checkbox checked={consent} onChange={setConsent} label={t('multi.consent')} />
          <Button title={t('multi.pick', { min: cfg.min_duration_s, max: cfg.max_duration_s })} loading={busy}
            disabled={!(rights && consent)} onPress={pick} />
        </>
      ) : null}
    </ScrollView>
  );
}
