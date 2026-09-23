import { policyIdentity as identity, scopeIdentity, policyChanges, policyRequest } from './admin-policy.js';
import { element as el } from './dom.js';
import { requestJson } from './http.js';
import { confirmAction } from './confirmation.js';
import { reasoningLabel, reasoningLevels, populateReasoningOptions } from './ai-reasoning.js';

const API = '/api/v1/admin';
const POOL = 'user_schemii_connection_pool';
const APPS = { 'schemii:access': 'Schemii', 'schemoo:access': 'Schemoo', 'schemer:access': 'Schemer viewer', 'schemer:author': 'Schemer author' };
const SECTIONS = [['users', 'Users'], ['databases', 'Databases'], ['roles', 'Roles'], ['ai', 'AI Connections'], ['diagnostics', 'Diagnostics']];
const btn = (text, action, primary = false) => { const node = el('button', { type: 'button', className: `ui-button${primary ? ' primary' : ''}`, text }); node.onclick = action; return node; };
const paragraph = text => el('p', { text });
const label = (text, input) => el('label', { className: 'account-field' }, [text, input]);
const field = (name, value = '', type = 'text', required = true) => {
  const input = el('input', { attrs: { type, required: required ? '' : undefined, autocomplete: type === 'password' ? 'new-password' : 'off' } }); input.value = value ?? '';
  return { input, node: label(name, input) };
};
const check = (name, checked) => { const input = el('input', { attrs: { type: 'checkbox' } }); input.checked = !!checked; return { input, node: el('label', { className: 'account-check' }, [input, name]) }; };
const section = (title, description, nodes = []) => el('section', { className: 'admin-card', attrs: { 'aria-label': title } }, [el('h2', { text: title }), ...(description ? [paragraph(description)] : []), ...nodes]);
let dispose = () => {};

