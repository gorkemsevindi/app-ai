import { useTranslation } from 'react-i18next';

import { Body, Screen, Title } from '@/components/ui';
import { spacing } from '@/lib/theme';

// Phase 4: StoreKit 2 / Google Play Billing via the billing adapter, server-side verification at
// POST /purchases/verify. No external payment links for digital goods (App Store 3.1.1).
export default function Paywall() {
  const { t } = useTranslation();
  return (
    <Screen style={{ gap: spacing.md }}>
      <Title>{t('paywall.title')}</Title>
      <Body>{t('paywall.sub')}</Body>
      <Body muted>{t('paywall.comingSoon')}</Body>
    </Screen>
  );
}
