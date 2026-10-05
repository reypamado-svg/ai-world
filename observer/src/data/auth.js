// The observer server's token (O3). `sovereign-world observe` prints an address ending in
// `#token=…`: the part after `#` never leaves the browser, so the token is not sent in any
// address or kept in any log. It is read from there, held in memory only, and sent as an
// `Authorization: Bearer` header. Nothing here stores it.

/** The token in the page address's fragment, or null. */
export function tokenFromLocation(loc = globalThis.location) {
  const hash = loc?.hash ?? '';
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  const token = params.get('token');
  return token ? token : null;
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
    throw new AuthError('This observer needs its token: open the address `sovereign-world observe` printed.');
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
