import { router, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput, View } from 'react-native';

import { Body, Button, Card, Chip, ProgressBar, Title } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

type Ep = { number: number; season: number; title: string; status: string; target_duration_s: number };
type Prod = { id: string; title: string; kind: string; content_rating: string; profile: string; episodes: Ep[];
  budget: { cap_credits: number | null; committed_credits: number; remaining_credits: number | null } };
type Status = { status: string; progress: number; shots_total: number; shots_done: number; errors: { shot: string; error: string }[] };
type Finding = { severity: string; message: string };
type Estimate = { id: string; credits_low: number; credits_high: number; currency: string | null; amount_high_minor: number | null;
  feasible: boolean | null; alternatives: { type: string; detail?: string }[] };

const key = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

// Production screen: episode list, screenplay → plan, free animatic, budget estimate + approval (hard cap),
// 30–60 s pilot, full render, progress, cancel (refund) and continuity findings.
export default function ProductionScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [p, setP] = useState<Prod | null>(null);
  const [ep, setEp] = useState(1);
  const [st, setSt] = useState<Status | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [script, setScript] = useState('');
  const [usd, setUsd] = useState('');
  const [est, setEst] = useState<Estimate | null>(null);
  const idem = useRef(key());

  const load = useCallback(async () => {
    const x = await api<Prod>(`/productions/${id}`);
    setP(x);
    const e = await api<{ status: Status; continuity: Finding[] }>(`/productions/${id}/episodes/${ep}`);
    setSt(e.status); setFindings(e.continuity);
    return e.status;
  }, [id, ep]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const tick = async () => {
      try {
        const s = await load();
        if (alive && ['queued', 'generating', 'qc', 'assembling'].includes(s.status)) timer = setTimeout(tick, 4000);
      } catch { if (alive) timer = setTimeout(tick, 8000); }
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [load]);

  const call = async (path: string, body: Record<string, unknown> = {}, paid = false) => {
    try {
      await api(`/productions/${id}/${path}`, { body, ...(paid ? { idempotencyKey: idem.current } : {}) });
      if (paid) idem.current = key();
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.code === 'confirmation_required') {
        const credits = (e.extra as { credits?: number }).credits;
        Alert.alert(t('production.confirm', { credits }), '', [{ text: t('job.cancel'), style: 'cancel' },
          { text: 'OK', onPress: () => call(path, { ...body, confirmed_credits: credits }, paid) }]);
      } else Alert.alert(errorMessage(t, e));
    }
  };
  const estimate = async () => {
    try {
      const body = usd ? { cap_minor: Math.round(Number(usd) * 100), currency: 'USD' } : {};
      setEst(await api<Estimate>(`/productions/${id}/estimate`, { body }));
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };

  if (!p) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const input = { borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text };
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{p.title}</Title>
      <Body muted>{`${t(`production.ratings.${p.content_rating}`)} · ${t(`production.profiles.${p.profile}`)}`}</Body>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
        {p.episodes.map((e) => <Chip key={`${e.season}-${e.number}`} label={`S${e.season}E${e.number}`} active={ep === e.number}
          onPress={() => setEp(e.number)} />)}
      </View>
      {st ? (
        <Card style={{ gap: 4 }}>
          <Body>{t(`production.status.${st.status}`, { defaultValue: st.status })}</Body>
          {st.shots_total ? <ProgressBar value={st.progress} /> : null}
          {st.errors.map((x, i) => <Body key={i} muted>{`${x.shot}: ${x.error}`}</Body>)}
        </Card>
      ) : null}
      {findings.map((f, i) => <Body key={i} muted>{`${f.severity === 'error' ? '⛔' : '⚠︎'} ${f.message}`}</Body>)}
      <TextInput accessibilityLabel={t('production.script')} placeholder={t('production.scriptHint')} value={script}
        onChangeText={setScript} multiline style={[input, { minHeight: 140, textAlignVertical: 'top' }]} placeholderTextColor={c.textMuted} />
      <Button title={t('production.plan')} variant="secondary" disabled={script.length < 10}
        onPress={() => call(`episodes/${ep}/plan`, { script })} />
      <Button title={t('production.animatic')} variant="secondary" onPress={() => call(`episodes/${ep}/animatic`)} />
      <Button title={t('production.dialogue')} variant="secondary"
        onPress={() => router.push({ pathname: '/productions/dialogue', params: { id, ep: String(ep) } })} />
      <Card style={{ gap: spacing.sm }}>
        <Body>{t('production.budget')}</Body>
        <TextInput accessibilityLabel={t('production.budgetUsd')} placeholder={t('production.budgetUsd')} keyboardType="decimal-pad"
          value={usd} onChangeText={setUsd} style={input} placeholderTextColor={c.textMuted} />
        <Button title={t('production.estimate')} variant="secondary" onPress={estimate} />
        {est ? (
          <View style={{ gap: 4 }}>
            <Body>{t('production.estimateRange', { lo: est.credits_low, hi: est.credits_high })}</Body>
            {est.feasible === false ? <Body>{t('production.infeasible')}</Body> : null}
            {est.alternatives.map((a, i) => <Body key={i} muted>{`• ${t(`production.alt.${a.type}`, { defaultValue: a.type })}${a.detail ? `: ${a.detail}` : ''}`}</Body>)}
            <Button title={t('production.approve')} onPress={() => call('approve', { estimate_id: est.id }, true)} />
          </View>
        ) : null}
        {p.budget.cap_credits != null ? <Body muted>{t('production.capInfo', { cap: p.budget.cap_credits, used: p.budget.committed_credits })}</Body> : null}
      </Card>
      <Button title={t('production.pilot')} onPress={() => call(`episodes/${ep}/pilot`, {}, true)} />
      <Button title={t('production.render')} onPress={() => call(`episodes/${ep}/render`, {}, true)} />
      {st && ['queued', 'generating', 'qc'].includes(st.status) ? (
        <Button title={t('production.cancel')} variant="secondary" onPress={() => call(`episodes/${ep}/cancel`)} />
      ) : null}
    </ScrollView>
  );
}
