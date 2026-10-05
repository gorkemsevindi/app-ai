import { useColorScheme } from 'react-native';

// Original visual identity: deep ink + electric coral accent. Contrast pairs checked for WCAG AA.
const palette = {
  light: {
    bg: '#FAFAF7', surface: '#FFFFFF', surfaceAlt: '#F0EFEA', text: '#16161A', textMuted: '#5A5A66',
    accent: '#E8453C', accentText: '#FFFFFF', border: '#E2E1DA', success: '#1F8A4C', danger: '#B3261E',
  },
  dark: {
    bg: '#0E0E12', surface: '#18181F', surfaceAlt: '#22222B', text: '#F4F4F6', textMuted: '#A8A8B3',
    accent: '#FF6A5C', accentText: '#16161A', border: '#2C2C36', success: '#4CC38A', danger: '#FF8A80',
  },
};

export type Colors = typeof palette.light;

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 };
export const radius = { sm: 8, md: 14, lg: 22, pill: 999 };

export function useColors(): Colors {
  return useColorScheme() === 'dark' ? palette.dark : palette.light;
}
