import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
test.use({ trace: 'off', video: 'off' });
const POOL = 'user_schemii_connection_pool';
const profile = { id: 'pg_admin_fixture', owner_id: POOL, ownerId: POOL, ownership: 'schemii', name: 'Reporting database', database: 'reports', username: 'report_reader', host: 'db.example.internal', port: 5432, sslMode: 'verify-full', connectTimeout: 10, credentialStored: true, revision: 1 };
const model = { id: 'gpt-6-luna', name: 'GPT-6 Luna', reasoningLevels: ['default', 'minimal', 'high'] };
async function fixture(page, { legacy = false, failSecondAi = false } = {}) {
  const user = { id: 'user_admin_fixture', username: 'fixture_user', display_name: 'Fixture user', disabled: false, is_admin: false, direct_access: { capabilities: [], connections: [], dashboards: [] } };
  const role = { id: 'role_admin_fixture', name: 'Reporting analysts', capabilities: ['schemii:access'], user_ids: [], connections: [{ connection_id: legacy ? 'pg_legacy' : profile.id, owner_id: legacy ? 'personal_owner' : POOL, allow_authoring: true }], dashboards: [] };
  const shared = { connected: true, catalogCheckedAt: '2026-09-23T00:00:00Z', models: [model], verifiedModels: [model], grants: [], roleGrants: [], connections: [] };
  const writes = [], accounts = [user];
  let aiAttempts = 0;
  await page.route('**/api/v1/admin/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/admin/', '');
    const method = route.request().method();
    const data = ['POST','PATCH','PUT','DELETE'].includes(method) && route.request().postData() ? route.request().postDataJSON() : null;
    const respond = (body, status=200) => route.fulfill({ status, contentType:'application/json', body: JSON.stringify(body) });
    if (method !== 'GET') writes.push({ path, method, data });
    if (path === 'resources') return respond({ connections: [profile, ...(legacy ? [{ id: 'pg_legacy', owner_id: 'personal_owner', ownership: 'user', name: 'Old private login' }] : [])], dashboards: [] });
    if (path === 'schemii-connections') return respond({ connections: [profile] });
    if (path === 'accounts' && method === 'GET') return respond(accounts);
    if (path.startsWith('accounts/') && method === 'DELETE') { accounts.splice(0); return route.fulfill({status:204}); }
    if (path.startsWith('accounts/') && method === 'PATCH') { Object.assign(user,data); role.user_ids = data.role_ids.includes(role.id) ? [user.id] : []; return respond(user); }
    if (path === 'roles' && method === 'GET') return respond([role]);
    if (path.startsWith('roles/') && method === 'PUT') { Object.assign(role,data); return respond(role); }
    if (path === 'ai/zen') return respond({ connected: false, grants: [], roleGrants: [], connections: [] });
    if (path === 'ai/shared-codex') return respond(shared);
    if (path === 'ai/shared-codex/test') return respond({connected:true,models:[model],checkedAt:'2026-09-24T00:00:00Z'});
    if (path === 'ai/shared-codex/role-grants') {
      if (method === 'PUT') { aiAttempts++; if (failSecondAi && aiAttempts === 2) return respond({detail:'Fixture connection interruption'},503); shared.roleGrants.push({...data,active:true}); return respond(data); }
      const index = shared.roleGrants.findIndex(g => g.modelId === data.modelId && g.reasoningEffort === data.reasoningEffort && g.connectionId === data.connectionId);
      shared.roleGrants.splice(index,1); return respond({deleted:true});
    }
    throw Error(`Unexpected fixture request ${method} ${path}`);
  });
  return { user, role, shared, writes };
}

test('admin navigation separates sections and opens diagnostics', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: 'Access & connections' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Roles', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Diagnostics', exact: true }).click();
  await expect(page).toHaveURL(/#diagnostics$/);
  await expect(page.getByRole('link', { name: 'Open API lens' })).toHaveAttribute('href','/api-map');
});

