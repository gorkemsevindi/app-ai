import type { TFunction } from 'i18next';

import { ApiError } from './api';

/** Maps API error codes to localized, user-friendly copy. Unknown codes get a generic message. */
export function errorMessage(t: TFunction, e: unknown): string {
  if (e instanceof ApiError) {
    const key = `errors.${e.code}`;
    const msg = t(key, { defaultValue: '' });
    if (msg) return msg;
  }
  return t('errors.generic');
}
