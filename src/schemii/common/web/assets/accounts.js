import { loginUrl } from './login-return.js';
import { requestJson } from './http.js';
import { element as el } from './dom.js';
import { confirmAction } from './confirmation.js';
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
async function adminPage() {
  document.title = 'Administration · Schemii';
  main.replaceChildren(...heading('Administration', 'Assign application access and Schemii-owned read-only database accounts through roles. PostgreSQL controls visible rows, columns, and write operations.'));
  const [users, roles, resources, managedResult] = await Promise.all([
    requestJson(`${ADMIN}/accounts`), requestJson(`${ADMIN}/roles`), requestJson(`${ADMIN}/resources`),
    requestJson(`${ADMIN}/schemii-connections`).then(value => ({ connections: value.connections })).catch(error => ({ error })),
  ]);
  const managedConnections = managedResult.connections || [];
  const columns = el('div', { className: 'account-columns' });
  const people = el('section', { className: 'account-panel', attrs: { 'aria-labelledby': 'admin-people-title' } }, [el('h2', { text: 'People', attrs: { id: 'admin-people-title' } }), button('Add user', () => editUser(null), true)]);
  const rolePanel = el('section', { className: 'account-panel', attrs: { 'aria-labelledby': 'admin-roles-title' } }, [el('h2', { text: 'Roles', attrs: { id: 'admin-roles-title' } }), button('Create role', () => editRole(null), true)]);
  const userList = el('div', { className: 'account-list' });
  for (const user of users) userList.append(el('div', { className: 'account-row' }, [el('div', {}, [el('strong', { text: user.display_name || user.username }), el('small', { text: `${user.username} · ${user.disabled ? 'Disabled' : user.is_admin ? 'Administrator' : 'Active'}` })]), button('Edit', () => editUser(user))]));
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
  main.append(columns, connectionPanel, link('Jump to system diagnostics', '#system-diagnostics'), diagnostics);
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
    const username = field('Username', { value: user?.username || '', autocomplete: 'off' });
    const display = field('Display name', { value: user?.display_name || '', autocomplete: 'off' });
    const password = field(user ? 'Reset password (leave blank to keep)' : 'Initial password', { type: 'password', autocomplete: 'new-password', required: !user });
    const admin = check('Can provision accounts and roles', user?.is_admin), disabled = check('Disable sign-in and revoke sessions', user?.disabled);
    d.append(formWithSubmit(user ? 'Save user' : 'Create user', async () => {
      const body = { display_name: display.input.value.trim(), is_admin: admin.input.checked, ...(password.input.value ? { password: password.input.value } : {}), ...(user ? { disabled: disabled.input.checked } : { username: username.input.value.trim() }) };
      await requestJson(`${ADMIN}/accounts${user ? `/${user.id}` : ''}`, { method: user ? 'PATCH' : 'POST', body }); d.close(); await adminPage();
    }, [...(!user ? [username.node] : []), display.node, password.node, admin.node, ...(user ? [disabled.node] : []), el('p', { text: 'Share initial or reset passwords through a secure channel. Users can change their password from Account.' })]));
    d.append(button('Cancel', () => d.close())); d.showModal();
  }
  function editRole(role) {
    const d = dialog(role ? 'Edit role' : 'Create role');
    const name = field('Role name', { value: role?.name || '' });
    const capabilities = [
      ['schemii:access', 'Schemii — schema design and SQL'],
      ['schemoo:access', 'Schemoo — semantic models'],
      ['schemer:access', 'Schemer — view granted dashboards'],
      ['schemer:author', 'Schemer — create and edit dashboards'],
    ].map(([id, label]) => ({ id, ...check(label, role?.capabilities.includes(id)) }));
    const memberships = users.map(user => ({ id: user.id, ...check(`${user.display_name || user.username} (${user.username})`, role?.user_ids.includes(user.id)) }));
    const roleConnections = resources.connections.filter(connection => connection.ownership === 'schemii');
    const legacyConnections = (role?.connections || []).filter(grant => grant.owner_id !== 'user_schemii_connection_pool');
    const profiles = roleConnections.map(connection => {
      const current = role?.connections.find(c => c.connection_id === connection.id && c.owner_id === connection.owner_id);
      const enabled = check(`Schemii-owned read-only · ${connection.name || connection.id} · ${connection.database} · ${connection.username}`, !!current);
      const authoring = check('Use in Schemii and Schemoo tools or Schemer editing', current?.allow_authoring);
      const options = el('div', { className: 'account-editor', hidden: !current }, [authoring.node,
        el('p', { text: 'Enable this even for read-only PostgreSQL profiles. This permits app workflows; PostgreSQL still controls visible rows, columns, and write privileges.' }),
      ]);
      enabled.input.onchange = () => { options.hidden = !enabled.input.checked; };
      return { connection, input: enabled.input, authoring, node: el('div', { className: 'account-grant' }, [enabled.node, options]) };
    });
    const grants = resources.dashboards.map(dashboard => {
      const saved = role?.dashboards.find(g => g.dashboard_id === dashboard.id && g.owner_id === dashboard.owner_id);
      const current = saved && roleConnections.some(c => c.id === saved.connection_id && c.owner_id === saved.connection_owner_id) ? saved : null;
      const enabled = check(dashboard.name, !!current), exp = check('Allow export', current?.can_export), drill = check('Allow drill-through', current?.can_drill);
      const select = el('select', { attrs: { 'aria-label': `Database connection for ${dashboard.name}` } });
      select.append(el('option', { text: 'Choose an authorized connection', attrs: { value: '' } }));
      roleConnections.forEach((c, i) => select.append(el('option', { text: `Schemii-owned · ${c.name || c.id}`, attrs: { value: String(i) } })));
      if (current) select.value = String(roleConnections.findIndex(c => c.id === current.connection_id && c.owner_id === current.connection_owner_id));
      const options = el('div', { className: 'account-editor', hidden: !current }, [el('label', { className: 'account-field' }, ['Run using', select]), exp.node, drill.node]);
      enabled.input.onchange = () => { options.hidden = !enabled.input.checked; };
      return { dashboard, enabled, select, exp, drill, node: el('div', { className: 'account-grant' }, [enabled.node, options]) };
    });
    const sections = [name.node, el('h3', { text: 'Applications' }), el('div', { className: 'account-list' }, capabilities.map(c => c.node)), el('p', { text: 'Schemer editing also requires Schemer access. Database operations use the PostgreSQL identity on an authorized connection.' }), el('h3', { text: 'Members' }), el('div', { className: 'account-list' }, memberships.map(m => m.node)), el('h3', { text: 'Schemii-owned read-only accounts' }), el('p', { text: 'Choose which read-only PostgreSQL identity this role may use. Credentials stay on the server; PostgreSQL grants and row policies apply to every query.' }), el('div', { className: 'account-list' }, profiles.length ? profiles.map(p => p.node) : [el('p', { text: 'No Schemii-owned accounts are available yet. Add one in Administration first.' })]), ...(legacyConnections.length ? [el('h3', { text: 'Inactive legacy user-owned grants' }), el('p', { text: 'Personal credentials can no longer be shared through roles. Create a Schemii-owned read-only account for each needed row-policy identity, select it above, and rebind affected dashboards. Saving this role removes these inactive grants.' }), el('div', { className: 'account-list' }, legacyConnections.map(c => el('p', { text: `${c.connection_id} · owned by ${c.owner_id} · user-owned, not assignable` })))] : []), el('h3', { text: 'Shared dashboards' }), el('p', { text: 'Bind each dashboard to one of the connections selected above. The database applies that connection’s row and column permissions.' }), el('div', { className: 'account-list' }, grants.length ? grants.map(g => g.node) : [el('p', { text: 'Create a dashboard in Schemer before sharing it.' })])];
    d.append(formWithSubmit('Save role', async () => {
      const selected = profiles.filter(p => p.input.checked).map(p => p.connection);
      const dashboards = grants.filter(g => g.enabled.input.checked).map(g => {
        const c = g.select.value === '' ? null : roleConnections[Number(g.select.value)];
        if (!c || !selected.includes(c)) throw new Error(`Select an authorized role connection for “${g.dashboard.name}”.`);
        return { dashboard_id: g.dashboard.id, owner_id: g.dashboard.owner_id, connection_id: c.id, connection_owner_id: c.owner_id, can_export: g.exp.input.checked, can_drill: g.drill.input.checked };
      });
      await requestJson(`${ADMIN}/roles${role ? `/${role.id}` : ''}`, { method: role ? 'PUT' : 'POST', body: { name: name.input.value.trim(), capabilities: capabilities.filter(c => c.input.checked).map(c => c.id), user_ids: memberships.filter(m => m.input.checked).map(m => m.id), connections: profiles.filter(p => p.input.checked).map(p => ({ connection_id: p.connection.id, owner_id: p.connection.owner_id, allow_authoring: p.authoring.input.checked })), dashboards } });
      d.close(); await adminPage();
    }, sections));
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