test('user detail shows source and stages role and direct permissions behind one save', async ({page}) => {
  const state = await fixture(page);
  await page.goto('/admin');
  await page.getByRole('button', {name:'Edit fixture_user'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading', {name:'App access without a database'})).toBeVisible();
  await page.getByRole('checkbox', {name:'Reporting analysts',exact:true}).check();
  await page.getByRole('checkbox', {name:'Schemoo — semantic models'}).check();
  expect(state.writes).toHaveLength(0);
  await page.getByRole('button', {name:'Save user',exact:true}).click();
  await expect(page).toHaveURL(/#users$/);
  expect(state.writes[0].data.role_ids).toEqual([state.role.id]);
  expect(state.writes[0].data.direct_access.capabilities).toEqual(['schemoo:access']);
  expect(state.writes[0].data.direct_access.connections).toEqual([]);
  await page.getByRole('button',{name:'Edit fixture_user'}).click();
  await expect(page.getByText('Source: Role: Reporting analysts').first()).toBeVisible();
  await page.getByRole('tab',{name:'Account',exact:true}).click();
  await page.getByRole('button',{name:'Remove account',exact:true}).click();
  await page.getByRole('dialog',{name:'Remove user account?'}).getByRole('button',{name:'Remove account',exact:true}).click();
  await expect(page.getByText('No users match your search.')).toBeVisible();
});

test('cancel and section navigation protect pending edits', async ({page}) => {
  const state = await fixture(page);
  await page.goto('/admin#users/user_admin_fixture');
  await page.getByRole('tab',{name:'Account',exact:true}).click();
  await page.getByRole('textbox',{name:'Display name'}).fill('Changed name');
  await page.getByRole('link',{name:'Databases',exact:true}).click();
  const dialog = page.getByRole('dialog',{name:'Discard unsaved changes?'});
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Display name'})).toHaveValue('Changed name');
  expect(state.writes).toHaveLength(0);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.getByRole('dialog',{name:'Discard unsaved changes?'}).getByRole('button',{name:'Discard changes'}).click();
  await expect(page).toHaveURL(/#users$/);
  expect(state.writes).toHaveLength(0);
});

test('user AI tab shows inherited policies and links to their source role', async ({page}) => {
  const state = await fixture(page);
  state.role.user_ids.push(state.user.id);
  state.shared.roleGrants.push({roleId:state.role.id,product:'schemii',connectionOwnerId:POOL,connectionId:profile.id,modelId:model.id,reasoningEffort:'high',active:true});
  await page.goto('/admin#users/user_admin_fixture');
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  const inherited = page.getByRole('region',{name:'AI access through roles'});
  await expect(inherited).toContainText('gpt-6-luna');
  await expect(inherited).toContainText('High');
  await expect(inherited).toContainText('Active · Source: role Reporting analysts');
  await inherited.getByRole('button',{name:'Edit role Reporting analysts'}).click();
  await expect(page).toHaveURL(/#roles\/role_admin_fixture$/);
});

test('saving reveals the tab containing a required invalid field', async ({page}) => {
  const state = await fixture(page);
  await page.goto('/admin#users/new');
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  await page.getByRole('button',{name:'Create user',exact:true}).click();
  await expect(page.getByRole('tab',{name:'Account',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('textbox',{name:'Display name'})).toBeVisible();
  expect(state.writes).toHaveLength(0);
});

test('database access table identifies role scopes and opens a preselected new role', async ({page}) => {
  await fixture(page);
  await page.goto('/admin#databases');
  await page.getByRole('button',{name:'Manage access'}).click();
  await expect(page.getByRole('table')).toContainText('Reporting analysts');
  await expect(page.getByRole('table')).toContainText('App tools enabled');
  await expect(page.getByRole('table')).toContainText('No AI access');
  await page.getByRole('button',{name:'Create role for this database'}).click();
  await expect(page.getByRole('checkbox',{name:'Reporting database · report_reader',exact:true})).toBeChecked();
  await expect(page.getByRole('heading',{name:'Add role',exact:true})).toBeVisible();
});

test('database actions retain database context and open role members directly', async ({page}) => {
  const state = await fixture(page);
  await page.goto(`/admin#databases/${profile.id}`);
  await page.getByRole('button',{name:'Grant direct user access'}).click();
  await page.getByRole('dialog',{name:'Choose user'}).getByRole('button',{name:'Continue'}).click();
  await expect(page.getByRole('tab',{name:'Access',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('checkbox',{name:'Reporting database · report_reader',exact:true})).toBeChecked();
  await expect(page.getByText('Unsaved changes',{exact:true})).toBeVisible();
  expect(state.writes).toHaveLength(0);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.getByRole('dialog',{name:'Discard unsaved changes?'}).getByRole('button',{name:'Discard changes'}).click();
  await page.goto(`/admin#databases/${profile.id}`);
  await page.getByRole('button',{name:'Assign a role to users'}).click();
  await page.getByRole('dialog',{name:'Assign database role'}).getByRole('button',{name:'Review role & members'}).click();
  await expect(page.getByRole('tab',{name:'Members',exact:true})).toHaveAttribute('aria-selected','true');
  expect(state.writes).toHaveLength(0);
});

test('disabled user effective access explains assignments cannot currently be used', async ({page}) => {
  const state = await fixture(page); state.user.disabled = true;
  await page.goto('/admin#users/user_admin_fixture');
  await expect(page.getByRole('region',{name:'Saved effective access'})).toContainText('Sign-in is disabled; these saved assignments are currently unusable.');
});

test('legacy grants require an explicit acknowledgment before unrelated save', async ({page}) => {
  const state = await fixture(page,{legacy:true});
  await page.goto('/admin#roles/role_admin_fixture');
  await expect(page.getByRole('heading',{name:'Inactive legacy database grants'})).toBeVisible();
  await expect(page.getByText('Old private login · inactive personal profile', {exact:true})).toBeVisible();
  await page.getByRole('textbox',{name:'Role name'}).fill('Renamed role');
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('confirm removal');
  expect(state.writes).toHaveLength(0);
  await page.getByRole('checkbox',{name:'Remove these inactive legacy grants when saving'}).check();
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page).toHaveURL(/#roles$/);
  expect(state.writes[0].data.connections).toEqual([]);
});

async function stagePolicy(page, effort) {
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  await page.getByRole('button',{name:'Add AI policy',exact:true}).click();
  await page.getByRole('combobox',{name:'App and database scope'}).selectOption({label:'Schemii · Reporting database'});
  await page.getByRole('combobox',{name:'Reasoning level'}).selectOption(effort);
  await page.getByRole('button',{name:'Stage AI policy'}).click();
}

test('AI policies stage, cancel, save exact siblings, and remove one only on save', async ({page}) => {
  const state = await fixture(page);
  await page.goto('/admin#roles/role_admin_fixture');
  await stagePolicy(page,'minimal');
  expect(state.shared.roleGrants).toHaveLength(0);
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.getByRole('dialog',{name:'Discard unsaved changes?'}).getByRole('button',{name:'Discard changes'}).click();
  await expect(page).toHaveURL(/#roles$/);
  expect(state.writes).toHaveLength(0);
  await page.getByRole('button',{name:'Edit Reporting analysts'}).click();
  await stagePolicy(page,'minimal'); await stagePolicy(page,'high');
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page).toHaveURL(/#roles$/);
  expect(state.shared.roleGrants.map(g=>g.reasoningEffort)).toEqual(['minimal','high']);
  await page.getByRole('button',{name:'Edit Reporting analysts'}).click();
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  await page.getByRole('button',{name:'Remove policy'}).first().click();
  expect(state.shared.roleGrants).toHaveLength(2);
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page).toHaveURL(/#roles$/);
  expect(state.shared.roleGrants.map(g=>g.reasoningEffort)).toEqual(['high']);
  const deletion=state.writes.find(w=>w.method==='DELETE');
  expect(deletion.data).toMatchObject({roleId:state.role.id,connectionId:profile.id,modelId:model.id,reasoningEffort:'minimal'});
  expect(deletion.data).not.toHaveProperty('active');
});

test('editing an unavailable policy preserves its values until an explicit replacement is staged', async ({page}) => {
  const state = await fixture(page);
  state.shared.roleGrants.push({roleId:state.role.id,product:'schemii',connectionOwnerId:POOL,connectionId:'removed_database',modelId:'retired_model',reasoningEffort:'high',active:false});
  await page.goto('/admin#roles/role_admin_fixture');
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  await page.getByRole('button',{name:'Edit policy',exact:true}).click();
  await expect(page.getByRole('combobox',{name:'Allowed model'})).toHaveValue('retired_model');
  await expect(page.getByRole('combobox',{name:'App and database scope'})).toHaveValue(JSON.stringify(['schemii',POOL,'removed_database']));
  await expect(page.getByRole('button',{name:'Stage policy changes'})).toBeDisabled();
  await page.getByRole('button',{name:'Cancel policy',exact:true}).click();
  await expect(page.getByText('No unsaved changes',{exact:true})).toBeVisible();
  expect(state.writes).toHaveLength(0);
  await expect(page.getByText(/retired_model/).first()).toBeVisible();
  await page.getByRole('button',{name:'Edit policy',exact:true}).click();
  await page.getByRole('combobox',{name:'App and database scope'}).selectOption({label:'Schemii · Reporting database'});
  await expect(page.getByRole('button',{name:'Stage policy changes'})).toBeDisabled();
  await expect(page.getByRole('combobox',{name:'Reasoning level'})).toHaveValue('high');
  await page.getByRole('combobox',{name:'Allowed model'}).selectOption('gpt-6-luna');
  await expect(page.getByRole('combobox',{name:'Reasoning level'})).toHaveValue('high');
  await expect(page.getByRole('button',{name:'Stage policy changes'})).toBeEnabled();
  await page.getByRole('button',{name:'Stage policy changes'}).click();
  expect(state.writes).toHaveLength(0);
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page).toHaveURL(/#roles$/);
  expect(state.shared.roleGrants).toHaveLength(1);
  expect(state.shared.roleGrants[0]).toMatchObject({modelId:'gpt-6-luna',connectionId:profile.id,reasoningEffort:'high'});
});

test('choosing a supported reasoning level immediately enables an edited policy', async ({page}) => {
  const state = await fixture(page);
  state.shared.roleGrants.push({roleId:state.role.id,product:'schemii',connectionOwnerId:POOL,connectionId:profile.id,modelId:model.id,reasoningEffort:'max',active:false});
  await page.goto('/admin#roles/role_admin_fixture');
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  await page.getByRole('button',{name:'Edit policy',exact:true}).click();
  await expect(page.getByRole('combobox',{name:'Reasoning level'})).toHaveValue('max');
  await expect(page.getByRole('button',{name:'Stage policy changes'})).toBeDisabled();
  await page.getByRole('combobox',{name:'Reasoning level'}).selectOption('high');
  await expect(page.getByRole('button',{name:'Stage policy changes'})).toBeEnabled();
  expect(state.writes).toHaveLength(0);
});

test('verifying models inside the policy composer preserves an unsaved role draft', async ({page}) => {
  const state = await fixture(page); state.shared.catalogCheckedAt = null; state.shared.verifiedModels = [];
  await page.goto('/admin#roles/role_admin_fixture');
  await page.getByRole('textbox',{name:'Role name'}).fill('Unsaved role name');
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  await page.getByRole('button',{name:'Add AI policy',exact:true}).click();
  await expect(page.getByRole('button',{name:'Stage AI policy'})).toBeDisabled();
  await page.getByRole('button',{name:'Verify models',exact:true}).click();
  await expect(page.getByRole('button',{name:'Stage AI policy'})).toBeEnabled();
  await expect(page.getByText('1 models verified. Your pending access changes are preserved.')).toBeVisible();
  await page.getByRole('tab',{name:'Permissions',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Role name'})).toHaveValue('Unsaved role name');
  expect(state.writes.map(write=>write.path)).toEqual(['ai/shared-codex/test']);
});

test('Save refuses to silently discard an open unstaged policy editor', async ({page}) => {
  const state = await fixture(page);
  await page.goto('/admin#roles/role_admin_fixture');
  await page.getByRole('tab',{name:'AI',exact:true}).click();
  await page.getByRole('button',{name:'Add AI policy',exact:true}).click();
  await page.getByRole('combobox',{name:'Reasoning level'}).selectOption('high');
  await page.getByRole('tab',{name:'Permissions',exact:true}).click();
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Stage or cancel the open AI policy');
  await expect(page.getByRole('tab',{name:'AI',exact:true})).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('combobox',{name:'Reasoning level'})).toHaveValue('high');
  expect(state.writes).toHaveLength(0);
});

test('partial AI save reports completed work and retries only remaining policy', async ({page}) => {
  const state = await fixture(page,{failSecondAi:true});
  await page.goto('/admin#roles/role_admin_fixture');
  await stagePolicy(page,'minimal'); await stagePolicy(page,'high');
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('permissions were saved');
  await expect(page.getByText(/Pending addition/)).toHaveCount(1);
  expect(state.shared.roleGrants.map(g=>g.reasoningEffort)).toEqual(['minimal']);
  await page.getByRole('button',{name:'Save role',exact:true}).click();
  await expect(page).toHaveURL(/#roles$/);
  expect(state.shared.roleGrants.map(g=>g.reasoningEffort)).toEqual(['minimal','high']);
  expect(state.writes.filter(w=>w.path.endsWith('role-grants') && w.data.reasoningEffort==='minimal')).toHaveLength(1);
});

test('a provisioned viewer can sign in, see an empty report library, and sign out', async ({ browser, request, baseURL }, testInfo) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');
  const suffix = randomUUID().replaceAll('-', '');
  const username = `qa_viewer_${suffix}`, password = `QA-${randomUUID()}`;
  const created = await request.post('/api/v1/admin/accounts', { data: { username, display_name: 'QA isolated report viewer', password, is_admin: false } });
  expect(created.ok()).toBeTruthy();
  const user = await created.json();
  let role, context;
  try {
    const granted = await request.post('/api/v1/admin/roles', { data: {
      name: `QA report viewer ${suffix}`, capabilities: ['schemer:access'], user_ids: [user.id],
    } });
    expect(granted.ok()).toBeTruthy();
    role = await granted.json();
    const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = testInfo.project.use;
    context = await browser.newContext({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch, baseURL, ignoreHTTPSErrors: true, storageState: { cookies: [], origins: [] } });
    const page = await context.newPage();
    await page.goto('/login');
    await page.getByRole('textbox', { name: 'Username', exact: true }).fill(username);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page).toHaveURL(/\/schemer$/);
    await expect(page.getByRole('heading', { name: 'Reports shared with you' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create dashboard', exact: true })).toBeHidden();
    await page.locator('.ui-product-navigation > summary').click();
    await expect(page.getByRole('link', { name: 'Administration', exact: true })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Schemii Schema design', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
    // Streamed report exports navigate a native POST form. Exercise that browser
    // request mode without fixtures: a fresh viewer session can safely log out.
    await page.getByRole('textbox', { name: 'Username', exact: true }).fill(username);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page).toHaveURL(/\/schemer$/);
    const submitted = context.waitForEvent('response', {
      predicate: response => response.url().endsWith('/api/v1/auth/logout') && response.request().method() === 'POST',
    });
    await page.evaluate(() => {
      const form = document.createElement('form');
      form.method = 'POST'; form.action = '/api/v1/auth/logout'; form.target = '_blank';
      document.body.append(form); form.submit(); form.remove();
    });
    const response = await submitted;
    expect((await response.request().allHeaders()).origin).toBe(new URL(baseURL).origin);
    expect(response.status()).toBe(200);
    expect((await context.request.get('/api/v1/auth/me')).status()).toBe(401);

  } finally {
    if (context) await context.close();
    if (role) {
      const deletedRole = await request.delete(`/api/v1/admin/roles/${role.id}`);
      expect(deletedRole.ok()).toBeTruthy();
    }
    const deletedUser = await request.delete(`/api/v1/admin/accounts/${user.id}`);
    expect(deletedUser.ok()).toBeTruthy();
  }
});
