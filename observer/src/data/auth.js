// The observer server's token (O3). `sovereign-world observe` prints an address ending in
// `#token=…`; the part after `#` is never sent to a server. The page reads the token from it
// once and then drops it from the address, so neither the address bar, the history nor a
// restored session keeps it. It is held in memory only and sent as an
// `Authorization: Bearer` header; nothing here stores it. Reloading the page therefore needs
// the printed address again.

/** The token in the page address's fragment, or null. */
export function tokenFromLocation(loc = globalThis.location) {
  const hash = loc?.hash ?? '';
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  const token = params.get('token');
  return token ? token : null;
}

/** The token in the fragment, which is then removed from the address (one history entry,
 * replaced in place, no reload); null, and the address left alone, when there is none. */
export function takeTokenFromLocation(loc = globalThis.location, hist = globalThis.history) {
  const token = tokenFromLocation(loc);
  if (token) {
    const url = new URL(loc.href);
    url.hash = '';
    hist?.replaceState?.(hist.state, '', url);
  }
  return token;
}

export class AuthError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AuthError';
  }
}

/** A fetch that carries the token; a refusal becomes an AuthError with a plain message. */
export function authorisedFetch(token, get = (...args) => fetch(...args)) {
  if (!token) {
    throw new AuthError(
      'This observer needs its token: open the address `sovereign-world observe` printed' +
        ' (the token is dropped from the address once read, so a reload needs it again).',
    );
  }
  return async (url, init = {}) => {
    const headers = new Headers(init.headers ?? {});
    headers.set('Authorization', `Bearer ${token}`);
    const res = await get(url, { ...init, headers });
    if (res.status === 401) {
      throw new AuthError('The observer server refused the token (401): open the address it printed.');
    }
    return res;
  };
}
