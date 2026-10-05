import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FlatList, RefreshControl, ScrollView, View } from 'react-native';

import { TemplateCard } from '@/components/TemplateCard';
import { Body, Button, Chip } from '@/components/ui';
import { api, type Profile, type Template } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import i18n from '@/lib/i18n';
import { spacing, useColors } from '@/lib/theme';

const CATEGORIES = ['trending', 'new', 'funny', 'cinematic', 'dance', 'fashion', 'travel', 'fantasy'];

export default function Discover() {
  const { t } = useTranslation();
  const c = useColors();
  const { me, refreshMe } = useAuth();
  const [cat, setCat] = useState('trending');
  const [items, setItems] = useState<Template[]>([]);
  const [hasProfile, setHasProfile] = useState(true);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const q = cat === 'trending' ? '' : `&category=${cat}`;
      const [tpls, profiles] = await Promise.all([
        api<Template[]>(`/templates?locale=${i18n.language}${q}`),
        api<Profile[]>('/identity-profiles'),
      ]);
      setItems(tpls);
      setHasProfile(profiles.some((p) => p.status === 'ready'));
      refreshMe();
    } finally {
      setLoading(false);
    }
  }, [cat, refreshMe]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  return (
    <View style={{ flex: 1, backgroundColor: c.bg }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }}
        contentContainerStyle={{ gap: spacing.sm, padding: spacing.md }}>
        {CATEGORIES.map((k) => <Chip key={k} label={t(`categories.${k}`)} active={k === cat} onPress={() => setCat(k)} />)}
      </ScrollView>
      {!hasProfile ? (
        <View style={{ paddingHorizontal: spacing.lg, paddingBottom: spacing.md }}>
          <Button title={t('discover.createProfile')} onPress={() => router.push('/identity/new')} />
        </View>
      ) : null}
      <FlatList
        data={items}
        numColumns={2}
        keyExtractor={(x) => x.id}
        renderItem={({ item }) => <TemplateCard tpl={item} />}
        contentContainerStyle={{ padding: spacing.sm }}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
        ListHeaderComponent={me ? <Body muted style={{ margin: spacing.xs }}>{t('discover.credits', { count: me.credits })}</Body> : null}
        ListEmptyComponent={!loading ? <Body muted style={{ textAlign: 'center', marginTop: spacing.xxl }}>{t('discover.empty')}</Body> : null}
      />
    </View>
  );
}
