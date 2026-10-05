import { Image } from 'expo-image';
import { Link, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FlatList, Pressable, RefreshControl, View } from 'react-native';

import { Body } from '@/components/ui';
import { api, type Generation } from '@/lib/api';
import { stageKey } from '@/lib/progress';
import { radius, spacing, useColors } from '@/lib/theme';

export default function Library() {
  const { t } = useTranslation();
  const c = useColors();
  const [items, setItems] = useState<Generation[]>([]);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try { setItems(await api<Generation[]>('/generations')); } finally { setLoading(false); }
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));
  return (
    <FlatList
      style={{ backgroundColor: c.bg }}
      data={items}
      numColumns={3}
      keyExtractor={(g) => g.id}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
      contentContainerStyle={{ padding: spacing.sm }}
      renderItem={({ item }) => (
        <Link href={{ pathname: '/job/[id]', params: { id: item.id } }} asChild>
          <Pressable accessibilityRole="button" accessibilityLabel={t(stageKey(item.status))} style={{ flex: 1 / 3, padding: spacing.xs }}>
            <View style={{ aspectRatio: 9 / 16, borderRadius: radius.sm, overflow: 'hidden', backgroundColor: c.surfaceAlt,
                           alignItems: 'center', justifyContent: 'center' }}>
              {item.output?.thumbnail_url ? (
                <Image source={{ uri: item.output.thumbnail_url }} style={{ width: '100%', height: '100%' }} contentFit="cover" />
              ) : <Body muted style={{ fontSize: 13, textAlign: 'center' }}>{t(stageKey(item.status))}</Body>}
            </View>
          </Pressable>
        </Link>
      )}
    />
  );
}
