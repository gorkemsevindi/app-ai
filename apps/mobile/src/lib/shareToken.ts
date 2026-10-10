// Pure helpers for share-link tokens (`code.signature`, URL-safe base64). The server is the authority on
// validity; this only rejects obviously malformed input before a network call.
const TOKEN_RE = /^[A-Za-z0-9_-]{8,40}\.[A-Za-z0-9_-]{8,40}$/;

export function isShareToken(token: string | null | undefined): token is string {
  return !!token && token.length <= 80 && TOKEN_RE.test(token);
}

/** Extract a token from `https://host/t/<token>`, `aivideo://t/<token>` or a Play install-referrer string. */
export function tokenFromUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  const m = url.match(/(?:^|\/)t\/([^/?#]+)/) ?? url.match(/(?:^|[?&])referrer=([^&#]+)/);
  const tok = m ? decodeURIComponent(m[1]) : null;
  return isShareToken(tok) ? tok : null;
}
