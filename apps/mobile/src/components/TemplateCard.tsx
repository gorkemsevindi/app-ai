import { Image } from 'expo-image';
import { Link } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { Pressable, Text, View } from 'react-native';

import type { Template } from '@/lib/api';
import { radius, spacing, useColors } from '@/lib/theme';

export function TemplateCard({ tpl }: { tpl: Template & { person_count?: number } }) {
  const c = useColors();
  const { t } = useTranslation();
  return (
    <Link href={{ pathname: '/template/[id]', params: { id: tpl.id } }} asChild>
      <Pressable accessibilityRole="button" accessibilityLabel={`${tpl.title}, ${t('template.cost', { count: tpl.credit_cost })}`}
        style={{ flex: 1, margin: spacing.xs }}>
        <View style={{ aspectRatio: 9 / 16, borderRadius: radius.md, overflow: 'hidden', backgroundColor: c.surfaceAlt }}>
          {tpl.thumbnail_url?.startsWith('http') ? (
            <Image source={{ uri: tpl.thumbnail_url }} style={{ flex: 1 }} contentFit="cover" transition={150}
              accessibilityIgnoresInvertColors />
          ) : null}
          {tpl.pro_only ? (
            <Text style={{ position: 'absolute', top: 8, left: 8, backgroundColor: c.accent, color: c.accentText,
                           paddingHorizontal: 8, borderRadius: radius.pill, fontWeight: '800' }}>{t('template.pro')}</Text>
          ) : null}
          {(tpl.person_count ?? 1) > 1 ? (
            <Text style={{ position: 'absolute', top: 8, right: 8, backgroundColor: c.surface, color: c.text,
                           paddingHorizontal: 8, borderRadius: radius.pill, fontWeight: '700' }}>
              {t('remix.people', { count: tpl.person_count })}
            </Text>
          ) : null}
        </View>
        <Text style={{ color: c.text, fontWeight: '700', marginTop: spacing.xs }} numberOfLines={1}>{tpl.title}</Text>
        <Text style={{ color: c.textMuted }}>{t('template.cost', { count: tpl.credit_cost })}</Text>
      </Pressable>
    </Link>
  );
}
