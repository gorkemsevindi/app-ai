import { useLocalSearchParams } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, ScrollView, TextInput, View } from 'react-native';

import { Body, Button, Card, Chip, Title } from '@/components/ui';
import { api, ApiError, type StudioEdit, type StudioEstimate, type StudioProject } from '@/lib/api';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

function newKey() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export default function StudioProjectScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const [p, setP] = useState<StudioProject | null>(null);
  const [est, setEst] = useState<StudioEstimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [proposal, setProposal] = useState<StudioEdit | null>(null);
  const idem = useRef(newKey());
  const player = useVideoPlayer(p?.output?.video_url ?? null, (pl) => { pl.loop = true; });

  const load = useCallback(async () => {
    const proj = await api<StudioProject>(`/studio/projects/${id}`);
    setP(proj);
    if (proj.current_version) {
      setEst(await api<StudioEstimate>(`/studio/projects/${id}/estimate`, { body: { version_id: proj.current_version.id } }));
    }
    return proj;
  }, [id]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let alive = true;
    const tick = async () => {
      try {
        const proj = await load();
        if (alive && proj.status === 'rendering') timer = setTimeout(tick, 4000);
      } catch { if (alive) timer = setTimeout(tick, 8000); }
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [load]);

  const render = () => {
    if (!est) return;
    const go = async () => {
      setBusy(true);
      try {
        await api(`/studio/projects/${id}/render`, {
          body: { version_id: est.version_id, confirmed_credits: est.credits }, idempotencyKey: idem.current,
        });
        idem.current = newKey();
        await load();
      } catch (e) {
        if (e instanceof ApiError && e.code === 'confirmation_required') await load();  // price changed: re-confirm
        Alert.alert(errorMessage(t, e));
      } finally {
        setBusy(false);
      }
    };
    Alert.alert(t('studio.confirm', { credits: est.credits }), t('studio.confirmDetail', { n: est.new_shots, r: est.reused_shots }), [
      { text: t('job.cancel'), style: 'cancel' }, { text: 'OK', onPress: go },
    ]);
  };

  // Chat and timeline buttons send the same typed operations; chat edits are previewed (diff + cost) first.
  const propose = async (body: Record<string, unknown>) => {
    try {
      const e = await api<StudioEdit>(`/studio/projects/${id}/edits`, { body });
      if (e.status === 'applied') { setProposal(null); await load(); } else setProposal(e);
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    }
  };
  const decide = async (apply: boolean) => {
    if (!proposal) return;
    try {
      await api(`/studio/projects/${id}/edits/${proposal.id}/${apply ? 'apply' : 'reject'}`, { method: 'POST' });
      setProposal(null);
      setInstruction('');
      await load();
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    }
  };
  const timeline = (op: Record<string, unknown>) => propose({ source: 'timeline', auto_apply: true, ops: [op] });
  const variations = async () => {
    try {
      const r = await api<{ items: { version: number }[] }>(`/studio/projects/${id}/variations`, { body: { count: 2 } });
      Alert.alert(t('studio.variationsReady', { versions: r.items.map((x) => `v${x.version}`).join(', ') }));
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    }
  };

  const undo = async () => {
    try { await api(`/studio/projects/${id}/undo`, { method: 'POST' }); await load(); }
    catch (e) { Alert.alert(errorMessage(t, e)); }
  };

  if (!p) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const sb = p.current_version?.storyboard;
  const statusOf = (k: string) => p.shots.find((s) => s.key === k)?.status;
  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <Title>{sb?.title ?? p.title}</Title>
      <Body muted>{`${t(`studio.status.${p.status}`)} · ${p.current_version?.director?.label ?? ''}`}</Body>
      {p.output ? (
        <View style={{ aspectRatio: p.aspect_ratio === '16:9' ? 16 / 9 : p.aspect_ratio === '1:1' ? 1 : 9 / 16,
                       borderRadius: radius.lg, overflow: 'hidden', backgroundColor: '#000' }}>
          <VideoView player={player} style={{ flex: 1 }} fullscreenOptions={{ enable: true }} />
        </View>
      ) : null}
      {sb?.scenes.flatMap((sc) => sc.shots).map((s, i) => (
        <Card key={s.key} style={{ gap: 4 }}>
          <Body>{`${i + 1}. ${s.duration_s}s · ${s.camera ?? ''}${statusOf(s.key) ? ` · ${t(`studio.shot.${statusOf(s.key)}`)}` : ''}`}</Body>
          <Body muted>{s.prompt}</Body>
          {s.caption ? <Body>{`“${s.caption}”`}</Body> : null}
          {(s.dialogue ?? []).map((d, j) => <Body key={j}>{`${d.character ?? ''}: ${d.text}`}</Body>)}
          {p.status !== 'rendering' ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
              {i > 0 ? <Chip label="↑" onPress={() => timeline({ op: 'move_shot', shot: s.key, before: sb.scenes.flatMap((x) => x.shots)[i - 1].key })} /> : null}
              <Chip label={t('studio.edit.duplicate')} onPress={() => timeline({ op: 'duplicate_shot', shot: s.key })} />
              <Chip label={t('studio.edit.remove')} onPress={() => timeline({ op: 'remove_shot', shot: s.key })} />
              {statusOf(s.key) === 'ready' && !s.derive ? (
                <Chip label={t('studio.edit.extend')} onPress={() => propose({ source: 'timeline', ops: [{ op: 'extend_shot', shot: s.key, direction: 'end', seconds: 4 }] })} />
              ) : null}
            </View>
          ) : null}
        </Card>
      ))}
      <Card style={{ gap: spacing.sm }}>
        <Body>{t('studio.edit.title')}</Body>
        <TextInput accessibilityLabel={t('studio.edit.title')} placeholder={t('studio.edit.placeholder')} value={instruction}
          onChangeText={setInstruction} maxLength={1000} placeholderTextColor={c.textMuted}
          style={{ borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, color: c.text }} />
        <Button title={t('studio.edit.preview')} variant="secondary" disabled={instruction.trim().length < 2}
          onPress={() => propose({ instruction, source: 'chat' })} />
        {proposal ? (
          proposal.status === 'proposed' && proposal.cost && proposal.diff ? (
            <View style={{ gap: 4 }}>
              <Body>{t('studio.edit.summary', { added: proposal.diff.added.length, removed: proposal.diff.removed.length,
                changed: proposal.diff.changed.length, credits: proposal.cost.render_credits_after })}</Body>
              {proposal.notes.map((n, i) => <Body key={i} muted>{n}</Body>)}
              <View style={{ flexDirection: 'row', gap: spacing.sm }}>
                <Button title={t('studio.edit.apply')} onPress={() => decide(true)} />
                <Button title={t('studio.edit.reject')} variant="secondary" onPress={() => decide(false)} />
              </View>
            </View>
          ) : <Body muted>{proposal.clarification ?? ''}</Body>
        ) : null}
        <Button title={t('studio.edit.undo')} variant="secondary" onPress={undo} />
        <Button title={t('studio.variations')} variant="secondary" onPress={variations} />
      </Card>
      {[...(est?.limitations ?? [])].map((l, i) => <Body key={i} muted>{`⚠︎ ${l}`}</Body>)}
      {est ? (
        <Body>{t('studio.estimate', { credits: est.credits, n: est.new_shots, r: est.reused_shots, balance: est.balance })}</Body>
      ) : null}
      {p.status !== 'rendering' && est ? (
        <Button title={t('studio.render', { credits: est.credits })} loading={busy}
          disabled={est.blocked.length > 0 || est.missing_capabilities.length > 0} onPress={render} />
      ) : null}
    </ScrollView>
  );
}
