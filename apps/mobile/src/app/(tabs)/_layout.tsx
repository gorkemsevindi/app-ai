import { Tabs } from 'expo-router/tabs';
import { useTranslation } from 'react-i18next';

import { useColors } from '@/lib/theme';

export default function TabsLayout() {
  const { t } = useTranslation();
  const c = useColors();
  return (
    <Tabs screenOptions={{ tabBarActiveTintColor: c.accent, headerTitleStyle: { fontWeight: '800' } }}>
      <Tabs.Screen name="index" options={{ title: t('tabs.discover') }} />
      <Tabs.Screen name="video" options={{ title: t('tabs.video') }} />
      <Tabs.Screen name="library" options={{ title: t('tabs.library') }} />
      <Tabs.Screen name="profile" options={{ title: t('tabs.profile') }} />
    </Tabs>
  );
}
