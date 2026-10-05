import { getLocales } from 'expo-localization';
import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, KeyboardAvoidingView, Platform, TextInput } from 'react-native';

import { Body, Button, Screen, Title } from '@/components/ui';
import { useAuth } from '@/lib/auth';
import { errorMessage } from '@/lib/errors';
import { radius, spacing, useColors } from '@/lib/theme';

export default function SignIn() {
  const { t } = useTranslation();
  const c = useColors();
  const { consent } = useLocalSearchParams<{ consent?: string }>();
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<'up' | 'in'>(consent ? 'up' : 'in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const input = { borderWidth: 1, borderColor: c.border, borderRadius: radius.md, padding: spacing.md, color: c.text,
                  backgroundColor: c.surface, fontSize: 17, minHeight: 50 } as const;

  const submit = async () => {
    setBusy(true);
    try {
      if (mode === 'up') {
        await signUp({ email, password, country: getLocales()[0]?.regionCode ?? undefined,
                       ageConfirmed: consent === '1', termsAccepted: consent === '1' });
      } else {
        await signIn(email, password);
      }
      router.replace('/(tabs)');
    } catch (e) {
      Alert.alert(errorMessage(t, e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <Screen style={{ gap: spacing.md }}>
        <Title>{mode === 'up' ? t('auth.signUp') : t('auth.signIn')}</Title>
        {/* Sign in with Apple / Google: native modules wired in Phase 1b (expo-apple-authentication /
            Google ID token) -> POST /auth/social. Apple requires SIWA whenever other social logins exist. */}
        <TextInput accessibilityLabel={t('auth.email')} placeholder={t('auth.email')} placeholderTextColor={c.textMuted}
          autoCapitalize="none" keyboardType="email-address" autoComplete="email" style={input} value={email} onChangeText={setEmail} />
        <TextInput accessibilityLabel={t('auth.password')} placeholder={t('auth.password')} placeholderTextColor={c.textMuted}
          secureTextEntry autoComplete={mode === 'up' ? 'new-password' : 'current-password'} style={input}
          value={password} onChangeText={setPassword} />
        <Button title={mode === 'up' ? t('auth.signUp') : t('auth.signIn')} loading={busy}
          disabled={!email || password.length < 8 || (mode === 'up' && consent !== '1')} onPress={submit} />
        <Body muted onPress={() => (mode === 'up' ? setMode('in') : consent === '1' ? setMode('up') : router.replace('/onboarding'))}
          style={{ textAlign: 'center', padding: spacing.md }}>
          {mode === 'up' ? t('auth.haveAccount') : t('auth.noAccount')}
        </Body>
      </Screen>
    </KeyboardAvoidingView>
  );
}
