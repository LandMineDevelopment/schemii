// Only application pages are valid destinations; never follow an external URL.
const PAGES = new Set(['/', '/schemoo', '/schemer', '/account', '/admin']);
export function safeReturnPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value, 'https://app.invalid');
    if (url.origin !== 'https://app.invalid' || !PAGES.has(url.pathname)) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch { return null; }
}
export function loginUrl(location = globalThis.location) {
  const next = safeReturnPath(`${location?.pathname || '/'}${location?.search || ''}${location?.hash || ''}`);
  return next ? `/login?next=${encodeURIComponent(next)}` : '/login';
}
