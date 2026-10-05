import { router } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ScrollView, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Body, Button, Checkbox, Title } from '@/components/ui';
import { spacing, useColors } from '@/lib/theme';

// First 5 seconds must explain the product: headline + 3 concrete steps, no carousel to swipe through.
export default function Onboarding() {
  const { t } = useTranslation();
  const c = useColors();
  const [age, setAge] = useState(false);
  const [terms, setTerms] = useState(false);
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.bg }}>
      <ScrollView contentContainerStyle={{ padding: spacing.xl, gap: spacing.lg, flexGrow: 1 }}>
        <Body muted>{t('app.tagline')}</Body>
        <Title style={{ fontSize: 34 }}>{t('onboarding.headline')}</Title>
        <Body>{t('onboarding.sub')}</Body>
        <View style={{ gap: spacing.sm, marginVertical: spacing.lg }}>
          {(['step1', 'step2', 'step3'] as const).map((k, i) => (
            <Body key={k} style={{ fontSize: 18, fontWeight: '600' }}>{`${i + 1}. ${t(`onboarding.${k}`)}`}</Body>
          ))}
        </View>
        <View style={{ flex: 1 }} />
        <Checkbox checked={age} onChange={setAge} label={t('onboarding.age')} />
        <Checkbox checked={terms} onChange={setTerms} label={t('onboarding.terms')} />
        <Button title={t('onboarding.continue')} disabled={!(age && terms)}
          onPress={() => router.push({ pathname: '/sign-in', params: { consent: '1' } })} />
      </ScrollView>
    </SafeAreaView>
  );
}
