import '@/lib/i18n';

import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useTranslation } from 'react-i18next';
import { useColorScheme } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider } from '@/lib/auth';

export default function RootLayout() {
  const scheme = useColorScheme();
  const { t } = useTranslation();
  return (
    <SafeAreaProvider>
      <ThemeProvider value={scheme === 'dark' ? DarkTheme : DefaultTheme}>
        <AuthProvider>
          <StatusBar style="auto" />
          <Stack screenOptions={{ headerBackButtonDisplayMode: 'minimal' }}>
            <Stack.Screen name="index" options={{ headerShown: false }} />
            <Stack.Screen name="onboarding" options={{ headerShown: false }} />
            <Stack.Screen name="sign-in" options={{ title: '' }} />
            <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
            <Stack.Screen name="template/[id]" options={{ title: '' }} />
            <Stack.Screen name="job/[id]" options={{ title: t('job.title') }} />
            <Stack.Screen name="identity/new" options={{ title: t('identity.title'), presentation: 'modal' }} />
            <Stack.Screen name="multi/[videoId]" options={{ title: t('multi.title') }} />
            <Stack.Screen name="paywall" options={{ title: '', presentation: 'modal' }} />
            <Stack.Screen name="t/[token]" options={{ title: '' }} />
            <Stack.Screen name="studio/[id]" options={{ title: t('studio.title') }} />
            <Stack.Screen name="creator" options={{ title: t('creator.title') }} />
          </Stack>
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