export function renderAdminWorkspace(ctx) {
  dispose();
  const { main, users, roles, resources, managedResult, zenResult, sharedCodexResult, reload, accessEditor, roleAiScopes, roleAccess, directAccess, aiScopeLabel, providerPanels } = ctx;
  const connections = managedResult.connections || [];
  const providers = [{ path: 'shared-codex', name: 'Shared Codex', state: sharedCodexResult }, { path: 'zen', name: 'OpenCode Zen', state: zenResult }];
  let dirty = false, currentRoute = '', saving = false;
  const body = el('div', { className: 'admin-content' });
  const navigation = el('nav', { className: 'admin-nav', attrs: { 'aria-label': 'Administration sections' } });
  for (const [id, name] of SECTIONS) navigation.append(el('a', { text: name, attrs: { href: `#${id}`, 'data-section': id } }));
  main.classList.add('admin-workspace');
  main.replaceChildren(el('header', { className: 'admin-heading' }, [el('small', { className: 'eyebrow', text: 'Schemii · Administration' }), el('h1', { text: 'Access & connections' }), paragraph('Manage people, database access, and the AI they can use.')]), el('div', { className: 'admin-layout' }, [navigation, body]));
  const leaveWarning = event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
  window.addEventListener('beforeunload', leaveWarning);
  const discard = action => {
    if (saving) return;
    if (!dirty) { action(); return; }
    void confirmAction({ title: 'Discard unsaved changes?', message: 'Your pending account, access, and AI changes have not all been saved.', details: 'Any changes already reported as saved will remain.', confirmLabel: 'Discard changes', onConfirm: async () => { dirty = false; action(); } });
  };
  const go = route => discard(() => { location.hash = route; });
  const navigate = event => {
    const anchor = event.target.closest('a[href]');
    if (anchor && saving) { event.preventDefault(); return; }
    if (anchor && dirty && !anchor.target) { event.preventDefault(); discard(() => { location.href = anchor.href; }); }
  };
  document.addEventListener('click', navigate);
  const hashChanged = () => {
    const target = location.hash.slice(1) || 'users';
    if (dirty || saving) {
      history.replaceState(null, '', `#${currentRoute}`);
      discard(() => { location.hash = target; }); return;
    }
    render(target);
  };
  window.addEventListener('hashchange', hashChanged);
  dispose = () => { window.removeEventListener('beforeunload', leaveWarning); window.removeEventListener('hashchange', hashChanged); document.removeEventListener('click', navigate); };
  function page(title, description, action) {
    body.replaceChildren(el('div', { className: 'admin-page-heading' }, [el('div', {}, [el('h2', { text: title }), paragraph(description)]), ...(action ? [action] : [])]));
  }
  function searchList(name, rows, empty) {
    const search = field(`Search ${name}`, '', 'search', false);
    search.input.placeholder = `Find ${name.toLowerCase()}…`;
    const list = el('div', { className: 'admin-record-list' }, rows);
    const noResults = paragraph(empty); noResults.hidden = rows.length > 0;
    search.input.oninput = () => { const query = search.input.value.toLowerCase().trim(); let visible = 0; rows.forEach(row => { row.hidden = !row.textContent.toLowerCase().includes(query); if (!row.hidden) visible++; }); noResults.hidden = visible > 0; };
    return el('div', {}, [search.node, list, noResults]);
  }
  function record(title, description, detail, action) { return el('div', { className: 'account-row' }, [el('div', {}, [el('strong', { text: title }), el('small', { text: description }), ...(detail ? [el('small', { text: detail })] : [])]), action]); }
  const dbName = grant => {
    const id = grant.connection_id || grant.connectionId, owner = grant.owner_id || grant.connectionOwnerId;
    const profile = resources.connections.find(c => c.id === id && c.owner_id === owner)
      || providers.flatMap(p => p.state.connections || []).find(c => (c.connectionId || c.id) === id && (c.connectionOwnerId || c.owner_id) === owner);
    if (profile) return `${profile.name || profile.database}${owner !== POOL ? ' · inactive personal profile' : ''}`;
    return `Unavailable ${owner === POOL ? 'managed' : 'private'} profile · …${(id || '').slice(-6)}`;
  };
  const roleSummary = role => `${role.capabilities.map(c => APPS[c] || c).join(', ') || 'No apps'} · ${role.connections.map(dbName).join(', ') || 'No database'}`;
  const subjectPolicies = (kind, id) => providers.flatMap(provider => (provider.state[kind === 'role' ? 'roleGrants' : 'grants'] || []).filter(g => g[kind === 'role' ? 'roleId' : 'userId'] === id).map(g => ({ ...g, path: provider.path })));
  function render(route) {
    currentRoute = route; dirty = false;
    const [tab, id, mode] = route.split('/');
    for (const anchor of navigation.children) { if (anchor.dataset.section === tab) anchor.setAttribute('aria-current', 'page'); else anchor.removeAttribute('aria-current'); }
    if (tab === 'users' && id) return editSubject('user', id === 'new' ? null : users.find(u => u.id === decodeURIComponent(id)), mode ? connections.find(c => c.id === decodeURIComponent(mode)) : null);
    if (tab === 'roles' && id) return editSubject('role', id === 'new' ? null : roles.find(r => r.id === decodeURIComponent(id)), mode ? connections.find(c => c.id === decodeURIComponent(mode)) : null, mode === 'members' ? 'members' : null);
    if (tab === 'databases' && id) {
      const connection = connections.find(c => c.id === decodeURIComponent(id));
      if (id === 'new' || mode === 'edit') return editConnection(connection);
      if (connection) return databasePage(connection);
    }
    if (tab === 'users') {
      page('Users', `${users.filter(u => !u.disabled).length} active users. Open a user to review their access and its source.`, btn('Add user', () => go('users/new'), true));
      body.append(searchList('users', [...users].sort((a,b) => Number(a.disabled)-Number(b.disabled)).map(user => {
        const memberships = roles.filter(r => r.user_ids.includes(user.id));
        return record(user.display_name || user.username, `${user.username} · ${user.disabled ? 'Disabled' : user.is_admin ? 'Administrator' : 'Active'}`, `${memberships.length} roles · ${directAccess(user).connections.length} direct database profiles`, btn(`Edit ${user.username}`, () => go(`users/${encodeURIComponent(user.id)}`)));
      }), 'No users match your search.'));
    } else if (tab === 'roles') {
      page('Roles', 'Bundle app, database, dashboard, and AI permissions. Assigning a role grants its complete access set.', btn('Create role', () => go('roles/new'), true));
      body.append(searchList('roles', roles.map(role => record(role.name, `${role.user_ids.length} members`, roleSummary(role), btn(`Edit ${role.name}`, () => go(`roles/${encodeURIComponent(role.id)}`)))), 'No roles match your search.'));
    } else if (tab === 'databases') {
      page('Databases', 'Each profile is an installation-owned PostgreSQL identity. Open one to manage who can use it and which AI they receive.', btn('Add database profile', () => go('databases/new'), true));
      if (managedResult.error) body.append(section('Database profiles unavailable', managedResult.error.message, [btn('Retry', reload)]));
      else body.append(searchList('databases', connections.map(c => record(c.name, `${c.database} · ${c.username}@${c.host}`, `${roles.filter(r => r.connections.some(g => g.connection_id === c.id && g.owner_id === POOL)).length} roles · Credential ${c.credentialStored ? 'stored' : 'missing'}`, btn('Manage access', () => go(`databases/${encodeURIComponent(c.id)}`)))), 'No database profiles match. Add an existing PostgreSQL login to share it with users.'));
    } else if (tab === 'ai') {
      page('AI Connections', 'Connect an installation-owned provider and verify its models. Assign model access from a user, role, or database.');
      body.append(...providerPanels());
    } else if (tab === 'diagnostics') {
      page('Diagnostics', 'Inspect how this installation handles requests and database operations.');
      for (const [name, description, url] of [['Live system map','Follow request journeys through the application.','/system-map'],['API lens','Inspect routes and request contracts.','/api-map'],['Database lens','Trace application calls into database operations.','/db-map']]) body.append(record(name, description, '', el('a', { className: 'ui-button', text: `Open ${name.toLowerCase()}`, attrs: { href: url } })));
    } else { page('Page unavailable', 'Choose an administration section to continue.'); }
    body.querySelector('h2')?.setAttribute('tabindex', '-1');
  }
  function editor(title, description, returnTo, saveLabel, onSave) {
    page(title, description);
    body.prepend(btn('← Back', () => go(returnTo)));
    const form = el('form', { className: 'admin-detail-form' });
    const content = el('div', { className: 'admin-detail-content' });
    const status = el('span', { text: 'No unsaved changes', attrs: { role: 'status' } });
    const error = el('p', { className: 'account-error', attrs: { role: 'alert' } });
    const save = el('button', { type: 'submit', className: 'ui-button primary', text: saveLabel });
    const cancel = btn('Cancel', () => discard(() => { location.hash = returnTo; }));
    const footer = el('footer', { className: 'admin-savebar' }, [el('div', {}, [status, error]), el('div', { className: 'account-actions' }, [cancel, save])]);
    const markDirty = event => { if (event?.target?.type === 'search' || event?.target?.closest('.admin-ai-composer')) return; dirty = true; status.textContent = 'Unsaved changes'; };
    form.addEventListener('input', markDirty); form.addEventListener('change', markDirty);
    form.onsubmit = async event => {
      event.preventDefault(); saving = true; content.inert = true; save.disabled = cancel.disabled = true; status.textContent = 'Saving changes…'; error.textContent = '';
      try { await onSave(); dirty = false; history.replaceState(null, '', `#${returnTo}`); await reload(); }
      catch (e) { dirty = true; error.textContent = e.message; status.textContent = 'Save incomplete — review and retry'; }
      finally { saving = false; content.inert = false; save.disabled = cancel.disabled = false; }
    };
    form.append(content, footer); body.append(form);
    const tabs = (definitions, selected) => {
      const bar = el('div', { className: 'admin-detail-tabs', attrs: { role: 'tablist', 'aria-label': 'Editor sections' } });
      const panels = definitions.map(([id, title, nodes]) => {
        const panel = el('div', { attrs: { role: 'tabpanel', id: `admin-panel-${id}`, 'aria-labelledby': `admin-tab-${id}` } }, nodes);
        const tab = btn(title, () => activate(id));
        tab.id = `admin-tab-${id}`; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', panel.id);
        bar.append(tab); return { id, tab, panel };
      });
      function activate(id) { for (const item of panels) { const active = item.id === id; item.tab.setAttribute('aria-selected', String(active)); item.tab.tabIndex = active ? 0 : -1; item.panel.hidden = !active; } }
      bar.onkeydown = event => {
        const index = panels.findIndex(item => item.tab === document.activeElement);
        if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? panels.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + panels.length) % panels.length;
        activate(panels[next].id); panels[next].tab.focus();
      };
      form.addEventListener('invalid', event => {
        const item = panels.find(item => item.panel.contains(event.target));
        if (item?.panel.hidden) { event.preventDefault(); activate(item.id); requestAnimationFrame(() => event.target.reportValidity()); }
      }, true);
      content.replaceChildren(bar, ...panels.map(item => item.panel)); activate(selected);
    };
    return { content, markDirty, status, tabs };
  }
  function groupedAccess(access) {
    const cards = [section('App access without a database', 'These app permissions apply across this access set. Database tool use is enabled separately for each profile.', access.capabilities.map(item => item.node))];
    cards.push(section('Database access', 'Select the installation-owned identities this access set may use. PostgreSQL controls the rows, columns, and operations available.', access.profiles.length ? access.profiles.map(item => item.node) : [paragraph('No database profiles are available. Add one in Databases.')]));
    if (access.dashboards.length) cards.push(section('Shared dashboards', 'Choose the exact database identity each dashboard runs with. Schemer viewing and that identity must be enabled in this same access set.', access.dashboards.map(item => item.node)));
    return cards;
  }
  function effectiveAccess(user, memberships) {
    const sets = [{ name: 'Direct access', ...directAccess(user) }, ...memberships.map(r => ({ ...r, name: `Role: ${r.name}` }))];
    const rows = [];
    for (const set of sets) {
      if (set.capabilities.length) rows.push(record('App access', set.capabilities.map(c => APPS[c] || c).join(', '), `Source: ${set.name}`, el('span')));
      for (const grant of set.connections) rows.push(record(dbName(grant), `${set.capabilities.map(c => APPS[c] || c).join(', ') || 'No app permissions'} · ${grant.allow_authoring ? 'App tools enabled' : 'Dashboard use only'}`, `Source: ${set.name}`, el('span')));
    }
    return section('Saved effective access', `${user.disabled ? 'Sign-in is disabled; these saved assignments are currently unusable. ' : ''}Direct access and assigned roles combine. Editing a role affects all its members. Changes below take effect when saved.`, rows.length ? rows : [paragraph('This user has no saved app or database access.')]);
  }
  function editSubject(kind, subject, selectedConnection = null, initialTab = null) {
    const isRole = kind === 'role';
    if (subject === undefined) { page('Record unavailable', 'This record was removed. Return to the list to refresh.'); return; }
    let savedId = subject?.id;
    const name = field(isRole ? 'Role name' : 'Display name', isRole ? subject?.name : subject?.display_name);
    const username = field('Username', subject?.username);
    const password = field(subject ? 'Reset password (leave blank to keep)' : 'Initial password', '', 'password', !subject);
    password.input.minLength = 12; password.input.maxLength = 256;
    const admin = check('Administrator — manage all users, roles, and connections', subject?.is_admin);
    const disabled = check('Disable sign-in and revoke sessions', subject?.disabled);
    const direct = directAccess(subject);
    const preselectDirect = !isRole && selectedConnection && !direct.connections.some(grant => grant.connection_id === selectedConnection.id && grant.owner_id === POOL);
    const accessValue = isRole ? subject : preselectDirect ? { ...direct, connections: [...direct.connections, { connection_id: selectedConnection.id, owner_id: POOL, allow_authoring: false }] } : direct;
    const access = accessEditor(accessValue, resources, { selectedConnection });
    const memberships = (isRole ? users : roles).map(item => ({ item, ...check(isRole ? `${item.display_name || item.username} (${item.username})` : item.name, isRole ? subject?.user_ids.includes(item.id) : item.user_ids.includes(subject?.id)) }));
    const membershipValues = () => memberships.filter(m => m.input.checked).map(m => m.item);
    const getScopes = () => {
      const own = access.values();
      const scopes = roleAiScopes(own);
      if (!isRole) {
        for (const role of membershipValues()) scopes.push(...roleAiScopes(role));
        // Existing personal database profiles remain eligible for direct AI policies.
        for (const provider of providers) for (const connection of provider.state.connections || []) {
          if (connection.userId !== subject?.id || connection.ownership === 'schemii') continue;
          const capabilities = [...own.capabilities, ...membershipValues().flatMap(role => role.capabilities)];
          if (capabilities.includes(`${connection.product}:access`)) scopes.push({ product: connection.product, connectionOwnerId: connection.connectionOwnerId ?? connection.owner_id, connectionId: connection.connectionId ?? connection.id });
        }
      }
      return scopes.filter((s, i, list) => list.findIndex(other => scopeIdentity(other) === scopeIdentity(s)) === i);
    };
    const legacyGrants = (isRole ? subject?.connections || [] : directAccess(subject).connections).filter(g => g.owner_id !== POOL);
    const acknowledgeLegacy = check('Remove these inactive legacy grants when saving', false);
    let ai;
    const detail = editor(subject ? `Edit ${isRole ? subject.name : subject.display_name || subject.username}` : `Add ${isRole ? 'role' : 'user'}`, isRole ? 'A role grants its complete permission set to every member. Use a separate role for each database when permissions differ.' : 'Review access by source, assign roles, or grant individual permissions.', isRole ? 'roles' : 'users', isRole ? 'Save role' : subject ? 'Save user' : 'Create user', async () => {
      if (legacyGrants.length && !acknowledgeLegacy.input.checked) throw new Error('Review and confirm removal of the inactive legacy database grants before saving.');
      const values = access.values();
      ai.validate();
      const payload = isRole ? { name: name.input.value.trim(), user_ids: membershipValues().map(u => u.id), ...values } : { display_name: name.input.value.trim(), is_admin: admin.input.checked, role_ids: membershipValues().map(r => r.id), direct_access: values, ...(password.input.value ? { password: password.input.value } : {}), ...(savedId ? { disabled: disabled.input.checked } : { username: username.input.value.trim() }) };
      const result = await requestJson(`${API}/${isRole ? 'roles' : 'accounts'}${savedId ? `/${encodeURIComponent(savedId)}` : ''}`, { method: savedId ? isRole ? 'PUT' : 'PATCH' : 'POST', body: payload });
      savedId = result.id || savedId; password.input.value = ''; password.input.required = false;
      try { await ai.save(savedId); } catch (error) { throw new Error(`The ${kind} and its app/database permissions were saved. Some AI changes remain unsaved: ${error.message}. Retry Save to finish, or cancel to leave the saved changes in place.`); }
    });
    const info = [name.node];
    if (!isRole) info.push(...(!subject ? [username.node] : []), password.node, admin.node, disabled.node);
    detail.content.append(section(isRole ? 'Role details' : 'Account', '', info));
    if (!isRole && subject) detail.content.append(effectiveAccess(subject, roles.filter(r => r.user_ids.includes(subject.id))));
    const memberSummary = paragraph('');
    const syncMembers = () => { memberSummary.textContent = isRole ? `This save affects ${new Set([...(subject?.user_ids || []), ...membershipValues().map(u => u.id)]).size} users, including removed members.` : `${membershipValues().length} roles selected. Each selected role grants all the databases listed below.`; };
    memberships.forEach(m => m.input.addEventListener('change', syncMembers)); syncMembers();
    detail.content.append(section(isRole ? 'Members & impact' : 'Access through roles', isRole ? 'Every member receives all apps, databases, dashboards, and AI policies in this role.' : 'Use database-specific roles to give this person different permissions on different databases.', [memberSummary, searchList(isRole ? 'members' : 'available roles', memberships.map(m => el('div', { className: 'account-grant' }, [m.node, ...(!isRole ? [el('small', { text: roleSummary(m.item) })] : [])])), 'No matches.')]));
    if (legacyGrants.length) detail.content.append(section('Inactive legacy database grants', 'Personal credentials cannot be shared. Saving this page removes these obsolete grants; select a replacement installation-owned profile and rebind its dashboards before confirming.', [paragraph(legacyGrants.map(dbName).join(', ')), acknowledgeLegacy.node]));
    detail.content.append(...groupedAccess(access));
    ai = stagedAi(kind, subject, getScopes, detail.markDirty);
    detail.content.append(ai.node);
    detail.content.addEventListener('change', event => { if (!event.target.closest('.admin-ai-composer')) ai.refresh(); });
    if (subject) detail.content.append(section('Remove access', isRole ? 'Deleting this role revokes its grants for all members.' : 'Removing an account revokes sign-in and grants. Their saved work remains.', [btn(isRole ? 'Delete role' : 'Remove account', () => confirmAction({ title: isRole ? 'Delete role?' : 'Remove user account?', message: `Remove “${isRole ? subject.name : subject.display_name || subject.username}”?`, details: 'This takes effect immediately and discards pending edits on this page.', confirmLabel: isRole ? 'Delete role' : 'Remove account', onConfirm: async () => { await requestJson(`${API}/${isRole ? 'roles' : 'accounts'}/${encodeURIComponent(subject.id)}`, { method: 'DELETE' }); dirty = false; location.hash = isRole ? 'roles' : 'users'; await reload(); } }))]));
    const inheritedAi = section('AI access through roles', 'These policies come from the selected roles. Edit the source role to change a policy for all its members.');
    const refreshInheritedAi = () => {
      const rows = [];
      for (const role of membershipValues()) for (const policy of subjectPolicies('role', role.id)) {
        const provider = providers.find(p => p.path === policy.path);
        const active = !disabled.input.checked && policy.active !== false && provider.state.connected
          && (policy.path !== 'shared-codex' || (provider.state.verifiedModels || []).some(model => model.id === policy.modelId));
        rows.push(record(aiScopeLabel(policy, resources), `${provider.name}${policy.modelId ? ` · ${policy.modelId} · ${reasoningLabel(policy.reasoningEffort)}` : ''}`,
          `${active ? role.user_ids.includes(subject?.id) ? 'Active' : 'Active after save' : 'Inactive'} · Source: role ${role.name}${role.user_ids.includes(subject?.id) ? '' : ' · Pending role assignment'}`,
          btn(`Edit role ${role.name}`, () => go(`roles/${encodeURIComponent(role.id)}`))));
      }
      inheritedAi.replaceChildren(el('h2', { text: 'AI access through roles' }), paragraph('These policies come from the selected roles. Edit the source role to change a policy for all its members.'), ...(rows.length ? rows : [paragraph('The selected roles grant no AI access.')]));
    };
    if (!isRole) { refreshInheritedAi(); detail.content.addEventListener('change', refreshInheritedAi); }
    const cards = [...detail.content.children];
    if (isRole) detail.tabs([
      ['permissions', 'Permissions', cards.filter(card => !['Members & impact', 'Role AI access'].includes(card.getAttribute('aria-label')))],
      ['members', 'Members', cards.filter(card => card.getAttribute('aria-label') === 'Members & impact')],
      ['ai', 'AI', [ai.node]],
    ], initialTab || 'permissions');
    else detail.tabs([
      ['account', 'Account', cards.filter(card => ['Account', 'Remove access'].includes(card.getAttribute('aria-label')))],
      ['access', 'Access', cards.filter(card => !['Account', 'Remove access', 'Direct AI access'].includes(card.getAttribute('aria-label')))],
      ['ai', 'AI', [inheritedAi, ai.node]],
    ], selectedConnection || subject ? 'access' : 'account');
    if (preselectDirect) detail.markDirty();
  }
  function stagedAi(kind, subject, getScopes, changed) {
    const initial = subjectPolicies(kind, subject?.id);
    let persisted = [...initial], draft = [...initial], editingPolicy = null, pendingSelection = null;
    const node = section(kind === 'role' ? 'Role AI access' : 'Direct AI access', 'Stage model access here, then save this page. Each policy permits one app, database profile, model, and reasoning level. Installation credentials stay private.');
    const list = el('div', { className: 'account-list' });
    const addArea = el('div', { className: 'admin-ai-composer', hidden: true });
    const providerSelect = el('select', { attrs: { 'aria-label': 'AI connection' } });
    providers.forEach(p => providerSelect.append(el('option', { text: `${p.name}${p.state.connected ? '' : ' · disconnected'}`, attrs: { value: p.path } })));
    const scopeSelect = el('select', { attrs: { 'aria-label': 'App and database scope' } });
    const modelSelect = el('select', { attrs: { 'aria-label': 'Allowed model' } });
    const reasoningSelect = el('select', { attrs: { 'aria-label': 'Reasoning level' } });
    const modelField = label('Allowed model', modelSelect), reasoningField = label('Reasoning level', reasoningSelect);
    const help = paragraph(''); help.setAttribute('role', 'status');
    let scopes = [], models = [];
    const refresh = () => {
      try { scopes = getScopes(); } catch { scopes = []; }
      const selectedScope = pendingSelection ? scopeIdentity(pendingSelection) : scopeSelect.value;
      scopeSelect.replaceChildren(...scopes.map(scope => el('option', { text: aiScopeLabel(scope, resources), attrs: { value: scopeIdentity(scope) } })));
      if (scopes.some(scope => scopeIdentity(scope) === selectedScope)) scopeSelect.value = selectedScope;
      else if (editingPolicy && selectedScope) {
        scopeSelect.append(el('option', { text: `Unavailable · ${aiScopeLabel(editingPolicy, resources)} — choose a scope`, attrs: { value: selectedScope, disabled: '' } })); scopeSelect.value = selectedScope;
      }
      const provider = providers.find(p => p.path === providerSelect.value);
      const isCodex = provider.path === 'shared-codex';
      models = provider.state.catalogCheckedAt ? provider.state.verifiedModels || [] : [];
      const selectedModel = pendingSelection?.modelId || modelSelect.value;
      modelSelect.replaceChildren(...models.map(model => el('option', { text: model.name || model.id, attrs: { value: model.id } })));
      if (models.some(model => model.id === selectedModel)) modelSelect.value = selectedModel;
      else if (editingPolicy && selectedModel && isCodex) {
        modelSelect.append(el('option', { text: `${selectedModel} · unavailable — choose a model`, attrs: { value: selectedModel, disabled: '' } })); modelSelect.value = selectedModel;
      } else if (models.some(model => model.id === 'gpt-6-luna')) modelSelect.value = 'gpt-6-luna';
      updateReasoning(pendingSelection?.reasoningEffort);
      pendingSelection = null; modelField.hidden = reasoningField.hidden = !isCodex;
      help.textContent = !scopes.length ? 'Select matching app and database permissions above to make a scope available. Only Schemii supports AI without a database.' : !provider.state.connected ? 'Connect this provider in AI Connections before adding access.' : isCodex && !models.length ? 'Verify the models below to enable model selection without leaving this draft.' : 'The gateway enforces this exact policy on every AI turn.';
      syncStage();
      add.textContent = editingPolicy ? 'Stage policy changes' : 'Stage AI policy';
      verifyModels.hidden = !isCodex || !provider.state.connected;
      renderPolicies();
    };
    const updateReasoning = preferred => {
      populateReasoningOptions(reasoningSelect, models.find(model => model.id === modelSelect.value), preferred || reasoningSelect.value || 'default');
    };
    function syncStage() {
      const provider = providers.find(item => item.path === providerSelect.value);
      const model = models.find(item => item.id === modelSelect.value);
      add.disabled = !scopes.some(scope => scopeIdentity(scope) === scopeSelect.value) || !provider?.state.connected
        || (provider.path === 'shared-codex' && (!model || !reasoningLevels(model).includes(reasoningSelect.value)));
    }
    function renderPolicies() {
      list.replaceChildren();
      for (const policy of draft) {
        const eligible = scopes.some(scope => scopeIdentity(scope) === scopeIdentity(policy));
        const provider = providers.find(p => p.path === policy.path);
        const verified = policy.path !== 'shared-codex' || (provider.state.verifiedModels || []).some(m => m.id === policy.modelId);
        const status = !eligible ? 'Inactive — app or database access missing' : !provider.state.connected ? 'Inactive — connection unavailable' : !verified ? 'Inactive — model not verified' : subject?.disabled ? 'Inactive — user disabled' : 'Allowed by current draft';
        list.append(record(`${aiScopeLabel(policy, resources)}`, `${provider.name}${policy.modelId ? ` · ${policy.modelId} · ${reasoningLabel(policy.reasoningEffort)}` : ''}`, `${status}${persisted.some(p => identity(p) === identity(policy)) ? '' : ' · Pending addition'}`, el('div', { className: 'account-actions' }, [btn('Edit policy', () => { editingPolicy = policy; pendingSelection = policy; providerSelect.value = policy.path; addArea.hidden = false; refresh(); modelSelect.focus(); }), btn('Remove policy', () => { draft = draft.filter(p => p !== policy); if (editingPolicy === policy) { editingPolicy = null; addArea.hidden = true; } changed(); renderPolicies(); })])));
      }
      for (const policy of persisted.filter(p => !draft.some(d => identity(p) === identity(d)))) list.append(record(aiScopeLabel(policy, resources), `${policy.modelId || 'Zen'} · Pending removal`, '', btn('Undo removal', () => { draft.push(policy); changed(); renderPolicies(); })));
      if (!list.children.length) list.append(paragraph('No AI policies in this access set.'));
    }
    const add = btn('Stage AI policy', () => {
      syncStage(); if (add.disabled) return;
      const scope = scopes.find(s => scopeIdentity(s) === scopeSelect.value);
      if (!scope) return;
      const policy = { path: providerSelect.value, ...scope, ...(providerSelect.value === 'shared-codex' ? { modelId: modelSelect.value, reasoningEffort: reasoningSelect.value } : {}) };
      if (draft.some(p => p !== editingPolicy && identity(p) === identity(policy))) { help.textContent = 'This exact policy is already present.'; return; }
      if (editingPolicy) draft = draft.map(p => p === editingPolicy ? policy : p); else draft.push(policy);
      editingPolicy = null; changed(); addArea.hidden = true; renderPolicies();
    }, true);
    const verifyModels = btn('Verify models', async event => {
      const trigger = event.currentTarget; trigger.disabled = true; help.textContent = 'Verifying models for the installation connection…';
      try {
        const result = await requestJson(`${API}/ai/shared-codex/test`, { method: 'POST' });
        if (!result.connected) throw new Error(result.error || 'The installation connection could not be verified.');
        const provider = providers.find(p => p.path === 'shared-codex');
        provider.state.verifiedModels = result.models || [];
        provider.state.catalogCheckedAt = result.checkedAt || new Date().toISOString();
        refresh();
        help.textContent = `${provider.state.verifiedModels.length} models verified. Your pending access changes are preserved.`;
      } catch (error) { help.textContent = `Model verification failed: ${error.message}`; }
      finally { trigger.disabled = false; }
    });
    providerSelect.onchange = refresh;
    modelSelect.onchange = () => { updateReasoning(); syncStage(); };
    scopeSelect.onchange = reasoningSelect.onchange = syncStage;
    addArea.append(el('div', { className: 'admin-field-grid' }, [label('AI connection', providerSelect), label('App and database scope', scopeSelect), modelField, reasoningField]), help, el('div', { className: 'account-actions' }, [verifyModels, add, btn('Cancel policy', () => { addArea.hidden = true; editingPolicy = null; pendingSelection = null; })]));
    node.append(list, btn('Add AI policy', () => { editingPolicy = null; pendingSelection = null; scopeSelect.replaceChildren(); modelSelect.replaceChildren(); reasoningSelect.replaceChildren(); addArea.hidden = false; refresh(); providerSelect.focus(); }), addArea);
    refresh();
    return { node, refresh,
      validate() {
        if (!addArea.hidden) {
          node.closest('form')?.querySelector('[role="tab"][aria-controls="admin-panel-ai"]')?.click();
          throw new Error('Stage or cancel the open AI policy before saving this page.');
        }
        const allowed = getScopes();
        for (const policy of draft.filter(p => !persisted.some(old => identity(old) === identity(p)))) {
          if (!allowed.some(scope => scopeIdentity(scope) === scopeIdentity(policy))) throw new Error(`The pending AI policy for ${aiScopeLabel(policy, resources)} no longer has matching app/database access. Remove it or restore access before saving.`);
        }
      },
      async save(id) {
        const { removals, additions } = policyChanges(persisted, draft);
        try {
          for (const policy of removals) {
            const request = policyRequest(policy, kind, id, true);
            await requestJson(request.url, request.options);
            persisted = persisted.filter(p => identity(p) !== identity(policy));
          }
          for (const policy of additions) {
            const request = policyRequest(policy, kind, id);
            await requestJson(request.url, request.options);
            persisted.push(policy);
          }
        } finally { renderPolicies(); }

      },
    };
  }
  function databasePage(connection) {
    const owner = connection.ownerId || connection.owner_id || POOL;
    const grantsDb = grant => grant.connection_id === connection.id && grant.owner_id === owner;
    const scopedRoles = roles.filter(role => role.connections.some(grantsDb));
    const directUsers = users.filter(user => directAccess(user).connections.some(grantsDb));
    page(connection.name, `${connection.database} · ${connection.username}@${connection.host}:${connection.port}`, btn('Edit database profile', () => go(`databases/${encodeURIComponent(connection.id)}/edit`)));
    body.prepend(btn('← Databases', () => go('databases')));
    const testStatus = paragraph(''); testStatus.setAttribute('role', 'status');
    body.append(section('Installation-owned credential', `Credential ${connection.credentialStored ? 'stored' : 'missing'}. PostgreSQL permissions and row policies apply to every user of this identity.`, [btn('Test connection', async event => { const trigger = event.currentTarget; trigger.disabled = true; testStatus.textContent = 'Testing connection…'; try { const result = await requestJson(`${API}/schemii-connections/${encodeURIComponent(connection.id)}/test`, { method: 'POST' }); testStatus.textContent = `Connected to ${result.database}.`; } catch (error) { testStatus.textContent = `Connection failed: ${error.message}`; } finally { trigger.disabled = false; } }), testStatus]));
    const table = el('table', { className: 'admin-access-table' });
    const head = el('tr'); ['Access source', 'Apps & database use', 'Credential', 'AI policies', 'Manage'].forEach(text => head.append(el('th', { text, attrs: { scope: 'col' } }))); table.append(el('thead', {}, [head]));
    const rows = el('tbody');
    for (const [kind, subjects] of [['role', scopedRoles], ['user', directUsers]]) for (const subject of subjects) {
      const access = kind === 'role' ? subject : directAccess(subject);
      const grant = access.connections.find(grantsDb);
      const policies = subjectPolicies(kind, subject.id).filter(p => p.connectionOwnerId === owner && p.connectionId === connection.id);
      const allowedScopes = kind === 'role' ? roleAiScopes(subject) : [...roleAiScopes(access), ...roles.filter(r => r.user_ids.includes(subject.id)).flatMap(roleAiScopes)];
      const policyLines = policies.map(p => {
        const provider = providers.find(item => item.path === p.path);
        const active = !subject.disabled && p.active !== false && provider.state.connected && allowedScopes.some(s => scopeIdentity(s) === scopeIdentity(p))
          && (p.path !== 'shared-codex' || (provider.state.verifiedModels || []).some(model => model.id === p.modelId));
        return el('div', {}, [el('strong', { text: `${p.product} · ${p.modelId || 'Zen'}` }), el('small', { text: `${p.modelId ? reasoningLabel(p.reasoningEffort) + ' · ' : ''}${active ? 'Active' : 'Inactive'}` })]);
      });
      const cells = [el('div', {}, [el('strong', { text: kind === 'role' ? subject.name : subject.display_name || subject.username }), el('small', { text: kind === 'role' ? `Role · ${subject.user_ids.length} members${subject.connections.length > 1 ? ` · ${subject.connections.length} databases` : ''} · ${subject.user_ids.map(id => users.find(user => user.id === id)?.display_name || users.find(user => user.id === id)?.username || 'Unavailable user').join(', ') || 'No members'}` : 'Direct user access' })]), el('div', {}, [paragraph(access.capabilities.map(c => APPS[c] || c).join(', ') || 'No app access'), el('small', { text: grant.allow_authoring ? 'App tools enabled' : 'Dashboard use only' })]), el('span', { text: `${connection.username} · ${connection.credentialStored ? 'Stored' : 'Missing'}` }), el('div', { className: 'admin-policy-cell' }, policyLines.length ? policyLines : [el('span', { text: 'No AI access' })]), btn(kind === 'role' ? 'Edit role' : 'Edit user', () => go(`${kind === 'role' ? 'roles' : 'users'}/${encodeURIComponent(subject.id)}`))];
      rows.append(el('tr', {}, cells.map((cell, index) => el('td', { attrs: { 'data-label': ['Access source', 'Apps & database use', 'Credential', 'AI policies', 'Manage'][index] } }, [cell]))));
    }
    table.append(rows);
    body.append(section('Access on this database', 'Role membership grants the entire role, including its other database profiles. Direct grants affect one user. Open a role or user to stage app and AI changes.', [rows.children.length ? el('div', { className: 'admin-table-wrap' }, [table]) : paragraph('No roles or users have access yet.'), el('div', { className: 'account-actions' }, [btn('Create role for this database', () => go(`roles/new/${encodeURIComponent(connection.id)}`), true), btn('Assign a role to users', () => assignRole(connection, scopedRoles)), btn('Grant direct user access', () => chooseUser(connection))])]));
  }
  function chooseUser(connection) {
    const d = el('dialog', { className: 'account-dialog admin-small-dialog', attrs: { 'aria-label': 'Choose user' } }, [el('h2', { text: 'Choose user' }), paragraph(`${connection.name} will be selected as pending direct database access. Review app and tool permissions, then save the user.`)]);
    const select = el('select', { attrs: { 'aria-label': 'User' } });
    users.filter(u => !u.disabled).forEach(u => select.append(el('option', { text: u.display_name || u.username, attrs: { value: u.id } })));
    d.append(label('User', select), el('div', { className: 'account-actions' }, [btn('Continue', () => { d.close(); go(`users/${encodeURIComponent(select.value)}/${encodeURIComponent(connection.id)}`); }, true), btn('Cancel', () => d.close())]));
    d.onclose = () => d.remove(); document.body.append(d); d.showModal();
  }
  function assignRole(connection, scopedRoles) {
    const d = el('dialog', { className: 'account-dialog admin-small-dialog', attrs: { 'aria-label': 'Assign database role' } }, [el('h2', { text: 'Assign database role' }), paragraph('Choose a role, then edit its members. Review the complete scope before saving.')]);
    const select = el('select', { attrs: { 'aria-label': 'Role' } });
    [...scopedRoles].sort((a,b) => a.connections.length-b.connections.length).forEach(r => select.append(el('option', { text: `${r.name}${r.connections.length > 1 ? ' · multiple databases' : ''}`, attrs: { value: r.id } })));
    const summary = paragraph(''); const update = () => { const role = roles.find(r => r.id === select.value); summary.textContent = role ? `All members receive: ${roleSummary(role)}` : 'Create a role for this database first.'; }; select.onchange = update; update();
    const next = btn('Review role & members', () => { d.close(); go(`roles/${encodeURIComponent(select.value)}/members`); }, true); next.disabled = !scopedRoles.length;
    d.append(label('Role', select), summary, el('div', { className: 'account-actions' }, [next, btn('Cancel', () => d.close())])); d.onclose = () => d.remove(); document.body.append(d); d.showModal();
  }
  function editConnection(connection) {
    const name = field('Profile name', connection?.name), host = field('PostgreSQL host', connection?.host), port = field('Port', connection?.port ?? 5432, 'number'), database = field('Database', connection?.database), username = field('PostgreSQL username', connection?.username), password = field(connection ? 'Replace password (leave blank to keep)' : 'PostgreSQL password', '', 'password', !connection), timeout = field('Connect timeout (seconds)', connection?.connectTimeout ?? 10, 'number');
    port.input.min = 1; port.input.max = 65535; timeout.input.min = 1; timeout.input.max = 30; password.input.maxLength = 4096;
    const ssl = el('select', { attrs: { 'aria-label': 'SSL mode' } });
    ['verify-full','verify-ca','require','prefer','allow','disable'].forEach(value => ssl.append(el('option', { text: value, attrs: { value } }))); ssl.value = connection?.sslMode || 'verify-full';
    const detail = editor(connection ? `Edit ${connection.name}` : 'Add database profile', 'Use a PostgreSQL login provisioned with the required privileges and row policies. A connection test verifies connectivity, not read-only privileges.', 'databases', connection ? 'Save profile' : 'Add profile', async () => {
      const values = { name: name.input.value.trim(), host: host.input.value.trim(), port: Number(port.input.value), database: database.input.value.trim(), username: username.input.value.trim(), sslMode: ssl.value, connectTimeout: Number(timeout.input.value) };
      const payload = connection ? { expectedRevision: connection.revision } : values;
      if (connection) for (const [key, value] of Object.entries(values)) if (value !== connection[key]) payload[key] = value;
      if (password.input.value) payload.password = password.input.value;
      await requestJson(`${API}/schemii-connections${connection ? `/${encodeURIComponent(connection.id)}` : ''}`, { method: connection ? 'PATCH' : 'POST', body: payload }); password.input.value = '';
    });
    detail.content.append(section('Connection details', 'The installation owns this identity; assigned users never see its password.', [el('div', { className: 'admin-field-grid' }, [name.node, database.node, host.node, port.node, username.node, password.node, label('SSL mode', ssl), timeout.node])]));
    if (connection) detail.content.append(section('Remove profile', 'Remove dependent grants and saved work first. This does not delete the PostgreSQL login.', [btn('Delete database profile', () => confirmAction({ title: 'Delete database profile?', message: `Delete “${connection.name}”?`, details: 'Dependent grants or saved work prevent deletion.', confirmLabel: 'Delete profile', onConfirm: async () => { await requestJson(`${API}/schemii-connections/${encodeURIComponent(connection.id)}?expectedRevision=${connection.revision}`, { method: 'DELETE' }); dirty = false; location.hash = 'databases'; await reload(); } }))]));
  }
  render(location.hash.slice(1) || 'users');
}
