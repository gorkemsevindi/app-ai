import * as FileSystem from 'expo-file-system';
import * as MediaLibrary from 'expo-media-library';
import { router, useLocalSearchParams } from 'expo-router';
import * as Sharing from 'expo-sharing';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActionSheetIOS, Alert, Platform, ScrollView, View } from 'react-native';

import { Body, Button, Chip, ProgressBar, Title } from '@/components/ui';
import { api, type Generation } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { shareTemplateLink } from '@/lib/referral';
import { displayProgress, formatEta, nextPollMs, stageKey, TERMINAL } from '@/lib/progress';
import { radius, spacing, useColors } from '@/lib/theme';

const REPORT_REASONS = ['sexual', 'minor_safety', 'impersonation', 'harassment', 'violence', 'copyright', 'low_quality', 'other'];

export default function JobScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [job, setJob] = useState<Generation | null>(null);
  const [rating, setRating] = useState<number | null>(null);
  const player = useVideoPlayer(job?.output?.video_url ?? null, (p) => { p.loop = true; p.play(); });

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let alive = true;
    const tick = async () => {
      try {
        const j = await api<Generation>(`/generations/${id}`);
        if (!alive) return;
        setJob(j);
        const next = nextPollMs(j, attempt++);
        if (next) timer = setTimeout(tick, next);
      } catch {
        timer = setTimeout(tick, 5000);
      }
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [id]);

  const download = async () => {
    if (!job?.output) return;
    const perm = await MediaLibrary.requestPermissionsAsync(true); // write-only: ask only when saving
    if (!perm.granted) return;
    const file = await FileSystem.File.downloadFileAsync(job.output.video_url, FileSystem.Paths.cache, { idempotent: true });
    await MediaLibrary.saveToLibraryAsync(file.uri);
    api('/events', { body: [{ name: 'download', props: { job_id: job.id } }] }).catch(() => {});
  };

  const share = async () => {
    if (!job?.output) return;
    const file = await FileSystem.File.downloadFileAsync(job.output.video_url, FileSystem.Paths.cache, { idempotent: true });
    api('/events', { body: [{ name: 'share_tap', props: { job_id: job.id } }] }).catch(() => {});
    await Sharing.shareAsync(file.uri, { mimeType: 'video/mp4', dialogTitle: t('job.share') });
  };

  const shareLink = async () => {
    try {
      await shareTemplateLink({ job_id: job!.id }, t('share.message'));
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    }
  };

  const rate = async (n: number) => {
    setRating(n);
    // feedback improves technical quality only; it is optional and never shared with other users
    api(`/generations/${id}/feedback`, { body: { rating: n } }).catch(() => {});
  };

  const report = () => {
    const send = async (reason: string) => {
      await api(`/generations/${id}/report`, { body: { reason } });
      Alert.alert(t('job.reported'));
    };
    const labels = REPORT_REASONS.map((r) => t(`report.${r}`));
    if (Platform.OS === 'ios') {
      ActionSheetIOS.showActionSheetWithOptions({ title: t('report.title'), options: [...labels, '✕'], cancelButtonIndex: labels.length },
        (i) => { if (i < labels.length) send(REPORT_REASONS[i]); });
    } else {
      Alert.alert(t('report.title'), undefined, REPORT_REASONS.slice(0, 3).map((r, i) => ({ text: labels[i], onPress: () => send(r) }))
        .concat([{ text: t('report.other'), onPress: () => send('other') }]));
    }
  };

  const retry = async () => {
    try {
      const j = await api<Generation>(`/generations/${id}/retry`, { method: 'POST' });
      router.replace({ pathname: '/job/[id]', params: { id: j.id } });
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    }
  };

  if (!job) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const done = job.status === 'completed' && job.output;
  const eta = formatEta(job.est_seconds_remaining);
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      {done ? (
        <>
          <View style={{ aspectRatio: 9 / 16, borderRadius: radius.lg, overflow: 'hidden', backgroundColor: '#000' }}>
            <VideoView player={player} style={{ flex: 1 }} fullscreenOptions={{ enable: true }} allowsPictureInPicture={false} />
          </View>
          <Body muted style={{ textAlign: 'center' }}>{t('job.aiLabel')}</Body>
          <Button title={t('job.share')} onPress={share} />
          <Button title={t('share.link')} variant="secondary" onPress={shareLink} />
          <Button title={t('job.download')} variant="secondary" onPress={download} />
          <Body>{t('job.rate')}</Body>
          <View style={{ flexDirection: 'row', gap: 6 }}>
            {[1, 2, 3, 4, 5].map((n) => (
              <Chip key={n} label={'★'.repeat(n)} active={rating === n} onPress={() => rate(n)} />
            ))}
          </View>
          <Button title={t('job.report')} variant="secondary" onPress={report} />
        </>
      ) : (
        <View style={{ gap: spacing.md, paddingTop: spacing.xl }}>
          <Title accessibilityLiveRegion="polite">{t(stageKey(job.status))}</Title>
          {!TERMINAL.has(job.status) ? <ProgressBar value={displayProgress(job)} /> : null}
          {job.status === 'queued' && job.queue_position ? <Body>{t('job.position', { n: job.queue_position })}</Body> : null}
          {eta && !TERMINAL.has(job.status) ? <Body muted>{t('job.eta', { eta })}</Body> : null}
          {!TERMINAL.has(job.status) ? <Body muted>{t('job.notifyHint')}</Body> : null}
          {job.status === 'failed' ? (
            <>
              <Body>{job.error_message ?? ''}</Body>
              {job.refunded ? <Body style={{ color: c.success }}>{t('job.refunded')}</Body> : null}
              <Button title={t('job.retry')} onPress={retry} />
            </>
          ) : null}
          {['queued', 'preprocessing', 'generating'].includes(job.status) ? (
            <Button title={t('job.cancel')} variant="secondary"
              onPress={() => api<Generation>(`/generations/${id}/cancel`, { method: 'POST' }).then(setJob)} />
          ) : null}
        </View>
      )}
    </ScrollView>
  );
}
