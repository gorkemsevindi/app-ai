import { router } from 'expo-router';
import { useTranslation } from 'react-i18next';
import { Pressable, View } from 'react-native';

import { Body, Title } from '@/components/ui';
import { radius, spacing, useColors } from '@/lib/theme';

const CARDS = ['series', 'film', 'stars', 'life_story'] as const;

// V7 home: "Which story do you want to tell?" — four entry flows sharing one production infrastructure.
export function StoryEntry() {
  const { t } = useTranslation();
  const c = useColors();
  const go = (kind: (typeof CARDS)[number]) => {
    if (kind === 'stars') router.push('/characters');
    else if (kind === 'life_story') router.push('/productions/life');
    else router.push({ pathname: '/productions/new', params: { kind } });
  };
  return (
    <View style={{ gap: spacing.sm }}>
      <Title>{t('production.question')}</Title>
      <Body muted>{t('production.slogan')}</Body>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
        {CARDS.map((k) => (
          <Pressable key={k} accessibilityRole="button" accessibilityLabel={t(`production.entry.${k}`)} onPress={() => go(k)}
            style={{ width: '48%', minHeight: 84, padding: spacing.md, borderRadius: radius.md, backgroundColor: c.surfaceAlt,
                     borderWidth: 1, borderColor: c.border, justifyContent: 'center' }}>
            <Body>{t(`production.entry.${k}`)}</Body>
          </Pressable>
        ))}
      </View>
    </View>
  );
}
