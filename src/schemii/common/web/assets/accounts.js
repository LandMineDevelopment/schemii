import { loginUrl } from './login-return.js';
import { requestJson } from './http.js';
import { element as el } from './dom.js';
import { confirmAction } from './confirmation.js';
import { reasoningLabel, reasoningLevels } from './ai-reasoning.js';
import { canAccessProduct, signInDestination, signOut, sessionChanged } from './accounts-session.js';
const main = document.getElementById('accounts-main');
const nav = document.getElementById('account-navigation');
const AUTH = '/api/v1/auth', ADMIN = '/api/v1/admin';
const button = (text, action, primary = false) => { const b = el('button', { type: 'button', className: `ui-button${primary ? ' primary' : ''}`, text }); b.onclick = action; return b; };
const link = (text, href) => el('a', { className: 'ui-button', text, attrs: { href } });
function field(label, { value = '', type = 'text', autocomplete, required = true } = {}) {
  const input = el('input', { attrs: { type, autocomplete, required: required ? '' : undefined, ...(type === 'password' ? { minlength: 12 } : {}), maxlength: type === 'password' ? 256 : 128 } }); input.value = value;
  return { input, node: el('label', { className: 'account-field' }, [label, input]) };
}
function check(label, checked = false) { const input = el('input', { attrs: { type: 'checkbox' } }); input.checked = checked; return { input, node: el('label', { className: 'account-check' }, [input, label]) }; }
function formWithSubmit(label, submit, fields) {
  const error = el('p', { className: 'account-error', attrs: { role: 'alert' } });
  const form = el('form', { className: 'account-form' }, fields);
  const save = el('button', { type: 'submit', className: 'ui-button primary', text: label });
  form.append(error, save);
  form.onsubmit = async event => { event.preventDefault(); save.disabled = true; error.textContent = ''; try { await submit(); } catch (e) { error.textContent = e.message; } finally { save.disabled = false; } };
  return form;
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
  }, [...(setup ? [token.node, display.node] : []), username.node, password.node]));
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
function dialog(title) {
  const d = el('dialog', { className: 'account-dialog', attrs: { 'aria-label': title } }, [el('h2', { text: title })]);
  d.onclose = () => d.remove(); document.body.append(d); return d;
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
function assignedRoles(user, roles) { return roles.filter(role => role.user_ids.includes(user.id)); }
function directAccess(user) { return user?.direct_access || { capabilities: [], connections: [], dashboards: [] }; }
function accessEditor(access, resources, { selectedConnection = null } = {}) {
  const current = access || { capabilities: [], connections: [], dashboards: [] };
  const capabilities = ACCESS_CAPABILITIES.map(([id, label]) => ({ id, ...check(label, current.capabilities.includes(id)) }));
  const managed = resources.connections.filter(connection => connection.ownership === 'schemii');
  const profiles = managed.map(connection => {
    const grant = current.connections.find(item => item.connection_id === connection.id && item.owner_id === connection.owner_id);
    const preselected = !access && selectedConnection?.id === connection.id;
    const enabled = check(`Schemii-owned read-only · ${connection.name || connection.id} · ${connection.database} · ${connection.username}`,
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
  return `${app} · ${profile?.name || profile?.database || scope.connectionId}`;
}
function aiGrantBody(grant) {
  return { ...(grant.userId ? { userId: grant.userId } : { roleId: grant.roleId }),
    product: grant.product, connectionOwnerId: grant.connectionOwnerId, connectionId: grant.connectionId,
    ...(grant.modelId ? { modelId: grant.modelId, reasoningEffort: grant.reasoningEffort } : {}) };
}
const connectionIdentity = connection => ({
  connectionOwnerId: connection.connectionOwnerId ?? connection.owner_id,
  connectionId: connection.connectionId ?? connection.id,
});
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
  panel.append(connectionStatus, keyActions);
  appendAiGrants(panel, zen, users, roles, { name: 'Zen', path: 'zen', resources });
  return panel;
}
function appendAiGrants(panel, provider, users, roles, { name, path, resources }) {
  panel.append(el('h3', { text: 'Direct access grants' }));
  const verifiedCatalog = path !== 'shared-codex' || Boolean(provider.catalogCheckedAt);
  const selectableModels = path === 'shared-codex'
    ? (verifiedCatalog ? provider.verifiedModels || [] : provider.models || []) : [];
  const userById = new Map(users.map(user => [user.id, user]));
  const visibleConnections = provider.connections || [];
  const connectionByGrant = grant => visibleConnections.find(connection => {
    const identity = connectionIdentity(connection);
    return connection.userId === grant.userId && connection.product === grant.product
      && identity.connectionOwnerId === grant.connectionOwnerId && identity.connectionId === grant.connectionId;
  });
  const grantList = el('div', { className: 'account-list' });
  const grants = provider.grants || [];
  if (!grants.length) grantList.append(el('p', { text: `No direct ${name} grants yet. Roles may grant access separately.` }));
  for (const grant of grants) {
    const user = userById.get(grant.userId);
    const product = AI_PRODUCTS.find(([id]) => id === grant.product)?.[1] || grant.product;
    const connection = connectionByGrant(grant);
    const database = grant.connectionId === null ? 'No database · Schemii workspace' : connection
      ? `${connection.name || connection.connectionId || connection.id} · ${connection.database}`
      : `${grant.connectionOwnerId}/${grant.connectionId}`;
    const model = provider.models?.find(item => item.id === grant.modelId);
    const policy = path === 'shared-codex' ? ` · ${model?.name || grant.modelId || 'GPT-6 Luna'} · ${reasoningLabel(grant.reasoningEffort || 'default')} reasoning` : '';
    const unverified = path === 'shared-codex' && verifiedCatalog && !selectableModels.some(item => item.id === grant.modelId);
    grantList.append(el('div', { className: 'account-row' }, [
      el('div', {}, [el('strong', { text: `${user?.display_name || user?.username || grant.userId} · ${product}` }), el('small', { text: `${database}${policy}` }),
        ...(unverified ? [el('small', { className: 'account-error', text: 'This model is no longer verified for the installation connection. Choose an available model or revoke this grant.' })] : [])]),
      ...(path === 'shared-codex' ? [button('Change model and reasoning', () => openGrantEditor(grant, null, 'replace')),
        button('Add another model', () => openGrantEditor(grant, null, 'add')),
        button('Remove model', () => confirmAction({
          title: 'Remove shared Codex model?', message: `Remove ${grant.modelId} with ${reasoningLabel(grant.reasoningEffort)} reasoning for ${user?.display_name || user?.username || grant.userId}?`,
          details: 'Other model and reasoning policies on this app and database remain.', confirmLabel: 'Remove model',
          onConfirm: async () => { await requestJson(`${ADMIN}/ai/shared-codex/model-grants`, { method: 'DELETE', body: aiGrantBody(grant) }); await adminPage(); },
        }))] : []),
      button('Revoke', () => confirmAction({
        title: `Revoke ${name} access?`,
        message: `Remove ${product} ${name} access for ${user?.display_name || user?.username || grant.userId} on ${database}?`,
        details: path === 'shared-codex' ? 'All direct Codex models on this exact app and database will be removed. App and database permissions remain.'
          : 'An in-progress turn may stop. The person’s application and database permissions are unaffected.',
        confirmLabel: 'Revoke access',
        onConfirm: async () => { await requestJson(`${ADMIN}/ai/${path}/grants`, { method: 'DELETE', body: aiGrantBody(grant) }); await adminPage(); },
      })),
    ]));
  }
  function openGrantEditor(existingGrant = null, fixedUserId = null, mode = 'replace') {
    const d = dialog(existingGrant ? 'Change shared Codex policy' : `Grant ${name} access`);
    const userSelect = el('select', { attrs: { 'aria-label': 'Person' } });
    for (const user of users.filter(item => !item.disabled)) userSelect.append(el('option', { text: `${user.display_name || user.username} (${user.username})`, attrs: { value: user.id } }));
    const productSelect = el('select', { attrs: { 'aria-label': 'Application' } });
    const databaseSelect = el('select', { attrs: { 'aria-label': 'Database profile' } });
    const scopeHelp = el('p', { attrs: { role: 'status' } });
    const modelSelect = el('select', { attrs: { 'aria-label': 'Allowed model' } });
    const reasoningSelect = el('select', { attrs: { 'aria-label': 'Reasoning level' } });
    if (path === 'shared-codex') {
      if (existingGrant?.modelId && !selectableModels.some(item => item.id === existingGrant.modelId)) {
        modelSelect.append(el('option', { text: `${existingGrant.modelId} · unavailable — choose another model`, attrs: { value: existingGrant.modelId, disabled: '' } }));
      }
      for (const model of selectableModels) modelSelect.append(el('option', { text: model.name || model.id, attrs: { value: model.id } }));
      modelSelect.value = existingGrant?.modelId || (selectableModels.some(model => model.id === 'gpt-6-luna') ? 'gpt-6-luna' : selectableModels[0]?.id || '');
      const updateReasoning = preferred => {
        const model = selectableModels.find(item => item.id === modelSelect.value);
        const levels = reasoningLevels(model);
        reasoningSelect.replaceChildren(...levels.map(level => el('option', { text: reasoningLabel(level), attrs: { value: level } })));
        reasoningSelect.value = levels.includes(preferred) ? preferred : 'default';
      };
      modelSelect.onchange = () => updateReasoning('default');
      updateReasoning(existingGrant?.reasoningEffort || 'default');
    }
    const options = [];
    function updateProducts() {
      const selected = userById.get(userSelect.value), previous = productSelect.value;
      productSelect.replaceChildren();
      const capabilities = new Set(selected?.effective_capabilities || [
        ...directAccess(selected).capabilities,
        ...roles.filter(role => role.user_ids.includes(selected?.id)).flatMap(role => role.capabilities),
      ]);
      for (const [id, name] of AI_PRODUCTS) {
        if (capabilities.has(`${id}:access`)) {
          productSelect.append(el('option', { text: name, attrs: { value: id } }));
        }
      }
      if ([...productSelect.options].some(option => option.value === previous)) productSelect.value = previous;
      updateConnections();
    }
    function updateConnections() {
      databaseSelect.replaceChildren(); options.length = 0;
      if (productSelect.value === 'schemii') {
        options.push({ connectionOwnerId: null, connectionId: null });
        databaseSelect.append(el('option', { text: 'No database · detached Schemii workspace', attrs: { value: '0' } }));
      }
      const selected = visibleConnections.filter(connection => connection.userId === userSelect.value && connection.product === productSelect.value);
      for (const connection of selected) {
        const identity = connectionIdentity(connection);
        if (options.some(item => item.connectionOwnerId === identity.connectionOwnerId && item.connectionId === identity.connectionId)) continue;
        options.push(identity);
        const ownership = connection.ownership === 'schemii' ? 'Schemii-owned' : 'Personal';
        databaseSelect.append(el('option', { text: `${connection.name || identity.connectionId} · ${connection.database} · ${ownership}`, attrs: { value: String(options.length - 1) } }));
      }
      databaseSelect.disabled = !options.length;
      scopeHelp.textContent = options.length ? 'A grant applies only to the selected database identity. App and database permissions are checked again for each AI turn.' : productSelect.value ? 'This person has no database profile available for this app.' : 'This person does not have access to an AI app yet.';
    }
    userSelect.onchange = updateProducts; productSelect.onchange = updateConnections;
    if (existingGrant || fixedUserId) userSelect.value = existingGrant?.userId || fixedUserId;
    updateProducts();
    if (existingGrant) {
      productSelect.value = existingGrant.product; updateConnections();
      const selectedIndex = options.findIndex(scope => scope.connectionOwnerId === existingGrant.connectionOwnerId && scope.connectionId === existingGrant.connectionId);
      if (selectedIndex >= 0) databaseSelect.value = String(selectedIndex);
      userSelect.disabled = true; productSelect.disabled = true; databaseSelect.disabled = true;
    }
    if (fixedUserId) userSelect.disabled = true;
    const policyFields = path === 'shared-codex' ? [
      el('label', { className: 'account-field' }, ['Allowed model', modelSelect]),
      el('label', { className: 'account-field' }, ['Reasoning level', reasoningSelect]),
      el('p', { text: 'The selected model and reasoning level are enforced for this person, app, and database profile.' }),
      ...(!verifiedCatalog ? [el('p', { className: 'account-error', text: 'Test the shared Codex connection first. The listed catalog models have not been verified for this account.' })] : []),
      ...(verifiedCatalog && !selectableModels.length ? [el('p', { className: 'account-error', text: 'No Codex models were verified for this account. Reconnect or test again before granting access.' })] : []),
    ] : [];
    const grantForm = formWithSubmit(existingGrant ? mode === 'add' ? 'Add model' : 'Save policy' : 'Add grant', async () => {
      const scope = options[Number(databaseSelect.value)];
      if (!scope) throw new Error('Choose an available database profile.');
      const body = { userId: userSelect.value, product: productSelect.value, ...scope,
        ...(path === 'shared-codex' ? { modelId: modelSelect.value, reasoningEffort: reasoningSelect.value } : {}) };
      if (path === 'shared-codex' && (!verifiedCatalog || !selectableModels.some(item => item.id === body.modelId))) throw new Error('Test the Codex connection and choose a verified model before saving this grant.');
      const sameScope = grants.some(grant => grant.userId === body.userId && grant.product === body.product
        && grant.connectionOwnerId === body.connectionOwnerId && grant.connectionId === body.connectionId);
      if (!(existingGrant && mode === 'replace') && grants.some(grant => grant.userId === body.userId && grant.product === body.product
        && grant.connectionOwnerId === body.connectionOwnerId && grant.connectionId === body.connectionId
        && (path !== 'shared-codex' || (grant.modelId === body.modelId && grant.reasoningEffort === body.reasoningEffort)))) {
        throw new Error(path === 'shared-codex' ? 'This model and reasoning level are already granted for this scope.' : 'This access grant already exists.');
      }
      await requestJson(`${ADMIN}/ai/${path}/grants`, { method: path === 'shared-codex' && sameScope && mode === 'add' ? 'POST' : 'PUT', body });
      d.close(); await adminPage();
    }, [el('label', { className: 'account-field' }, ['Person', userSelect]), el('label', { className: 'account-field' }, ['Application', productSelect]), el('label', { className: 'account-field' }, ['Database profile', databaseSelect]), scopeHelp, ...policyFields]);
    if (path === 'shared-codex') {
      const save = grantForm.querySelector('button[type="submit"]');
      const syncSubmit = () => { save.disabled = !verifiedCatalog || !selectableModels.some(item => item.id === modelSelect.value); };
      modelSelect.addEventListener('change', syncSubmit); syncSubmit();
    }
    d.append(grantForm);
    d.append(button('Cancel', () => d.close())); d.showModal();
  }
  panel.openGrantEditorForUser = userId => openGrantEditor(null, userId);
  panel.openGrantEditor = (grant, mode = 'replace') => openGrantEditor(grant, null, mode);
  panel.append(button(`Grant ${name} access`, () => openGrantEditor(), true), grantList);
  panel.append(el('h3', { text: 'Role AI policies' }));
  const roleList = el('div', { className: 'account-list' });
  const roleGrants = provider.roleGrants || [];
  if (!roleGrants.length) roleList.append(el('p', { text: `No roles grant ${name} access yet. Edit a role to add a policy.` }));
  for (const grant of roleGrants) {
    const role = roles.find(item => item.id === grant.roleId);
    const policy = grant.modelId ? ` · ${grant.modelId} · ${reasoningLabel(grant.reasoningEffort)} reasoning` : '';
    roleList.append(el('div', { className: 'account-row' }, [
      el('div', {}, [el('strong', { text: role?.name || grant.roleId }),
        el('small', { text: `${aiScopeLabel(grant, resources)}${policy}` }),
        ...(grant.active === false ? [el('small', { className: 'account-error', text: grant.issue || 'This policy is inactive because its role no longer grants this app and database.' })] : [])]),
      button(grant.modelId ? 'Remove model' : 'Revoke', () => confirmAction({
        title: `Revoke ${name} role access?`, message: `Remove this exact ${name} policy from “${role?.name || grant.roleId}”?`,
        details: 'Other models and role permissions remain.', confirmLabel: grant.modelId ? 'Remove model' : 'Revoke access',
        onConfirm: async () => { await requestJson(`${ADMIN}/ai/${path}/role-grants`, { method: 'DELETE', body: aiGrantBody(grant) }); await adminPage(); },
      })),
    ]));
  }
  panel.append(roleList);
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
  appendAiGrants(panel, shared, users, roles, { name: 'shared Codex', path: 'shared-codex', resources });
  return panel;
}
async function adminPage() {
  document.title = 'Administration · Schemii';
  main.replaceChildren(...heading('Administration', 'Assign application access and Schemii-owned read-only database accounts through roles. PostgreSQL controls visible rows, columns, and write operations.'));
  const [users, roles, resources, managedResult, zenResult, sharedCodexResult] = await Promise.all([
    requestJson(`${ADMIN}/accounts`), requestJson(`${ADMIN}/roles`), requestJson(`${ADMIN}/resources`),
    requestJson(`${ADMIN}/schemii-connections`).then(value => ({ connections: value.connections })).catch(error => ({ error })),
    requestJson(`${ADMIN}/ai/zen`).catch(error => ({ error })),
    requestJson(`${ADMIN}/ai/shared-codex`).catch(error => ({ error })),
  ]);
  const managedConnections = managedResult.connections || [];
  const columns = el('div', { className: 'account-columns' });
  const people = el('section', { className: 'account-panel', attrs: { 'aria-labelledby': 'admin-people-title' } }, [el('h2', { text: 'People', attrs: { id: 'admin-people-title' } }), button('Add user', () => editUser(null), true)]);
  const rolePanel = el('section', { className: 'account-panel', attrs: { 'aria-labelledby': 'admin-roles-title' } }, [el('h2', { text: 'Roles', attrs: { id: 'admin-roles-title' } }), button('Create role', () => editRole(null), true)]);
  const userList = el('div', { className: 'account-list' });
  const disabledCount = users.filter(user => user.disabled).length;
  people.append(el('p', { text: `${users.length - disabledCount} active · ${disabledCount} disabled` }));
  const disabledRows = [];
  if (disabledCount) people.append(button(`Show ${disabledCount} disabled ${disabledCount === 1 ? 'user' : 'users'}`, event => {
    const show = disabledRows.some(row => row.hidden);
    for (const row of disabledRows) row.hidden = !show;
    event.currentTarget.textContent = show ? `Hide ${disabledCount} disabled ${disabledCount === 1 ? 'user' : 'users'}`
      : `Show ${disabledCount} disabled ${disabledCount === 1 ? 'user' : 'users'}`;
  }));
  for (const user of [...users].sort((a, b) => Number(a.disabled) - Number(b.disabled)
    || (a.display_name || a.username).localeCompare(b.display_name || b.username))) {
    const memberships = assignedRoles(user, roles);
    const direct = directAccess(user);
    const apps = [...new Set([...direct.capabilities, ...memberships.flatMap(role => role.capabilities)]
      .map(capability => ROLE_CAPABILITY_LABELS[capability] || capability))];
    const databases = new Set([...direct.connections, ...memberships.flatMap(role => role.connections)]
      .map(grant => `${grant.owner_id}/${grant.connection_id}`));
    const aiCount = [zenResult, sharedCodexResult].reduce((count, provider) => count
      + (provider.grants || []).filter(grant => grant.userId === user.id).length
      + (provider.roleGrants || []).filter(grant => memberships.some(role => role.id === grant.roleId)).length, 0);
    const row = el('div', { className: 'account-row', hidden: user.disabled }, [el('div', {}, [
      el('strong', { text: user.display_name || user.username }),
      el('small', { text: `${user.username} · ${user.disabled ? 'Disabled' : user.is_admin ? 'Administrator' : 'Active'}` }),
      el('small', { text: `Roles: ${memberships.map(role => role.name).join(', ') || 'none'} · Apps: ${apps.join(', ') || 'none'} · Managed databases: ${databases.size} · AI policies: ${aiCount}` }),
    ]), button(`Edit ${user.username}`, () => editUser(user))]);
    userList.append(row);
    if (user.disabled) disabledRows.push(row);
  }
  if (!users.length) userList.append(el('p', { text: 'No users yet. Add a user and assign roles to give them access.' }));
  people.append(userList);
  const roleList = el('div', { className: 'account-list' });
  for (const role of roles) roleList.append(el('div', { className: 'account-row' }, [el('div', {}, [el('strong', { text: role.name }), el('small', { text: `${role.user_ids.length} members · ${role.connections.length} connections · ${role.dashboards.length} dashboards` })]), button('Edit', () => editRole(role))]));
  if (!roles.length) roleList.append(el('p', { text: 'Create a role to give people access to shared reports.' }));
  rolePanel.append(roleList); columns.append(people, rolePanel);
  const connectionPanel = el('section', { className: 'account-panel', attrs: { 'aria-labelledby': 'schemii-connections-title' } }, [
    el('h2', { text: 'Schemii-owned read-only database accounts', attrs: { id: 'schemii-connections-title' } }),
    el('p', { text: 'Create and verify read-only PostgreSQL logins with the required row policies in each database first. Save their credentials here, then assign the accounts to roles. A connection test checks connectivity, not read-only privileges. People can use these accounts without seeing the passwords; their personal connections remain separate.' }),
  ]);
  const addManaged = button('Add Schemii-owned account', () => editManagedConnection(null), true);
  if (managedResult.error) {
    addManaged.disabled = true;
    connectionPanel.append(el('p', { className: 'account-error', text: `Schemii-owned accounts could not be loaded: ${managedResult.error.message}`, attrs: { role: 'alert' } }), button('Retry loading accounts', adminPage));
  } else {
    const connectionList = el('div', { className: 'account-list' });
    if (!managedConnections.length) connectionList.append(el('p', { text: 'No Schemii-owned accounts yet. Add a dedicated read-only PostgreSQL login to offer managed access.' }));
    for (const connection of managedConnections) {
      const connectionOwner = connection.ownerId || connection.owner_id || 'user_schemii_connection_pool';
      const includesConnection = grant => grant.connection_id === connection.id && grant.owner_id === connectionOwner;
      const scopedRoles = roles.filter(role => role.connections.some(includesConnection));
      const directUsers = users.filter(user => directAccess(user).connections.some(includesConnection));
      const status = el('p', { className: 'account-inline-status', attrs: { role: 'status' } });
      const actions = el('div', { className: 'account-actions' }, [
        button('Test connection', async event => {
          const trigger = event.currentTarget; trigger.disabled = true; status.textContent = 'Testing saved database identity…';
          try {
            const result = await requestJson(`${ADMIN}/schemii-connections/${encodeURIComponent(connection.id)}/test`, { method: 'POST' });
            status.textContent = `Connected to ${result.database} as the saved PostgreSQL account.`;
          } catch (error) { status.textContent = `Connection test failed: ${error.message}`; }
          finally { trigger.disabled = false; }
        }),
        button('Manage access', () => showDatabaseAccess(connection, scopedRoles, directUsers)),
        button('Edit', () => editManagedConnection(connection)),
        button('Delete', () => confirmAction({
          title: 'Delete Schemii-owned account?',
          message: `Delete “${connection.name}” from Schemii?`,
          details: 'Any roles or saved work depending on this account must be removed first. The PostgreSQL login itself is not deleted.',
          confirmLabel: 'Delete account',
          onConfirm: async () => {
            await requestJson(`${ADMIN}/schemii-connections/${encodeURIComponent(connection.id)}?expectedRevision=${connection.revision}`, { method: 'DELETE' });
            await adminPage();
          },
        })),
      ]);
      connectionList.append(el('div', { className: 'account-row' }, [
        el('div', {}, [
          el('strong', { text: connection.name }),
          el('small', { text: `Schemii-owned · ${connection.username}@${connection.host}:${connection.port}/${connection.database} · credential ${connection.credentialStored ? 'stored' : 'missing'}` }),
          el('small', { text: `${scopedRoles.length} ${scopedRoles.length === 1 ? 'role' : 'roles'} · ${directUsers.length} direct ${directUsers.length === 1 ? 'user' : 'users'}` }),
          status,
        ]), actions,
      ]));
    }
    connectionPanel.append(addManaged, connectionList);
  }
  const diagnostics = el('section', { className: 'account-panel', attrs: { id: 'system-diagnostics', 'aria-labelledby': 'system-diagnostics-title' } }, [
    el('h2', { text: 'System diagnostics', attrs: { id: 'system-diagnostics-title' } }),
    el('p', { text: 'Inspect the application topology and API/database paths. These pages are available only to application provisioners.' }),
  ]);
  const diagnosticList = el('div', { className: 'account-list' });
  for (const [title, action, description, path] of [
    ['Live system map', 'Open live system map', 'Follow request journeys through the application.', '/system-map'],
    ['API lens', 'Open API lens', 'Inspect routes and request/response contracts.', '/api-map'],
    ['Database lens', 'Open database lens', 'Trace application calls into database operations.', '/db-map'],
  ]) {
    diagnosticList.append(el('div', { className: 'account-row' }, [
      el('div', {}, [el('strong', { text: title }), el('small', { text: description })]),
      link(action, path),
    ]));
  }
  diagnostics.append(diagnosticList);
  const sharedPanel = sharedCodexAdministration(sharedCodexResult, users, roles, resources);
  const zenPanel = zenAdministration(zenResult, users, roles, resources);
  main.append(columns, connectionPanel, sharedPanel, zenPanel, link('Jump to system diagnostics', '#system-diagnostics'), diagnostics);
  function showDatabaseAccess(connection, scopedRoles, directUsers) {
    const d = dialog(`Access to ${connection.name}`);
    const connectionOwner = connection.ownerId || connection.owner_id || 'user_schemii_connection_pool';
    d.append(el('p', { text: `This installation-owned PostgreSQL account connects to ${connection.database}. App permissions, role membership, and the database’s own grants all apply.` }));
    d.append(el('h3', { text: 'Roles with this database' }));
    const roleList = el('div', { className: 'account-list' });
    if (!scopedRoles.length) roleList.append(el('p', { text: 'No roles use this database yet.' }));
    for (const role of scopedRoles) roleList.append(el('div', { className: 'account-row' }, [
      el('div', {}, [el('strong', { text: role.name }), el('small', { text: `${role.user_ids.length} members · Apps: ${role.capabilities.map(capability => ROLE_CAPABILITY_LABELS[capability] || capability).join(', ') || 'none'}` })]),
      button(`Edit role ${role.name}`, () => { d.close(); editRole(role); }),
    ]));
    d.append(roleList, button('Create role for this database', () => { d.close(); editRole(null, connection); }, true));
    d.append(el('h3', { text: 'People with direct access' }));
    const peopleList = el('div', { className: 'account-list' });
    if (!directUsers.length) peopleList.append(el('p', { text: 'No one has direct access to this database.' }));
    for (const user of directUsers) peopleList.append(el('div', { className: 'account-row' }, [
      el('div', {}, [el('strong', { text: user.display_name || user.username }),
        el('small', { text: `Apps: ${directAccess(user).capabilities.map(capability => ROLE_CAPABILITY_LABELS[capability] || capability).join(', ') || 'none'}` })]),
      button(`Edit ${user.username}`, () => { d.close(); editUser(user); }),
    ]));
    d.append(peopleList, el('h3', { text: 'AI access on this database' }));
    const aiList = el('div', { className: 'account-list' });
    for (const [provider, path, state] of [
      ['Shared Codex', 'shared-codex', sharedCodexResult], ['Zen', 'zen', zenResult],
    ]) for (const [kind, grants] of [['role', state.roleGrants || []], ['user', state.grants || []]]) {
      for (const grant of grants) {
        if (grant.connectionOwnerId !== connectionOwner || grant.connectionId !== connection.id) continue;
        const subject = kind === 'role' ? roles.find(role => role.id === grant.roleId)?.name || grant.roleId
          : users.find(user => user.id === grant.userId)?.display_name || grant.userId;
        const model = grant.modelId ? ` · ${grant.modelId} · ${reasoningLabel(grant.reasoningEffort)} reasoning` : '';
        aiList.append(el('div', { className: 'account-row' }, [
          el('div', {}, [el('strong', { text: `${subject} · ${provider}` }),
            el('small', { text: `${AI_PRODUCTS.find(([id]) => id === grant.product)?.[1] || grant.product}${model} · ${kind === 'role' ? 'role' : 'direct'}` }),
            ...(grant.active === false ? [el('small', { className: 'account-error', text: grant.issue || 'This AI policy is inactive because the role no longer has this app and database scope.' })] : [])]),
          button(grant.modelId ? 'Remove model' : 'Revoke', () => confirmAction({
            title: 'Revoke AI access?', message: `Remove this exact ${provider} policy for ${subject} on ${connection.name}?`,
            details: 'Other AI policies, app access, and database permissions remain.',
            confirmLabel: grant.modelId ? 'Remove model' : 'Revoke access',
            onConfirm: async () => {
              const route = kind === 'role' ? 'role-grants' : grant.modelId ? 'model-grants' : 'grants';
              await requestJson(`${ADMIN}/ai/${path}/${route}`, { method: 'DELETE', body: aiGrantBody(grant) });
              d.close(); await adminPage();
            },
          })),
        ]));
      }
    }
    if (!aiList.children.length) aiList.append(el('p', { text: 'No AI policies are scoped to this database.' }));
    d.append(aiList, button('Close', () => d.close())); d.showModal();
  }
  function editManagedConnection(connection) {
    const d = dialog(connection ? 'Edit Schemii-owned account' : 'Add Schemii-owned account');
    const name = field('Account name', { value: connection?.name || '' });
    const host = field('PostgreSQL host', { value: connection?.host || '' });
    const port = field('Port', { value: connection?.port ?? 5432, type: 'number' });
    port.input.min = '1'; port.input.max = '65535'; port.input.step = '1';
    const database = field('Database', { value: connection?.database || '' });
    const username = field('PostgreSQL username', { value: connection?.username || '' });
    const password = field(connection ? 'Replace password (leave blank to keep)' : 'PostgreSQL password', { type: 'password', autocomplete: 'new-password', required: !connection });
    password.input.removeAttribute('minlength'); password.input.maxLength = 4096;
    const sslMode = el('select', { attrs: { 'aria-label': 'SSL mode' } });
    for (const [value, label] of [['verify-full', 'Verify full'], ['verify-ca', 'Verify CA'], ['require', 'Require'], ['prefer', 'Prefer'], ['allow', 'Allow'], ['disable', 'Disable']]) sslMode.append(el('option', { text: label, attrs: { value } }));
    sslMode.value = connection?.sslMode || 'verify-full';
    const timeout = field('Connect timeout (seconds)', { value: connection?.connectTimeout ?? 10, type: 'number' });
    timeout.input.min = '1'; timeout.input.max = '30'; timeout.input.step = '1';
    d.append(formWithSubmit(connection ? 'Save account' : 'Add account', async () => {
      const values = { name: name.input.value.trim(), host: host.input.value.trim(), port: Number(port.input.value), database: database.input.value.trim(), username: username.input.value.trim(), sslMode: sslMode.value, connectTimeout: Number(timeout.input.value) };
      const body = connection ? { expectedRevision: connection.revision } : values;
      if (connection) for (const [key, value] of Object.entries(values)) if (value !== connection[key]) body[key] = value;
      if (password.input.value) body.password = password.input.value;
      if (connection && Object.keys(body).length === 1) throw new Error('No account details changed.');
      await requestJson(`${ADMIN}/schemii-connections${connection ? `/${encodeURIComponent(connection.id)}` : ''}`, { method: connection ? 'PATCH' : 'POST', body });
      password.input.value = '';
      d.close(); await adminPage();
    }, [
      el('p', { text: connection ? 'This is a Schemii-owned account, not a personal connection. The password is never displayed; leave it blank to retain the saved credential.' : 'Use a dedicated read-only login already created in PostgreSQL. Its grants and RLS policies determine what members can see.' }),
      name.node, host.node, port.node, database.node, username.node, password.node,
      el('label', { className: 'account-field' }, ['SSL mode', sslMode]), timeout.node,
    ]));
    d.append(button('Cancel', () => d.close())); d.showModal();
  }
  function editUser(user) {
    const d = dialog(user ? 'Edit user' : 'Add user');
    const aiAction = (label, action) => {
      const control = button(label, action); control.dataset.aiAction = ''; return control;
    };
    const username = field('Username', { value: user?.username || '', autocomplete: 'off' });
    const display = field('Display name', { value: user?.display_name || '', autocomplete: 'off' });
    const password = field(user ? 'Reset password (leave blank to keep)' : 'Initial password', { type: 'password', autocomplete: 'new-password', required: !user });
    const admin = check('Can provision accounts and roles', user?.is_admin), disabled = check('Disable sign-in and revoke sessions', user?.disabled);
    const memberships = roles.map(role => ({ role, ...check(role.name, !!user && role.user_ids.includes(user.id)) }));
    const direct = accessEditor(directAccess(user), resources);
    const roleChoices = el('div', { className: 'account-list' }, memberships.length
      ? memberships.map(({ role, node }) => el('div', { className: 'account-grant' }, [node, el('small', { text: roleAccess(role, resources, zenResult, sharedCodexResult) })]))
      : [el('p', { text: 'No roles yet. Create a role in Roles, then return here to assign it.' })]);
    const aiRows = [];
    if (user) for (const [provider, panel, state] of [['Shared Codex', sharedPanel, sharedCodexResult], ['OpenCode Zen', zenPanel, zenResult]]) {
      for (const grant of state.grants || []) {
        if (grant.userId !== user.id) continue;
        const product = AI_PRODUCTS.find(([id]) => id === grant.product)?.[1] || grant.product;
        const connection = (state.connections || []).find(item => item.userId === user.id && item.product === grant.product
          && connectionIdentity(item).connectionOwnerId === grant.connectionOwnerId && connectionIdentity(item).connectionId === grant.connectionId);
        const scope = grant.connectionId === null ? 'No database · Schemii workspace' : connection?.name || grant.connectionId;
        aiRows.push(el('div', { className: 'account-row' }, [el('div', {}, [
          el('strong', { text: `${provider} · ${product}` }),
          el('small', { text: `${scope}${grant.modelId ? ` · ${grant.modelId} · ${reasoningLabel(grant.reasoningEffort || 'default')} reasoning` : ''}` }),
        ]), ...(grant.modelId && panel.openGrantEditor ? [aiAction('Add another model', () => { d.close(); panel.openGrantEditor(grant, 'add'); })] : []),
        aiAction(grant.modelId ? 'Remove model' : 'Revoke AI access', () => confirmAction({
          title: 'Revoke AI access?', message: `Remove ${provider} access for ${user.username} in ${product} on ${scope}?`,
          details: grant.modelId ? 'Only this model and reasoning pair will be removed.' : 'The selected provider grant will be removed.',
          confirmLabel: grant.modelId ? 'Remove model' : 'Revoke access',
          onConfirm: async () => {
            await requestJson(`${ADMIN}/ai/${grant.modelId ? 'shared-codex/model-grants' : 'zen/grants'}`, { method: 'DELETE', body: aiGrantBody(grant) });
            d.close(); await adminPage();
          },
        }))]));
      }
    }
    const userForm = formWithSubmit(user ? 'Save user' : 'Create user', async () => {
      const body = { display_name: display.input.value.trim(), is_admin: admin.input.checked,
        role_ids: memberships.filter(membership => membership.input.checked).map(membership => membership.role.id),
        direct_access: direct.values(),
        ...(password.input.value ? { password: password.input.value } : {}),
        ...(user ? { disabled: disabled.input.checked } : { username: username.input.value.trim() }) };
      await requestJson(`${ADMIN}/accounts${user ? `/${user.id}` : ''}`, { method: user ? 'PATCH' : 'POST', body }); d.close(); await adminPage();
    }, [...(!user ? [username.node] : []), display.node, password.node, admin.node, ...(user ? [disabled.node] : []),
      el('h3', { text: 'Access through roles' }),
      el('p', { text: 'Select the roles this person should have. Each role’s apps, managed database accounts, and shared dashboards are shown below. Edit those permissions in Roles.' }),
      roleChoices,
      el('h3', { text: 'Direct application access' }),
      el('p', { text: 'Grant an app here for this person only. App access does not require a managed database account. Schemer editing also requires Schemer viewing in this direct access set.' }),
      el('div', { className: 'account-list' }, direct.capabilities.map(item => item.node)),
      el('h3', { text: 'Direct managed database access' }),
      el('p', { text: 'Choose an installation-owned PostgreSQL identity for this person. Enable tool use only when the matching direct app permission is selected. PostgreSQL controls rows, columns, and writes.' }),
      el('div', { className: 'account-list' }, direct.profiles.length ? direct.profiles.map(item => item.node)
        : [el('p', { text: 'No Schemii-owned database accounts are available yet.' })]),
      el('h3', { text: 'Direct shared dashboards' }),
      el('p', { text: 'These report grants belong only to this person and require direct Schemer viewing and a selected direct database account.' }),
      el('div', { className: 'account-list' }, direct.dashboards.length ? direct.dashboards.map(item => item.node)
        : [el('p', { text: 'No dashboards are available yet.' })]),
      ...(user ? [el('h3', { text: 'Direct AI access' }),
        el('p', { text: 'These grants are scoped to one app and database profile. Save changes to this user before editing AI access. AI access inherited through roles is managed in Roles; credentials remain installation-owned.' }),
        el('div', { className: 'account-list' }, aiRows.length ? aiRows : [el('p', { text: 'No direct AI grants for this person.' })])]
        : []),
      el('p', { text: 'Share initial or reset passwords through a secure channel. Users can change their password from Account.' })]);
    d.append(userForm);
    const actions = el('div', { className: 'account-actions' }, [button('Cancel', () => d.close())]);
    if (user && !user.disabled) {
      for (const [name, panel] of [['Shared Codex', sharedPanel], ['Zen', zenPanel]]) {
        if (panel.openGrantEditorForUser) actions.append(aiAction(`Grant ${name} to this user`, () => { d.close(); panel.openGrantEditorForUser(user.id); }));
      }
    }
    if (user) {
      const pending = el('p', { attrs: { role: 'status' } });
      const disableAiActions = () => {
        for (const control of d.querySelectorAll('[data-ai-action]')) control.disabled = true;
        pending.textContent = 'Save user changes before editing AI access.';
      };
      userForm.addEventListener('input', disableAiActions);
      userForm.addEventListener('change', disableAiActions);
      actions.append(pending);
    }
    if (user) actions.append(button('Remove account', async () => {
      await confirmAction({
        title: 'Remove user account?',
        message: `Remove “${user.display_name || user.username}” (${user.username})?`,
        details: 'Their sign-in, sessions, role memberships, and access grants will be removed. Dashboards and other work they own remain.',
        confirmLabel: 'Remove account',
        onConfirm: async () => {
          await requestJson(`${ADMIN}/accounts/${encodeURIComponent(user.id)}`, { method: 'DELETE' });
          d.close(); await adminPage();
        },
      });
    }));
    d.append(actions); d.showModal();
  }
  function editRole(role, selectedConnection = null) {
    const d = dialog(role ? 'Edit role' : 'Create role');
    const name = field('Role name', { value: role?.name || '' });
    const access = accessEditor(role, resources, { selectedConnection });
    const memberships = users.map(user => ({ id: user.id, ...check(`${user.display_name || user.username} (${user.username})`, role?.user_ids.includes(user.id)) }));
    const legacyConnections = (role?.connections || []).filter(grant => grant.owner_id !== 'user_schemii_connection_pool');
    const sections = [name.node, el('h3', { text: 'Applications' }), el('div', { className: 'account-list' }, access.capabilities.map(item => item.node)), el('p', { text: 'Schemer editing also requires Schemer access. Database operations use the PostgreSQL identity on an authorized connection.' }), el('h3', { text: 'Members' }), el('div', { className: 'account-list' }, memberships.map(item => item.node)), el('h3', { text: 'Schemii-owned read-only accounts' }), el('p', { text: 'Choose which read-only PostgreSQL identity this role may use. Credentials stay on the server; PostgreSQL grants and row policies apply to every query.' }), el('div', { className: 'account-list' }, access.profiles.length ? access.profiles.map(item => item.node) : [el('p', { text: 'No Schemii-owned accounts are available yet. Add one in Administration first.' })]), ...(legacyConnections.length ? [el('h3', { text: 'Inactive legacy user-owned grants' }), el('p', { text: 'Personal credentials can no longer be shared through roles. Create a Schemii-owned read-only account for each needed row-policy identity, select it above, and rebind affected dashboards. Saving this role removes these inactive grants.' }), el('div', { className: 'account-list' }, legacyConnections.map(grant => el('p', { text: `${grant.connection_id} · owned by ${grant.owner_id} · user-owned, not assignable` })))] : []), el('h3', { text: 'Shared dashboards' }), el('p', { text: 'Bind each dashboard to one of the connections selected above. The database applies that connection’s row and column permissions.' }), el('div', { className: 'account-list' }, access.dashboards.length ? access.dashboards.map(item => item.node) : [el('p', { text: 'Create a dashboard in Schemer before sharing it.' })])];
    const roleForm = formWithSubmit('Save role', async () => {
      await requestJson(`${ADMIN}/roles${role ? `/${role.id}` : ''}`, { method: role ? 'PUT' : 'POST', body: { name: name.input.value.trim(), user_ids: memberships.filter(member => member.input.checked).map(member => member.id), ...access.values() } });
      d.close(); await adminPage();
    }, sections);
    d.append(roleForm);
    const aiSection = el('section', { attrs: { 'aria-label': 'Role AI access' } }, [
      el('h3', { text: 'Role AI access' }),
      el('p', { text: role
        ? 'Grant installation-owned AI access by app and database. A member also needs this role’s app and database permissions. Models and reasoning levels are selected separately for Shared Codex.'
        : 'Save this role first, then add AI access for its app and database scopes.' }),
    ]);
    d.append(aiSection);
    if (role) {
      let roleChanged = false;
      const changedNotice = el('p', { attrs: { role: 'status' } });
      const grantsList = el('div', { className: 'account-list' });
      const buttons = el('div', { className: 'account-actions' });
      const providerStates = [
        { name: 'Shared Codex', path: 'shared-codex', state: sharedCodexResult },
        { name: 'Zen', path: 'zen', state: zenResult },
      ];
      const scopes = roleAiScopes(role).filter(scope => scope.connectionId === null || resources.connections.some(
        connection => connection.id === scope.connectionId && connection.owner_id === scope.connectionOwnerId && connection.ownership === 'schemii'));
      function renderRoleAi() {
        const rows = [];
        for (const { name: providerName, path, state } of providerStates) {
          for (const grant of state.roleGrants || []) {
            if (grant.roleId !== role.id) continue;
            const scope = aiScopeLabel(grant, resources);
            const policy = path === 'shared-codex' ? ` · ${grant.modelId} · ${reasoningLabel(grant.reasoningEffort)} reasoning` : '';
            const stillEligible = grant.active !== false && scopes.some(item => item.product === grant.product
              && item.connectionOwnerId === grant.connectionOwnerId && item.connectionId === grant.connectionId);
            rows.push(el('div', { className: 'account-row' }, [
              el('div', {}, [el('strong', { text: providerName }), el('small', { text: `${scope}${policy}` }),
                ...(!stillEligible ? [el('small', { className: 'account-error', text: grant.issue || 'The role no longer has this app and database scope. This AI policy is inactive.' })] : [])]),
              button(path === 'shared-codex' ? 'Remove model' : 'Revoke', () => confirmAction({
                title: `Revoke ${providerName} role access?`,
                message: `Remove ${providerName} for “${role.name}” on ${scope}${policy}?`,
                details: 'Members will lose this exact AI policy. Their app, database, and other AI access remain.',
                confirmLabel: path === 'shared-codex' ? 'Remove model' : 'Revoke access',
                onConfirm: async () => {
                  await requestJson(`${ADMIN}/ai/${path}/role-grants`, { method: 'DELETE', body: aiGrantBody(grant) });
                  state.roleGrants = (state.roleGrants || []).filter(item => item !== grant);
                  renderRoleAi();
                },
              })),
            ]));
          }
        }
        grantsList.replaceChildren(...(rows.length ? rows : [el('p', { text: 'No role AI access yet.' })]));
        if (roleChanged) for (const control of grantsList.querySelectorAll('button')) control.disabled = true;
      }
      function addRoleAi(path, state) {
        const modelPolicies = path === 'shared-codex';
        const grantDialog = dialog(`Add ${modelPolicies ? 'Shared Codex' : 'Zen'} role access`);
        const scopeSelect = el('select', { attrs: { 'aria-label': 'App and database scope' } });
        scopes.forEach((scope, index) => scopeSelect.append(el('option', { text: aiScopeLabel(scope, resources), attrs: { value: String(index) } })));
        const modelSelect = el('select', { attrs: { 'aria-label': 'Allowed model' } });
        const reasoningSelect = el('select', { attrs: { 'aria-label': 'Reasoning level' } });
        const verified = Boolean(state.catalogCheckedAt), models = verified ? state.verifiedModels || [] : [];
        if (modelPolicies) {
          for (const model of models) modelSelect.append(el('option', { text: model.name || model.id, attrs: { value: model.id } }));
          if (models.some(model => model.id === 'gpt-6-luna')) modelSelect.value = 'gpt-6-luna';
          const updateReasoning = () => {
            const model = models.find(item => item.id === modelSelect.value);
            reasoningSelect.replaceChildren(...reasoningLevels(model).map(level => el('option', { text: reasoningLabel(level), attrs: { value: level } })));
          };
          modelSelect.onchange = updateReasoning; updateReasoning();
        }
        const form = formWithSubmit('Add AI policy', async () => {
          const scope = scopes[Number(scopeSelect.value)];
          if (!scope) throw new Error('Choose an app and database scope already allowed by this role.');
          const body = { roleId: role.id, ...scope,
            ...(modelPolicies ? { modelId: modelSelect.value, reasoningEffort: reasoningSelect.value } : {}) };
          if (modelPolicies && (!verified || !models.some(model => model.id === body.modelId))) {
            throw new Error('Test the shared Codex connection and choose a verified model first.');
          }
          if ((state.roleGrants || []).some(grant => grant.roleId === body.roleId && grant.product === body.product
            && grant.connectionOwnerId === body.connectionOwnerId && grant.connectionId === body.connectionId
            && (!modelPolicies || (grant.modelId === body.modelId && grant.reasoningEffort === body.reasoningEffort)))) {
            throw new Error('This exact AI policy already exists for this role.');
          }
          const saved = await requestJson(`${ADMIN}/ai/${path}/role-grants`, { method: 'PUT', body });
          state.roleGrants = [...(state.roleGrants || []), saved];
          grantDialog.close(); renderRoleAi();
        }, [el('label', { className: 'account-field' }, ['App and database scope', scopeSelect]),
          ...(modelPolicies ? [el('label', { className: 'account-field' }, ['Allowed model', modelSelect]),
            el('label', { className: 'account-field' }, ['Reasoning level', reasoningSelect]),
            ...(!verified || !models.length ? [el('p', { className: 'account-error', text: 'Test the Shared Codex connection in Administration before granting a model.' })] : [])] : [])]);
        if (!scopes.length || (modelPolicies && (!verified || !models.length))) form.querySelector('button[type="submit"]').disabled = true;
        grantDialog.append(form, button('Cancel', () => grantDialog.close())); grantDialog.showModal();
      }
      for (const { name: providerName, path, state } of providerStates) {
        const add = button(`Add ${providerName} policy`, () => addRoleAi(path, state));
        add.disabled = !scopes.length || !!state.error;
        buttons.append(add);
      }
      const markChanged = () => {
        roleChanged = true; changedNotice.textContent = 'Save role changes before editing AI policies.';
        for (const control of aiSection.querySelectorAll('button')) control.disabled = true;
      };
      roleForm.addEventListener('input', markChanged);
      roleForm.addEventListener('change', markChanged);
      renderRoleAi(); aiSection.append(changedNotice, grantsList, buttons);
    }
    const actions = el('div', { className: 'account-actions' }, [button('Cancel', () => d.close())]);
    if (role) actions.append(button('Delete role', async () => {
      await confirmAction({ title: 'Delete role?', message: `Remove “${role.name}” and revoke its grants?`, details: 'Users and dashboards are kept. Members may lose access to reports.', confirmLabel: 'Delete role', onConfirm: async () => {
        await requestJson(`${ADMIN}/roles/${role.id}`, { method: 'DELETE' }); d.close(); await adminPage();
      } });
    }));
    d.append(actions); d.showModal();
  }
}
async function initialize() {
  try {
    const status = await requestJson(`${AUTH}/status`);
    if (!status.authenticated) { if (location.pathname !== '/login') { location.replace(loginUrl()); return; } await login(status); return; }
    const account = await requestJson(`${AUTH}/me`);
    if (location.pathname === '/login') { location.replace(signInDestination(account)); return; }
    for (const [product, label, href] of [['schemii', 'Schemii', '/'], ['schemoo', 'Schemoo', '/schemoo'], ['schemer', 'Schemer', '/schemer']]) {
      if (canAccessProduct(account, product)) nav.append(link(label, href));
    }
    nav.append(link('Account', '/account'));
    if (account.is_admin) nav.append(link('Administration', '/admin'));
    nav.append(button('Sign out', async () => { try { await signOut(); } catch (e) { main.prepend(el('p', { className: 'account-error', text: e.message, attrs: { role: 'alert' } })); } }));
    if (location.pathname === '/admin') { if (!account.is_admin) throw new Error('Administrator access is required.'); await adminPage(); } else accountPage(account);
  } catch (e) { main.replaceChildren(...heading('Account unavailable', e.message), button('Try again', initialize)); }
}
void initialize();
