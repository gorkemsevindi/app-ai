import * as SecureStore from 'expo-secure-store';
import { Share } from 'react-native';

import { api, ApiError } from '@/lib/api';
import { isShareToken } from '@/lib/shareToken';

// A share link opened before sign-up is remembered until the account exists, then attributed once.
// The server decides eligibility (new user, click window, first touch, no self-referral).
const KEY = 'share.pending';

export async function rememberShareToken(token: string): Promise<void> {
  if (isShareToken(token)) await SecureStore.setItemAsync(KEY, token);
}

export async function claimPendingAttribution(): Promise<void> {
  const token = await SecureStore.getItemAsync(KEY);
  if (!token) return;
  try {
    await api(`/share-links/${encodeURIComponent(token)}/attribute`, { method: 'POST' });
    await SecureStore.deleteItemAsync(KEY);
  } catch (e) {
    // keep it for the next app start after a network error; a rejected/revoked link is dropped
    if (e instanceof ApiError && e.status < 500) await SecureStore.deleteItemAsync(KEY);
  }
}

/** "Try this template" link through the OS share sheet. The link never exposes the user's own video. */
export async function shareTemplateLink(body: { template_id?: string; job_id?: string }, message: string): Promise<void> {
  const r = await api<{ url: string }>('/share-links', { body });
  api('/events', { body: [{ name: 'share_link_created', props: body }] }).catch(() => {});
  await Share.share({ message: `${message} ${r.url}`, url: r.url });
}
