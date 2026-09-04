const el = id => document.getElementById(id);
let loginId = null;
let timer;
let version = 0;
async function api(path, method = 'GET') {
  const response = await fetch(`/api/v1/ai/prototype/${path}`, { method, cache: 'no-store' });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || body.message || 'Request failed. Please try again.');
  return body;
}
function stop() { version++; clearTimeout(timer); loginId = null; el('device').hidden = true; el('cancel').hidden = true; }
function error(e) { el('status').textContent = e.message; el('connect').disabled = false; }
async function refresh() {
  const { credentials } = await api('credentials');
  const connected = credentials.length > 0;
  el('status').textContent = connected ? 'Connected. Credentials are encrypted on this server.' : 'No prototype account connected. Connect again if your credentials expired after inactivity.';
  el('connect').textContent = connected ? 'Reconnect Codex' : 'Connect Codex';
  el('connect').disabled = false;
  el('disconnect').hidden = !connected;
}
async function poll(epoch) {
  try {
    const state = await api(`logins/${encodeURIComponent(loginId)}`);
    if (epoch !== version) return;
    if (state.status === 'succeeded') { stop(); await refresh(); return; }
    if (state.status !== 'pending') { stop(); throw new Error(state.message || 'Sign-in expired. Start again.'); }
    if (state.verificationUrl && state.userCode) {
      el('code').textContent = state.userCode;
      el('authorize').href = state.verificationUrl;
      el('device').hidden = false;
      el('status').textContent = 'Waiting for you to authorize at OpenAI…';
    }
    timer = setTimeout(() => poll(epoch), 2000);
  } catch (e) { if (epoch === version) { stop(); error(e); } }
}
el('connect').onclick = async () => {
  stop(); el('connect').disabled = true; el('status').textContent = 'Preparing a secure device sign-in…';
  try { const result = await api('login', 'POST'); loginId = result.id; el('cancel').hidden = false; poll(version); }
  catch (e) { error(e); }
};
el('cancel').onclick = async () => {
  const id = loginId; stop();
  try { if (id) await api(`logins/${encodeURIComponent(id)}`, 'DELETE'); await refresh(); }
  catch (e) { error(e); }
};
el('disconnect').onclick = async () => {
  if (!confirm('Remove this prototype account from Schemii? Existing OpenCode chat is unaffected.')) return;
  stop();
  try { await api('credentials', 'DELETE'); await refresh(); } catch (e) { error(e); }
};
refresh().catch(error);
