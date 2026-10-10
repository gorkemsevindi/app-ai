import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useLocalSearchParams } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Linking, Modal, Pressable, ScrollView, Text, TextInput, View, useWindowDimensions } from 'react-native';

import type { Command } from '@shared/engine.ts';
import { fitInside, layerState } from '@shared/preview.ts';
import type { Clip, Project, TrackKind } from '@shared/schema.ts';
import { durationMs } from '@shared/schema.ts';

import { Body, Button, Card } from '@/components/ui';
import { api, newIdempotencyKey } from '@/lib/api';
import { uid, uploadPicked, useMobileEditor } from '@/lib/editor';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

const PX_PER_MS = 0.04;
type Exp = { job_id: string; format: string; status: string; url: string | null };

// V8 mobile editor: the same canonical project, engine commands, undo/redo, autosave and offline drafts as the
// web editor (packages/shared). Gestures are buttons on the selected clip so every edit is one exact command.
export default function EditorScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t } = useTranslation();
  const c = useColors();
  const { ctl, exec, error, notice, setNotice } = useMobileEditor(id);
  const [time, setTime] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [exportsList, setExports] = useState<Exp[]>([]);
  const { width } = useWindowDimensions();

  useEffect(() => {
    api<{ items: { uri: string; url: string | null }[] }>('/editor/assets')
      .then((r) => setUrls(Object.fromEntries(r.items.filter((a) => a.url).map((a) => [a.uri, a.url as string])))).catch(() => {});
  }, [busy]);
  useEffect(() => {
    if (notice) Alert.alert(t('editor.invalid', { code: notice }), undefined, [{ text: t('editor.dismiss'), onPress: () => setNotice(null) }]);
  }, [notice, t, setNotice]);

  if (error) return <View style={{ flex: 1, padding: spacing.lg, backgroundColor: c.bg }}><Body>{error}</Body></View>;
  if (!ctl) return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  const p = ctl.session.project;
  const photo = p.type === 'photo';
  const total = durationMs(p);
  const sel = selected ? p.clips[selected] ?? null : null;

  const addText = () => {
    const start = photo ? 0 : Math.round(time), dur = photo ? 0 : 3000;
    const tid = uid('trk'), cid = uid('txt');
    if (exec({ type: 'add_track', track_id: tid, kind: 'text', name: t('editor.addText') },
             { type: 'add_clip', clip: { id: cid, track_id: tid, start_ms: start, duration_ms: dur, name: t('editor.addText'),
               text: { content: t('editor.newText'), size: Math.round(p.canvas.height / 20), color: '#ffffff', align: 'center', weight: 'bold', font: 'Inter' } } })) setSelected(cid);
  };
  const addShape = () => {
    const tid = uid('trk'), cid = uid('shp'), w = Math.round(p.canvas.width / 3);
    if (exec({ type: 'add_track', track_id: tid, kind: 'overlay', name: t('editor.addShape') },
             { type: 'add_clip', clip: { id: cid, track_id: tid, start_ms: photo ? 0 : Math.round(time), duration_ms: photo ? 0 : 3000,
               shape: { type: 'rect', width: w, height: Math.round(w / 2), fill: '#e8453c' } } })) setSelected(cid);
  };
  const addMedia = async () => {
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['videos', 'images'], quality: 1 });
    if (res.canceled) return;
    const a = res.assets[0];
    const kind = a.type === 'video' ? 'video' : 'image';
    setBusy(true);
    try {
      const out = await uploadPicked({ uri: a.uri, mimeType: a.mimeType ?? (kind === 'video' ? 'video/mp4' : 'image/jpeg'),
        name: a.fileName ?? `${kind}.${kind === 'video' ? 'mp4' : 'jpg'}`, size: a.fileSize ?? 0,
        duration_ms: a.duration ?? undefined, width: a.width, height: a.height }, kind);
      const track: TrackKind = photo || (kind === 'image' && p.tracks.some((x) => x.kind === 'video')) ? 'overlay' : 'video';
      const existing = p.tracks.find((x) => x.kind === track && !photo);
      const at = photo ? 0 : existing ? Math.max(0, ...existing.clip_ids.map((cid) => p.clips[cid].start_ms + p.clips[cid].duration_ms)) : 0;
      const tid = existing?.id ?? uid('trk');
      const cid = uid('clip');
      exec({ type: 'add_asset', asset: { id: out.id, kind, uri: out.uri, name: out.name.slice(0, 120), ...(out.meta ?? {}) } },
           ...(existing ? [] : [{ type: 'add_track', track_id: tid, kind: track } as Command]),
           { type: 'add_clip', clip: { id: cid, track_id: tid, asset_id: out.id, start_ms: at, name: out.name.slice(0, 80),
             duration_ms: photo ? 0 : kind === 'image' ? 5000 : Math.max(100, out.meta?.duration_ms ?? 5000) } });
      setSelected(cid);
    } catch (e) { Alert.alert(errorMessage(t, e)); }
    setBusy(false);
  };

  const doExport = async () => {
    try {
      await ctl.flush();
      const format = photo ? 'png' : 'mp4';
      const q = await api<{ credits: number }>(`/editor/projects/${id}/render/quote`, { body: { format, quality: '720p' } });
      Alert.alert(t('editor.export'), t('editor.quote', { credits: q.credits }), [
        { text: t('editor.cancel'), style: 'cancel' },
        { text: t('editor.start'), onPress: async () => {
          try {
            await api(`/editor/projects/${id}/render`, { body: { format, quality: '720p', confirmed_credits: q.credits }, idempotencyKey: newIdempotencyKey() });
            pollExports();
          } catch (e) { Alert.alert(errorMessage(t, e)); }
        } },
      ]);
    } catch (e) { Alert.alert(errorMessage(t, e)); }
  };
  const pollExports = async () => {
    for (let i = 0; i < 60; i++) {
      const r = await api<{ items: Exp[] }>(`/editor/projects/${id}/exports`).catch(() => null);
      if (r) setExports(r.items);
      if (r && !r.items.some((x) => x.status === 'queued' || x.status === 'running')) return;
      await new Promise((ok) => setTimeout(ok, 3000));
    }
  };

  const stateLabel = ctl.state === 'conflict' ? t('editor.conflict', { count: ctl.dropped.length }) : t(`editor.${ctl.state}`);
  const stageW = Math.min(width - spacing.lg * 2, 420);
  const s = Math.min(stageW / p.canvas.width, 360 / p.canvas.height);

  return (
    <ScrollView style={{ backgroundColor: c.bg }} contentContainerStyle={{ padding: spacing.lg, gap: spacing.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
        <Text style={{ color: c.text, fontWeight: '700', fontSize: 18, flex: 1 }} numberOfLines={1}>{p.title}</Text>
        <Text accessibilityLiveRegion="polite" style={{ color: ctl.state === 'saved' ? c.success : ctl.state === 'conflict' || ctl.state === 'error' ? c.danger : c.textMuted, fontSize: 12 }}>
          {stateLabel}{ctl.state === 'saved' ? ` · r${ctl.session.revision}` : ''}
        </Text>
      </View>
      {ctl.state === 'conflict' ? <Button variant="secondary" title={t('editor.dismiss')} onPress={() => ctl.dismissConflict()} /> : null}

      <View style={{ alignSelf: 'center', width: p.canvas.width * s, height: p.canvas.height * s, backgroundColor: p.canvas.background, overflow: 'hidden' }}
            accessibilityLabel={t('editor.title')}>
        <StageLayers p={p} t={time} s={s} urls={urls} selected={selected} onSelect={setSelected} />
      </View>
      <Body muted style={{ fontSize: 12 }}>{t('editor.previewNote')}</Body>

      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
        <Small title={t('editor.addText')} onPress={addText} />
        <Small title={t('editor.addShape')} onPress={addShape} />
        <Small title={busy ? t('editor.uploading') : t('editor.addMedia')} onPress={addMedia} disabled={busy} />
        <Small title={t('editor.undo')} onPress={() => ctl.undo()} disabled={!ctl.session.canUndo()} />
        <Small title={t('editor.redo')} onPress={() => ctl.redo()} disabled={!ctl.session.canRedo()} />
      </View>

      {!photo ? (
        <ScrollView horizontal contentContainerStyle={{ paddingVertical: spacing.sm }}>
          <View style={{ width: Math.max(total + 5000, 15000) * PX_PER_MS }}>
            <Pressable accessibilityRole="adjustable" accessibilityLabel="timeline" onPress={(e) => setTime(Math.round(e.nativeEvent.locationX / PX_PER_MS))}
                       style={{ height: 22, backgroundColor: c.surfaceAlt, borderRadius: 4 }}>
              <View style={{ position: 'absolute', left: time * PX_PER_MS, top: 0, bottom: 0, width: 2, backgroundColor: c.accent }} />
            </Pressable>
            {[...p.tracks].reverse().map((tr) => (
              <View key={tr.id} style={{ height: 38, marginTop: 4 }}>
                {tr.clip_ids.map((cid) => {
                  const cl = p.clips[cid];
                  return (
                    <Pressable key={cid} accessibilityRole="button" accessibilityLabel={cl.text?.content ?? cl.name}
                               accessibilityState={{ selected: selected === cid }} onPress={() => setSelected(cid)}
                               style={{ position: 'absolute', left: cl.start_ms * PX_PER_MS, width: Math.max(8, cl.duration_ms * PX_PER_MS), top: 0, bottom: 0,
                                        backgroundColor: tr.kind === 'text' ? '#7048e8' : tr.kind === 'audio' ? '#0c8599' : tr.kind === 'overlay' ? '#e8590c' : '#3b5bdb',
                                        borderRadius: 6, padding: 4, borderWidth: selected === cid ? 2 : 0, borderColor: '#f2b33d' }}>
                      <Text numberOfLines={1} style={{ color: '#fff', fontSize: 11 }}>{cl.text?.content ?? cl.name}</Text>
                    </Pressable>
                  );
                })}
              </View>
            ))}
          </View>
        </ScrollView>
      ) : (
        <Card style={{ gap: spacing.xs }}>
          <Text style={{ color: c.text, fontWeight: '700' }}>{t('editor.layers')}</Text>
          {[...p.tracks].reverse().flatMap((tr) => tr.clip_ids.map((cid) => (
            <Pressable key={cid} onPress={() => setSelected(cid)} accessibilityRole="button" style={{ padding: 6, borderRadius: 6, backgroundColor: selected === cid ? c.surfaceAlt : undefined }}>
              <Text style={{ color: c.text }}>{p.clips[cid].text?.content ?? p.clips[cid].name}</Text>
            </Pressable>
          )))}
        </Card>
      )}

      {sel ? <ClipActions clip={sel} photo={photo} time={time} exec={exec} onEdit={() => setEditing(sel.id)} t={t} /> : null}
      <Button title={t('editor.export')} onPress={doExport} />
      {exportsList.length ? (
        <Card style={{ gap: spacing.xs }}>
          <Text style={{ color: c.text, fontWeight: '700' }}>{t('editor.exports')}</Text>
          {exportsList.map((x) => (
            <View key={x.job_id} style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <Text style={{ color: c.text }}>{x.format.toUpperCase()} · {x.status}</Text>
              {x.url ? <Text accessibilityRole="link" style={{ color: c.accent }} onPress={() => Linking.openURL(x.url as string)}>{t('editor.open')}</Text> : null}
            </View>
          ))}
        </Card>
      ) : null}
      <TextEditor clip={editing ? p.clips[editing] ?? null : null} onClose={() => setEditing(null)} exec={exec} t={t} />
    </ScrollView>
  );
}

function Small({ title, onPress, disabled }: { title: string; onPress: () => void; disabled?: boolean }) {
  const c = useColors();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={title} accessibilityState={{ disabled: !!disabled }} onPress={onPress} disabled={disabled}
               style={{ paddingHorizontal: 12, paddingVertical: 8, minHeight: 40, justifyContent: 'center', borderRadius: radius.sm, backgroundColor: c.surfaceAlt, opacity: disabled ? 0.5 : 1 }}>
      <Text style={{ color: c.text }}>{title}</Text>
    </Pressable>
  );
}

