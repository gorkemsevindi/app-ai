import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, type PressableProps, StyleSheet, Text, type TextProps, View, type ViewProps } from 'react-native';

import { radius, spacing, useColors } from '@/lib/theme';

export function Screen({ children, style, ...rest }: ViewProps & { children: ReactNode }) {
  const c = useColors();
  return <View style={[{ flex: 1, backgroundColor: c.bg, padding: spacing.lg }, style]} {...rest}>{children}</View>;
}

export function Title(props: TextProps) {
  const c = useColors();
  return <Text accessibilityRole="header" {...props} style={[{ color: c.text, fontSize: 26, fontWeight: '800' }, props.style]} />;
}

export function Body({ muted, ...props }: TextProps & { muted?: boolean }) {
  const c = useColors();
  // No fixed line heights / maxFontSizeMultiplier caps: respects Dynamic Type & large text.
  return <Text {...props} style={[{ color: muted ? c.textMuted : c.text, fontSize: 16 }, props.style]} />;
}

export function Button({ title, onPress, variant = 'primary', loading, disabled, ...rest }:
  PressableProps & { title: string; variant?: 'primary' | 'secondary' | 'danger'; loading?: boolean }) {
  const c = useColors();
  const bg = variant === 'primary' ? c.accent : variant === 'danger' ? c.danger : c.surfaceAlt;
  const fg = variant === 'primary' ? c.accentText : variant === 'danger' ? '#fff' : c.text;
  const off = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      accessibilityState={{ disabled: !!off, busy: !!loading }}
      onPress={onPress}
      disabled={off}
      style={({ pressed }) => [styles.btn, { backgroundColor: bg, opacity: off ? 0.5 : pressed ? 0.85 : 1 }]}
      {...rest}
    >
      {loading ? <ActivityIndicator color={fg} /> : <Text style={{ color: fg, fontSize: 17, fontWeight: '700' }}>{title}</Text>}
    </Pressable>
  );
}

export function Card({ children, style, ...rest }: ViewProps & { children: ReactNode }) {
  const c = useColors();
  return <View style={[{ backgroundColor: c.surface, borderRadius: radius.md, borderWidth: 1, borderColor: c.border, padding: spacing.lg }, style]} {...rest}>{children}</View>;
}

export function Checkbox({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  const c = useColors();
  return (
    <Pressable accessibilityRole="checkbox" accessibilityState={{ checked }} accessibilityLabel={label}
      onPress={() => onChange(!checked)} style={styles.row} hitSlop={8}>
      <View style={[styles.box, { borderColor: c.text, backgroundColor: checked ? c.accent : 'transparent' }]}>
        {checked ? <Text style={{ color: c.accentText, fontWeight: '900' }}>✓</Text> : null}
      </View>
      <Body style={{ flex: 1 }}>{label}</Body>
    </Pressable>
  );
}

export function Chip({ label, active, onPress, disabled }: { label: string; active?: boolean; onPress?: () => void; disabled?: boolean }) {
  const c = useColors();
  return (
    <Pressable accessibilityRole="button" accessibilityState={{ selected: !!active, disabled: !!disabled }}
      onPress={onPress} disabled={disabled}
      style={[styles.chip, { backgroundColor: active ? c.text : c.surfaceAlt, opacity: disabled ? 0.4 : 1 }]}>
      <Text style={{ color: active ? c.bg : c.text, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}

export function ProgressBar({ value }: { value: number }) {
  const c = useColors();
  return (
    <View accessibilityRole="progressbar" accessibilityValue={{ min: 0, max: 100, now: Math.round(value * 100) }}
      style={{ height: 10, borderRadius: 5, backgroundColor: c.surfaceAlt, overflow: 'hidden' }}>
      <View style={{ width: `${Math.round(value * 100)}%`, height: '100%', backgroundColor: c.accent }} />
    </View>
  );
}

const styles = StyleSheet.create({
  btn: { minHeight: 52, borderRadius: radius.pill, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.xl },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm },
  box: { width: 26, height: 26, borderRadius: 6, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  chip: { paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, borderRadius: radius.pill, minHeight: 40, justifyContent: 'center' },
});
