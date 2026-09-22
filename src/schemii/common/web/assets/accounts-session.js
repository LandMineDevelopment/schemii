import { requestJson } from './http.js';
let pending;
const channel = typeof window !== 'undefined' && typeof BroadcastChannel === 'function' ? new BroadcastChannel('schemii-session') : null;
if (typeof window !== 'undefined') {
  if (channel) channel.onmessage = () => { sessionStorage.clear(); location.reload(); };
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
}
export function sessionChanged() {
  sessionStorage.clear();
  channel?.postMessage('changed');
}
export function currentAccount() {
  return pending ||= requestJson('/api/v1/auth/me');
}
export function canAuthor(account) { return !!(account?.is_admin || account?.capabilities?.includes('author')); }
export async function signOut() {
  await requestJson('/api/v1/auth/logout', { method: 'POST', body: {} });
  // Product preferences and saved data remain on the server. No session data survives logout.
  sessionChanged();
  window.dispatchEvent(new Event('schemii:logout'));
  location.replace('/login');
}