function ClipActions({ clip, photo, time, exec, onEdit, t }: { clip: Clip; photo: boolean; time: number; exec: (...c: Command[]) => boolean; onEdit: () => void; t: (k: string) => string }) {
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
      {clip.text ? <Small title={t('editor.editText')} onPress={onEdit} /> : null}
      {!photo ? (
        <>
          <Small title={t('editor.split')} onPress={() => exec({ type: 'split_clip', clip_id: clip.id, at_ms: Math.round(time), new_clip_id: uid('clip') })} />
          <Small title={t('editor.trimStart')} onPress={() => exec({ type: 'trim_clip', clip_id: clip.id, side: 'start', delta_ms: 500 })} />
          <Small title={t('editor.trimEnd')} onPress={() => exec({ type: 'trim_clip', clip_id: clip.id, side: 'end', delta_ms: -500 })} />
          <Small title={t('editor.extend')} onPress={() => exec({ type: 'trim_clip', clip_id: clip.id, side: 'end', delta_ms: 500 })} />
          <Small title={t('editor.moveLeft')} onPress={() => exec({ type: 'move_clip', clip_id: clip.id, start_ms: Math.max(0, clip.start_ms - 500) })} />
          <Small title={t('editor.moveRight')} onPress={() => exec({ type: 'move_clip', clip_id: clip.id, start_ms: clip.start_ms + 500 })} />
        </>
      ) : null}
      <Small title={t('editor.delete')} onPress={() => exec({ type: 'remove_clip', clip_id: clip.id })} />
    </View>
  );
}

