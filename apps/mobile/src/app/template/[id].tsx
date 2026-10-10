import { router, useLocalSearchParams } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput, View } from 'react-native';

import { Body, Button, Chip, Title } from '@/components/ui';
import {
  api, ApiError, newIdempotencyKey, type Estimate, type Generation, type Profile, type Template,
} from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { shareTemplateLink } from '@/lib/referral';
import { formatEta } from '@/lib/progress';
import { radius, spacing, useColors } from '@/lib/theme';

export default function TemplateDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [tpl, setTpl] = useState<Template | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  // Remix templates: slot_id -> profile id (absent = keep the original person in that role).
  const [cast, setCast] = useState<Record<string, string>>({});
  const [quote, setQuote] = useState<Estimate | null>(null);
  const [lipSync, setLipSync] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  // One key per screen visit: double taps / network retries can't create (or charge) twice.
  const idemKey = useRef(newIdempotencyKey());
  const player = useVideoPlayer(tpl?.preview_url?.startsWith('http') ? tpl.preview_url : null, (p) => {
    p.loop = true;
    p.muted = true;
    p.play();
  });

  useEffect(() => {
    api<Template>(`/templates/${id}`).then(setTpl);
    api<Profile[]>('/identity-profiles').then((ps) => {
      const ready = ps.filter((p) => p.status === 'ready');
      setProfiles(ready);
      setProfile(ready[0] ?? null);
    });
  }, [id]);

  const isRemix = tpl?.mode === 'remix' && (tpl.person_slots?.length ?? 0) > 0;

  // Default casting: the user's first profile in the first slot.
  useEffect(() => {
    if (isRemix && profile && tpl?.person_slots && Object.keys(cast).length === 0) {
      setCast({ [tpl.person_slots[0].slot_id]: profile.id });
    }
  }, [isRemix, profile, tpl, cast]);

  // Server-side quote whenever the casting changes (spec: show the exact price before generating).
  useEffect(() => {
    if (!isRemix) return;
    api<Estimate>('/generations/estimate', { body: { template_id: id, slots: Object.keys(cast), lip_sync: lipSync } })
      .then(setQuote).catch(() => setQuote(null));
  }, [isRemix, cast, id, lipSync]);

  const cycleSlot = (slotId: string) => {
    const order = [undefined, ...profiles.map((p) => p.id)];
    const next = order[(order.indexOf(cast[slotId]) + 1) % order.length];
    setCast((cur) => {
      const copy = { ...cur };
      if (next) copy[slotId] = next; else delete copy[slotId];
      return copy;
    });
  };

  const remix = async () => {
    if (!profile) return router.push('/identity/new');
    const assignments = Object.entries(cast).map(([slot_id, profile_id]) => ({ slot_id, profile_id }));
    const send = async () => {
      setBusy(true);
      try {
        const job = await api<Generation>('/generations/remix', {
          body: {
            template_id: id, assignments, confirmed_credits: quote?.credits ?? null,
            ...(lipSync ? { audio: { lip_sync: { enabled: true, mode: 'auto' } } } : {}),
          },
          idempotencyKey: idemKey.current,
        });
        router.replace({ pathname: '/job/[id]', params: { id: job.id } });
      } catch (e) {
        if (e instanceof ApiError && (e.code === 'insufficient_credits' || e.code === 'pro_required')) {
          router.push('/paywall');
        } else if (e instanceof ApiError && e.code === 'confirmation_required') {
          // lip-sync price is final only at creation: refresh the quote so the user can confirm it
          api<Estimate>('/generations/estimate', { body: { template_id: id, slots: Object.keys(cast), lip_sync: lipSync } })
            .then(setQuote).catch(() => setQuote(null));
          Alert.alert(errorMessage(t, e));
        } else {
          Alert.alert(errorMessage(t, e));
        }
      } finally {
        setBusy(false);
      }
    };
    if (quote?.confirm_required) {
      Alert.alert(t('remix.confirm', { credits: quote.credits }), undefined, [
        { text: t('job.cancel'), style: 'cancel' }, { text: 'OK', onPress: send },
      ]);
    } else {
      await send();
    }
  };

  const shareLink = async () => {
    try {
      await shareTemplateLink({ template_id: id }, t('share.message'));
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    }
  };

  const report = () => {
    const reasons = ['impersonation', 'copyright', 'sexual', 'minor_safety', 'other'];
    Alert.alert(t('report.title'), undefined, [
      ...reasons.map((r) => ({
        text: t(`report.${r}`),
        onPress: () => {
          api(`/templates/${id}/report`, { body: { reason: r } })
            .then(() => Alert.alert(t('job.reported'))).catch((e) => Alert.alert(errorMessage(t, e)));
        },
      })),
      { text: t('job.cancel'), style: 'cancel' as const },
    ]);
  };

  const generate = async () => {
    if (!profile) return router.push('/identity/new');
    setBusy(true);
    try {
      const job = await api<Generation>('/generations', {
        body: { template_id: id, profile_id: profile.id, text: text || null },
        idempotencyKey: idemKey.current,
      });
      router.replace({ pathname: '/job/[id]', params: { id: job.id } });
    } catch (e) {
      if (e instanceof ApiError && (e.code === 'insufficient_credits' || e.code === 'pro_required')) {
        router.push('/paywall');
      } else {
        Alert.alert(errorMessage(t, e));
      }
    } finally {
      setBusy(false);
    }
  };

  if (!tpl) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const eta = formatEta(tpl.est_seconds);
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <View style={{ aspectRatio: 9 / 16, maxHeight: 460, alignSelf: 'center', width: '70%', borderRadius: radius.lg,
                     overflow: 'hidden', backgroundColor: c.surfaceAlt }}>
        {tpl.preview_url?.startsWith('http') ? <VideoView player={player} style={{ flex: 1 }} nativeControls={false} /> : null}
      </View>
      <Title>{tpl.title}</Title>
      <Body muted>{tpl.description}</Body>
      <Body>{`${t('template.cost', { count: quote?.credits ?? tpl.est_credits ?? tpl.credit_cost })}${eta ? ` · ${t('template.eta', { eta })}` : ''}`}</Body>
      {isRemix ? (
        <View style={{ gap: spacing.sm }}>
          <Body>{t('remix.slots')}</Body>
          {tpl.person_slots!.map((s) => {
            const chosen = profiles.find((p) => p.id === cast[s.slot_id]);
            return (
              <Chip key={s.slot_id} active={!!chosen} onPress={() => cycleSlot(s.slot_id)}
                label={`${s.label}${s.required ? ` (${t('remix.required')})` : ''}: ${chosen ? chosen.name : t('remix.keep')}`} />
            );
          })}
          {tpl.lip_sync_available ? (
            <Chip active={lipSync} onPress={() => setLipSync((v) => !v)}
              label={`${t('remix.lipSync')}: ${lipSync ? t('remix.on') : t('remix.off')}`} />
          ) : null}
        </View>
      ) : null}
      {tpl.accepts_text ? (
        <TextInput accessibilityLabel={t('template.addText')} placeholder={t('template.addText')} maxLength={200}
          placeholderTextColor={c.textMuted} value={text} onChangeText={setText}
          style={{ borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.md, color: c.text, minHeight: 50 }} />
      ) : null}
      {isRemix ? (
        <Button title={profile ? t('remix.generate', { credits: quote?.credits ?? '…' }) : t('discover.createProfile')}
          loading={busy} disabled={profile !== null && Object.keys(cast).length === 0} onPress={remix} />
      ) : (
        <Button title={profile ? t('template.generate') : t('discover.createProfile')} loading={busy} onPress={generate} />
      )}
      <Button title={t('share.link')} variant="secondary" onPress={shareLink} />
      <Button title={t('report.template')} variant="secondary" onPress={report} />
    </ScrollView>
  );
}
