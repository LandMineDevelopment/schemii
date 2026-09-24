import { renderAdminWorkspace } from './admin-workspace.js';
import { loginUrl } from './login-return.js';
import { ApiError, requestJson } from './http.js';
import { element as el } from './dom.js';
import { installDetailsMenu } from './ui.js';
import { installProductNavigation } from './product-navigation.js';
import { confirmAction } from './confirmation.js';
import { reasoningLabel, reasoningLevels } from './ai-reasoning.js';
import { canAccessProduct, signInDestination, signOut, sessionChanged } from './accounts-session.js';
const main = document.getElementById('accounts-main');
const nav = document.getElementById('account-navigation');
const mobileNav = document.getElementById('account-mobile-navigation');
let mobileNavMenuController = null;
const AUTH = '/api/v1/auth', ADMIN = '/api/v1/admin';
const button = (text, action, primary = false) => { const b = el('button', { type: 'button', className: `ui-button${primary ? ' primary' : ''}`, text }); b.onclick = action; return b; };
const link = (text, href) => el('a', { className: 'ui-button', text, attrs: { href } });
function field(label, { value = '', type = 'text', autocomplete, required = true } = {}) {
  const input = el('input', { attrs: { type, autocomplete, required: required ? '' : undefined, ...(type === 'password' ? { minlength: 12 } : {}), maxlength: type === 'password' ? 256 : 128 } }); input.value = value;
  return { input, node: el('label', { className: 'account-field' }, [label, input]) };
}
function check(label, checked = false) { const input = el('input', { attrs: { type: 'checkbox' } }); input.checked = checked; return { input, node: el('label', { className: 'account-check' }, [input, label]) }; }
function formWithSubmit(label, submit, fields, errorMessage = error => error.message) {
  const error = el('p', { className: 'account-error', attrs: { role: 'alert' } });
  const form = el('form', { className: 'account-form' }, fields);
  const save = el('button', { type: 'submit', className: 'ui-button primary', text: label });
  form.append(error, save);
  form.onsubmit = async event => { event.preventDefault(); save.disabled = true; error.textContent = ''; try { await submit(); } catch (e) { error.textContent = errorMessage(e); } finally { save.disabled = false; } };
  return form;
}
function signInErrorMessage(error) {
  if (error instanceof ApiError) {
    if (error.code === 'invalid_credentials' && error.status === 401) return 'Incorrect username or password. Check both fields and try again.';
    if (error.code === 'sign_in_rate_limited' && error.status === 429) return 'Too many sign-in attempts. Try again in 15 minutes.';
    if (error.code === 'network_error' || error.code === 'request_timeout') return 'Could not reach the sign-in service. Check your connection and try again.';
    if (error.status >= 500) return 'The sign-in service is unavailable. Try again shortly.';
  }
  return error.message;
}
function heading(title, description) { return [el('small', { className: 'eyebrow', text: 'Schemii · Account access' }), el('h1', { text: title }), el('p', { text: description })]; }
async function login(status) {
  document.title = `${status.setup_required ? 'Set up' : 'Sign in'} · Schemii`;
  const username = field('Username', { autocomplete: 'username' }), password = field('Password', { type: 'password', autocomplete: status.setup_required ? 'new-password' : 'current-password' });
  if (!status.setup_required) password.input.removeAttribute('minlength');
  const display = field('Display name', { autocomplete: 'name' }), token = field('Setup token', { autocomplete: 'off' });
  const setup = status.setup_required;
  const panel = el('section', { className: 'account-panel account-login' }, [...heading(setup ? 'Create your administrator account' : 'Welcome back', setup ? 'Use the setup token configured for this installation. Your administrator account will manage users, roles, and shared report access.' : 'Sign in to your reports and database tools.')]);
  panel.append(formWithSubmit(setup ? 'Create administrator account' : 'Sign in', async () => {
    const account = await requestJson(`${AUTH}/${setup ? 'setup' : 'login'}`, { method: 'POST', body: { username: username.input.value.trim(), password: password.input.value, ...(setup ? { display_name: display.input.value.trim(), setup_token: token.input.value } : {}) } });
    sessionChanged(); location.replace(signInDestination(account));
  }, [...(setup ? [token.node, display.node] : []), username.node, password.node], setup ? undefined : signInErrorMessage));
  main.replaceChildren(panel);
}
function accountPage(account) {
  const user = account.user;
  main.replaceChildren(...heading(user.display_name || user.username, `Signed in as ${user.username}. Your roles determine which applications and database connections you can use.${account.is_admin ? ' You can provision accounts and roles.' : ''}`));
  const current = field('Current password', { type: 'password', autocomplete: 'current-password' }); current.input.removeAttribute('minlength');
  const password = field('New password', { type: 'password', autocomplete: 'new-password' });
  const confirmation = field('Confirm new password', { type: 'password', autocomplete: 'new-password' });
  const panel = el('section', { className: 'account-panel account-login' }, [el('h2', { text: 'Change password' }), el('p', { text: 'Use at least 12 characters. Changing your password revokes your sessions; sign in again afterward.' })]);
  panel.append(formWithSubmit('Change password', async () => {
    if (password.input.value !== confirmation.input.value) throw new Error('The new passwords do not match.');
    await requestJson(`${AUTH}/change-password`, { method: 'POST', body: { current_password: current.input.value, password: password.input.value } });
    sessionChanged(); location.replace('/login');
  }, [current.node, password.node, confirmation.node])); main.append(panel);
}
const AI_PRODUCTS = [['schemii', 'Schemii'], ['schemoo', 'Schemoo'], ['schemer', 'Schemer']];
const ROLE_CAPABILITY_LABELS = {
  'schemii:access': 'Schemii', 'schemoo:access': 'Schemoo',
  'schemer:access': 'Schemer viewer', 'schemer:author': 'Schemer author',
};
const ACCESS_CAPABILITIES = [
  ['schemii:access', 'Schemii — schema design and SQL'],
  ['schemoo:access', 'Schemoo — semantic models'],
  ['schemer:access', 'Schemer — view granted dashboards'],
  ['schemer:author', 'Schemer — create and edit dashboards'],
];
function directAccess(user) { return user?.direct_access || { capabilities: [], connections: [], dashboards: [] }; }
function accessEditor(access, resources, { selectedConnection = null } = {}) {
  const current = access || { capabilities: [], connections: [], dashboards: [] };
  const capabilities = ACCESS_CAPABILITIES.map(([id, label]) => ({ id, ...check(label, current.capabilities.includes(id)) }));
  const managed = resources.connections.filter(connection => connection.ownership === 'schemii');
  const profiles = managed.map(connection => {
    const grant = current.connections.find(item => item.connection_id === connection.id && item.owner_id === connection.owner_id);
    const preselected = !access && selectedConnection?.id === connection.id;
    const enabled = check(`${connection.name || connection.database} · ${connection.username}`,
      !!grant || preselected);
    const authoring = check('Use in Schemii and Schemoo tools or Schemer editing', grant?.allow_authoring);
    const options = el('div', { className: 'account-editor', hidden: !grant && !preselected }, [authoring.node,
      el('p', { text: 'Enable this even for read-only PostgreSQL profiles. This permits app workflows; PostgreSQL still controls visible rows, columns, and write privileges.' }),
    ]);
    enabled.input.onchange = () => { options.hidden = !enabled.input.checked; };
    return { connection, input: enabled.input, authoring, node: el('div', { className: 'account-grant' }, [enabled.node, options]) };
  });
  const dashboards = resources.dashboards.map(dashboard => {
    const saved = current.dashboards.find(grant => grant.dashboard_id === dashboard.id && grant.owner_id === dashboard.owner_id);
    const grant = saved && managed.some(connection => connection.id === saved.connection_id && connection.owner_id === saved.connection_owner_id) ? saved : null;
    const enabled = check(dashboard.name || dashboard.id, !!grant);
    const exp = check('Allow export', grant?.can_export), drill = check('Allow drill-through', grant?.can_drill);
    const select = el('select', { attrs: { 'aria-label': `Database connection for ${dashboard.name || dashboard.id}` } });
    select.append(el('option', { text: 'Choose an authorized connection', attrs: { value: '' } }));
    managed.forEach((connection, index) => select.append(el('option', { text: `Schemii-owned · ${connection.name || connection.id}`, attrs: { value: String(index) } })));
    if (grant) select.value = String(managed.findIndex(connection => connection.id === grant.connection_id && connection.owner_id === grant.connection_owner_id));
    const options = el('div', { className: 'account-editor', hidden: !grant }, [el('label', { className: 'account-field' }, ['Run using', select]), exp.node, drill.node]);
    enabled.input.onchange = () => { options.hidden = !enabled.input.checked; };
    return { dashboard, enabled, select, exp, drill, node: el('div', { className: 'account-grant' }, [enabled.node, options]) };
  });
  function values() {
    const selected = profiles.filter(profile => profile.input.checked).map(profile => profile.connection);
    const chosenCapabilities = capabilities.filter(capability => capability.input.checked).map(capability => capability.id);
    if (chosenCapabilities.includes('schemer:author') && !chosenCapabilities.includes('schemer:access')) {
      throw new Error('Schemer editing requires Schemer access in the same access set.');
    }
    if (profiles.some(profile => profile.input.checked && profile.authoring.input.checked)
      && !['schemii:access', 'schemoo:access', 'schemer:author'].some(capability => chosenCapabilities.includes(capability))) {
      throw new Error('Enable an app capability in this access set before allowing database tool use.');
    }
    const chosenDashboards = dashboards.filter(item => item.enabled.input.checked).map(item => {
      const connection = item.select.value === '' ? null : managed[Number(item.select.value)];
      if (!connection || !selected.includes(connection)) throw new Error(`Select an authorized connection for “${item.dashboard.name || item.dashboard.id}”.`);
      return { dashboard_id: item.dashboard.id, owner_id: item.dashboard.owner_id, connection_id: connection.id,
        connection_owner_id: connection.owner_id, can_export: item.exp.input.checked, can_drill: item.drill.input.checked };
    });
    if (chosenDashboards.length && !chosenCapabilities.includes('schemer:access')) {
      throw new Error('Shared dashboards require Schemer access in the same access set.');
    }
    return { capabilities: chosenCapabilities,
      connections: profiles.filter(profile => profile.input.checked).map(profile => ({ connection_id: profile.connection.id,
        owner_id: profile.connection.owner_id, allow_authoring: profile.authoring.input.checked })),
      dashboards: chosenDashboards };
  }
  return { capabilities, profiles, dashboards, managed, values };
}
function roleAccess(role, resources, zen = {}, shared = {}) {
  const apps = role.capabilities.map(capability => ROLE_CAPABILITY_LABELS[capability] || capability);
  const connections = role.connections.map(grant => {
    const profile = resources.connections.find(connection => connection.id === grant.connection_id && connection.owner_id === grant.owner_id);
    const name = profile?.name || profile?.database || grant.connection_id;
    return grant.allow_authoring ? `${name} (app tools enabled)` : name;
  });
  const dashboards = role.dashboards.map(grant => {
    const dashboard = resources.dashboards.find(item => item.id === grant.dashboard_id && item.owner_id === grant.owner_id);
    const permissions = [grant.can_export && 'export', grant.can_drill && 'drill-through'].filter(Boolean);
    return `${dashboard?.name || grant.dashboard_id}${permissions.length ? ` (${permissions.join(', ')})` : ''}`;
  });
  const ai = [
    ...(zen.roleGrants || []).filter(grant => grant.roleId === role.id).map(grant => `Zen ${aiScopeLabel(grant, resources)}`),
    ...(shared.roleGrants || []).filter(grant => grant.roleId === role.id)
      .map(grant => `Codex ${aiScopeLabel(grant, resources)} ${grant.modelId} (${reasoningLabel(grant.reasoningEffort)})`),
  ];
  return [
    `Apps: ${apps.join(', ') || 'none'}`,
    `Databases: ${connections.join(', ') || 'none'}`,
    `Dashboards: ${dashboards.join(', ') || 'none'}`,
    `AI: ${ai.join('; ') || 'none'}`,
  ].join(' · ');
}
function roleAiScopes(role) {
  if (!role) return [];
  const scopes = [];
  const add = (product, connectionOwnerId, connectionId) => {
    if (!scopes.some(scope => scope.product === product && scope.connectionOwnerId === connectionOwnerId && scope.connectionId === connectionId)) {
      scopes.push({ product, connectionOwnerId, connectionId });
    }
  };
  for (const [product] of AI_PRODUCTS) {
    if (!role.capabilities.includes(`${product}:access`)) continue;
    if (product === 'schemii') add(product, null, null);
    for (const grant of role.connections) {
      if (grant.owner_id === 'user_schemii_connection_pool' && grant.allow_authoring
        && (product !== 'schemer' || role.capabilities.includes('schemer:author'))) {
        add(product, grant.owner_id, grant.connection_id);
      }
    }
    if (product === 'schemer') for (const grant of role.dashboards) {
      if (grant.connection_owner_id === 'user_schemii_connection_pool') add(product, grant.connection_owner_id, grant.connection_id);
    }
  }
  return scopes;
}
function aiScopeLabel(scope, resources) {
  const app = AI_PRODUCTS.find(([id]) => id === scope.product)?.[1] || scope.product;
  if (scope.connectionId === null) return `${app} · no database`;
  const profile = resources.connections.find(connection => connection.id === scope.connectionId && connection.owner_id === scope.connectionOwnerId);
  return `${app} · ${profile?.name || profile?.database || `Unavailable database · …${String(scope.connectionId || '').slice(-6)}`}`;
}
function zenAdministration(zen, users, roles, resources) {
  const panel = el('section', { className: 'account-panel', attrs: { 'aria-labelledby': 'zen-administration-title' } }, [
    el('h2', { text: 'OpenCode Zen for this installation', attrs: { id: 'zen-administration-title' } }),
    el('p', { text: 'Save one installation-owned key, then grant access to each person, app, and database profile. People never see the key. These grants do not add application access, database access, or Schemer authoring rights.' }),
  ]);
  if (zen.error) {
    panel.append(el('p', { className: 'account-error', text: `Zen administration could not be loaded: ${zen.error.message}`, attrs: { role: 'alert' } }), button('Retry loading Zen settings', adminPage));
    return panel;
  }

  const key = field(zen.connected ? 'Replace Zen API key' : 'Zen API key', { type: 'password', autocomplete: 'off' });
  key.input.removeAttribute('minlength'); key.input.maxLength = 16384; key.input.spellcheck = false;
  const connectionStatus = el('p', { className: zen.connected ? 'account-message' : 'account-inline-status', text: zen.connected ? 'Installation key stored. Its value is never displayed.' : 'No installation key stored. Zen remains unavailable until a key is saved.' });
  const keyForm = formWithSubmit(zen.connected ? 'Replace installation key' : 'Save installation key', async () => {
    try {
      const apiKey = key.input.value.trim();
      if (!apiKey) throw new Error('Enter a Zen API key.');
      await requestJson(`${ADMIN}/ai/zen/credential`, { method: 'PUT', body: { apiKey } });
      await adminPage();
    } finally { key.input.value = ''; }
  }, [key.node, el('p', { text: 'Create a key in your OpenCode account. It is encrypted on this installation and never added to the repository.' }),
    el('a', { className: 'ui-button', text: 'Get a Zen API key', attrs: { href: 'https://opencode.ai/auth', target: '_blank', rel: 'noopener noreferrer' } })]);
  keyForm.classList.add('account-ai-key-form');
  const keyActions = el('div', { className: 'account-actions' }, [keyForm]);
  if (zen.connected) keyActions.append(button('Remove installation key', () => confirmAction({
    title: 'Remove installation Zen key?',
    message: 'Zen will stop working for everyone on this installation.',
    details: 'Existing grants remain saved. Add a new key to restore access to granted users.',
    confirmLabel: 'Remove key',
    onConfirm: async () => { await requestJson(`${ADMIN}/ai/zen/credential`, { method: 'DELETE' }); await adminPage(); },
  })));
  const keyDetails = el('details', {}, [el('summary', { text: zen.connected ? 'Replace or remove connection' : 'Connect OpenCode Zen' }), keyActions]);
  panel.append(connectionStatus, keyDetails);

  return panel;
}
function sharedCodexAdministration(shared, users, roles, resources) {
  const panel = el('section', { className: 'account-panel', attrs: { 'aria-labelledby': 'shared-codex-administration-title' } }, [
    el('h2', { text: 'Shared ChatGPT Codex for this installation', attrs: { id: 'shared-codex-administration-title' } }),
    el('p', { text: 'Share an administrator’s existing ChatGPT Codex sign-in through an encrypted installation-owned connection. Grant each person access by app and database profile. People never see the connection, and these grants do not add app, database, or Schemer authoring rights.' }),
  ]);
  if (shared.error) {
    panel.append(el('p', { className: 'account-error', text: `Shared Codex administration could not be loaded: ${shared.error.message}`, attrs: { role: 'alert' } }), button('Retry loading shared Codex settings', adminPage));
    return panel;
  }
  panel.append(el('p', { className: shared.connected ? 'account-message' : 'account-inline-status', attrs: { role: 'status' }, text: shared.connected
    ? 'Installation connection stored. Granted people can use their assigned Codex model without their own sign-in.'
    : 'No installation connection stored. Shared Codex is unavailable until an administrator connects it.' }));
  const verified = shared.verifiedModels || [];
  panel.append(el('p', { className: 'account-inline-status', text: shared.catalogCheckedAt
    ? `Verified for this account: ${verified.length ? verified.map(model => model.name || model.id).join(', ') : 'No supported Codex models'}.`
    : `Supported model catalog (unverified): ${(shared.models || []).map(model => model.name || model.id).join(', ') || 'Unavailable'}. Test the shared connection before granting access.` }));
  const testStatus = el('p', { className: 'account-inline-status', attrs: { role: 'status' } });
  if (shared.connected) panel.append(button('Test shared Codex connection', async event => {
    const trigger = event.currentTarget; trigger.disabled = true; testStatus.textContent = 'Checking available Codex models…';
    try {
      const result = await requestJson(`${ADMIN}/ai/shared-codex/test`, { method: 'POST' });
      const models = result.models || [];
      testStatus.textContent = result.connected
        ? `Connection verified. ${models.length} ${models.length === 1 ? 'model' : 'models'} available.`
        : `Connection unavailable: ${result.error || 'Codex sign-in could not be verified.'}`;
      if (result.connected) { shared.verifiedModels = models; shared.catalogCheckedAt = result.checkedAt || new Date().toISOString(); await adminPage(); }
    } catch (error) { testStatus.textContent = `Connection test failed: ${error.message}`; }
    finally { trigger.disabled = false; }
  }), testStatus);
  const credentialActions = el('div', { className: 'account-actions' });
  if (shared.sourceConnected) {
    credentialActions.append(button(shared.connected ? 'Replace shared Codex connection' : 'Share my Codex sign-in', async event => {
      const trigger = event.currentTarget; trigger.disabled = true;
      try { await requestJson(`${ADMIN}/ai/shared-codex/credential`, { method: 'PUT', body: {} }); await adminPage(); }
      catch (error) { trigger.disabled = false; panel.append(el('p', { className: 'account-error', text: `Could not share Codex sign-in: ${error.message}`, attrs: { role: 'alert' } })); }
    }, !shared.connected));
  } else {
    panel.append(el('p', { className: 'account-inline-status', text: 'To connect or replace this installation connection, first sign in to your personal Codex account from an AI assistant, then return here.' }));
    credentialActions.append(link('Open Schemii assistant', '/'));
  }
  if (shared.connected) credentialActions.append(button('Remove shared Codex connection', () => confirmAction({
    title: 'Remove shared Codex connection?',
    message: 'Shared Codex will stop working for everyone on this installation.',
    details: 'Existing grants remain saved. Share a new Codex sign-in to restore access to granted people.',
    confirmLabel: 'Remove connection',
    onConfirm: async () => { await requestJson(`${ADMIN}/ai/shared-codex/credential`, { method: 'DELETE' }); await adminPage(); },
  })));
  panel.append(credentialActions);

  return panel;
}
async function adminPage() {
  document.title = 'Administration · Schemii';
  const [users, roles, resources, managedResult, zenResult, sharedCodexResult] = await Promise.all([
    requestJson(`${ADMIN}/accounts`), requestJson(`${ADMIN}/roles`), requestJson(`${ADMIN}/resources`),
    requestJson(`${ADMIN}/schemii-connections`).then(value => ({ connections: value.connections })).catch(error => ({ error })),
    requestJson(`${ADMIN}/ai/zen`).catch(error => ({ error })),
    requestJson(`${ADMIN}/ai/shared-codex`).catch(error => ({ error })),
  ]);
  renderAdminWorkspace({ main, users, roles, resources, managedResult, zenResult, sharedCodexResult,
    reload: adminPage, accessEditor, roleAiScopes, roleAccess, directAccess, aiScopeLabel,
    providerPanels: () => [sharedCodexAdministration(sharedCodexResult, users, roles, resources), zenAdministration(zenResult, users, roles, resources)] });
}
async function initialize() {
  try {
    const status = await requestJson(`${AUTH}/status`);
    if (!status.authenticated) { if (location.pathname !== '/login') { location.replace(loginUrl()); return; } await login(status); return; }
    const account = await requestJson(`${AUTH}/me`);
    if (location.pathname === '/login') { location.replace(signInDestination(account)); return; }
    nav.replaceChildren();
    for (const [product, label, href] of [['schemii', 'Schemii', '/'], ['schemoo', 'Schemoo', '/schemoo'], ['schemer', 'Schemer', '/schemer']]) {
      if (canAccessProduct(account, product)) nav.append(link(label, href));
    }
    nav.append(link('Account', '/account'));
    if (account.is_admin) nav.append(link('Administration', '/admin'));
    nav.append(button('Sign out', async () => { try { await signOut(); } catch (e) { main.prepend(el('p', { className: 'account-error', text: e.message, attrs: { role: 'alert' } })); } }));
    mobileNavMenuController?.destroy();
    mobileNavMenuController = installDetailsMenu(installProductNavigation(mobileNav, { account }));
    if (location.pathname === '/admin') { if (!account.is_admin) throw new Error('Administrator access is required.'); await adminPage(); } else accountPage(account);
  } catch (e) { main.replaceChildren(...heading('Account unavailable', e.message), button('Try again', initialize)); }
}
void initialize();