function TextEditor({ clip, onClose, exec, t }: { clip: Clip | null; onClose: () => void; exec: (...c: Command[]) => boolean; t: (k: string) => string }) {
  const c = useColors();
  const [value, setValue] = useState('');
  useEffect(() => { setValue(clip?.text?.content ?? ''); }, [clip?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Modal visible={!!clip} transparent animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,.4)' }}>
        <Card style={{ gap: spacing.md, margin: spacing.lg }}>
          <TextInput value={value} onChangeText={setValue} multiline maxLength={2000} accessibilityLabel={t('editor.editText')}
                     style={{ color: c.text, borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.sm, minHeight: 80 }} />
          <Button title={t('editor.apply')} disabled={!value.trim()} onPress={() => {
            if (clip && value !== clip.text?.content) exec({ type: 'set_clip', clip_id: clip.id, text: { content: value } });
            onClose();
          }} />
          <Button variant="secondary" title={t('editor.cancel')} onPress={onClose} />
        </Card>
      </View>
    </Modal>
  );
}

/** Approximate preview using the shared placement rules (shared/preview.ts). */
function StageLayers({ p, t, s, urls, selected, onSelect }: { p: Project; t: number; s: number; urls: Record<string, string>; selected: string | null; onSelect: (id: string) => void }) {
  const layers = useMemo(() => p.tracks.flatMap((tr, z) => tr.kind === 'audio' || tr.hidden ? [] : tr.clip_ids.map((cid) => ({ cl: p.clips[cid], z }))), [p]);
  return (
    <>
      {layers.map(({ cl, z }) => {
        const st = layerState(p, cl, t);
        if (!st.visible) return null;
        const border = selected === cl.id ? { borderWidth: 1, borderColor: '#f2b33d', borderStyle: 'dashed' as const } : {};
        if (cl.text) {
          return (
            <Pressable key={cl.id} onPress={() => onSelect(cl.id)} style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, justifyContent: 'center', zIndex: z }} pointerEvents="box-none">
              <Text style={{ color: cl.text.color, fontSize: Math.max(4, cl.text.size * st.scale) * s, fontWeight: cl.text.weight === 'bold' ? '700' : '400',
                             textAlign: cl.text.align, opacity: st.opacity, transform: [{ translateX: st.x * s }, { translateY: st.y * s }],
                             paddingHorizontal: cl.text.align === 'center' ? 0 : 40 * s, backgroundColor: cl.text.background || undefined, ...border }}>
                {cl.text.content}
              </Text>
            </Pressable>
          );
        }
        const a = cl.asset_id ? p.assets[cl.asset_id] : null;
        const size = cl.shape ? { width: cl.shape.width, height: cl.shape.height } : fitInside(p.canvas, a?.width, a?.height);
        const style = { position: 'absolute' as const, zIndex: z, width: size.width * s, height: size.height * s, opacity: st.opacity,
          left: (p.canvas.width * s - size.width * s) / 2, top: (p.canvas.height * s - size.height * s) / 2,
          transform: [{ translateX: st.x * s }, { translateY: st.y * s }, { rotate: `${st.rotation}deg` }, { scale: st.scale }], ...border };
        if (cl.shape) {
          return <Pressable key={cl.id} onPress={() => onSelect(cl.id)} style={{ ...style, backgroundColor: cl.shape.fill, borderRadius: cl.shape.type === 'ellipse' ? 9999 : (cl.shape.radius ?? 0) * s }} />;
        }
        const src = a ? urls[a.uri] : undefined;
        if (a?.kind === 'video' && src) return <Pressable key={cl.id} onPress={() => onSelect(cl.id)} style={style}><VideoLayer src={src} at={st.source_ms} /></Pressable>;
        return (
          <Pressable key={cl.id} onPress={() => onSelect(cl.id)} style={style}>
            {src ? <Image source={{ uri: src }} style={{ flex: 1 }} contentFit="contain" /> : <View style={{ flex: 1, backgroundColor: '#333' }} />}
          </Pressable>
        );
      })}
    </>
  );
}

function VideoLayer({ src, at }: { src: string; at: number }) {
  const player = useVideoPlayer(src, (pl) => { pl.muted = true; });
  useEffect(() => { player.currentTime = at / 1000; }, [player, at]);
  return <VideoView player={player} style={{ flex: 1 }} contentFit="contain" nativeControls={false} />;
}
