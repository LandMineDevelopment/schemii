import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

// This test submits an ephemeral password: never retain a trace of form input/API bodies.
test.use({ trace: 'off', video: 'off' });

test('system diagnostics live in Administration, not the Schemii Help menu', async ({ page }) => {
  await page.goto('/');
  const help = page.locator('summary[aria-label="Help"]');
  await help.click();
  const menu = help.locator('..');
  await expect(menu.getByRole('button', { name: 'Show introduction' })).toBeVisible();
  await expect(menu.getByRole('link')).toHaveCount(0);
  await expect(menu.getByRole('button', { name: /Restore examples|Shut down Schemii/ })).toHaveCount(0);

  await page.goto('/admin');
  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible();
  await page.getByRole('link', { name: 'Jump to system diagnostics' }).click();
  const diagnostics = page.getByRole('region', { name: 'System diagnostics' });
  await expect(diagnostics).toBeInViewport();
  await expect(diagnostics.getByRole('link', { name: 'Open live system map' })).toHaveAttribute('href', '/system-map');
  await expect(diagnostics.getByRole('link', { name: 'Open API lens' })).toHaveAttribute('href', '/api-map');
  await expect(diagnostics.getByRole('link', { name: 'Open database lens' })).toHaveAttribute('href', '/db-map');
  await diagnostics.getByRole('link', { name: 'Open API lens' }).click();
  await expect(page).toHaveURL(/\/api-map$/);
});

test('role editor explains that app connection use does not grant PostgreSQL writes', async ({ page, request }) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');
  const resources = await (await request.get('/api/v1/admin/resources')).json();
  test.skip(!resources.connections.some(connection => connection.ownership === 'schemii'), 'A Schemii-owned connection is needed to display connection grants.');
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Create role' }).click();
  const roleEditor = page.getByRole('dialog', { name: 'Create role' });
  await roleEditor.locator('.account-grant').first().getByRole('checkbox').first().check();
  await expect(roleEditor.getByRole('checkbox', { name: 'Use in Schemii and Schemoo tools or Schemer editing' })).toBeVisible();
  await expect(roleEditor).toContainText('Enable this even for read-only PostgreSQL profiles.');
  await expect(roleEditor).toContainText('PostgreSQL still controls visible rows, columns, and write privileges.');
});

test('Administration manages Schemii-owned accounts without exposing their password', async ({ page, request }) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');
  const profile = { id: `pg_${'a'.repeat(32)}`, ownership: 'schemii', ownerId: 'user_schemii_connection_pool', revision: 1,
    name: 'East reporting', host: 'db.example.internal', port: 5432, database: 'organization', username: 'report_east',
    sslMode: 'verify-full', connectTimeout: 10, credentialStored: true };
  const password = 'fixture-only-not-real';
  let saved = null;
  await page.route('**/api/v1/admin/schemii-connections**', async route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const respond = (value, responseStatus = 200) => route.fulfill({ status: responseStatus, contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname.endsWith('/schemii-connections') && method === 'GET') return respond({ connections: saved ? [saved] : [] });
    if (url.pathname.endsWith('/schemii-connections') && method === 'POST') {
      const body = route.request().postDataJSON();
      expect(body.password).toBe(password);
      saved = { ...profile, ...body, password: undefined };
      return respond(saved, 201);
    }
    if (url.pathname.endsWith('/test') && method === 'POST') return respond({ ok: true, database: 'organization', serverVersion: 'fixture' });
    if (method === 'PATCH') {
      const body = route.request().postDataJSON();
      expect(body.expectedRevision).toBe(1);
      saved = { ...saved, name: body.name, revision: 2 };
      return respond(saved);
    }
    if (method === 'DELETE') {
      expect(url.searchParams.get('expectedRevision')).toBe('2');
      saved = null;
      return route.fulfill({ status: 204 });
    }
    throw new Error(`Unexpected managed-profile request: ${method} ${url.pathname}`);
  });
  await page.goto('/admin');
  const managed = page.getByRole('region', { name: 'Schemii-owned read-only database accounts' });
  await expect(managed).toContainText('No Schemii-owned accounts yet');
  await managed.getByRole('button', { name: 'Add Schemii-owned account' }).click();
  const editor = page.getByRole('dialog', { name: 'Add Schemii-owned account' });
  await editor.getByRole('textbox', { name: 'Account name' }).fill('East reporting');
  await editor.getByRole('textbox', { name: 'PostgreSQL host' }).fill('db.example.internal');
  await editor.getByRole('textbox', { name: 'Database', exact: true }).fill('organization');
  await editor.getByRole('textbox', { name: 'PostgreSQL username' }).fill('report_east');
  await editor.getByLabel('PostgreSQL password').fill(password);
  await editor.getByRole('button', { name: 'Add account' }).click();
  await expect(managed.getByText('East reporting')).toBeVisible();
  await expect(managed).not.toContainText(password);
  await managed.getByRole('button', { name: 'Test connection' }).click();
  await expect(managed).toContainText('Connected to organization as the saved PostgreSQL account.');
  await managed.getByRole('button', { name: 'Edit' }).click();
  const update = page.getByRole('dialog', { name: 'Edit Schemii-owned account' });
  await expect(update.getByLabel('Replace password (leave blank to keep)')).toBeEmpty();
  await update.getByRole('textbox', { name: 'Account name' }).fill('East reporting v2');
  await update.getByRole('button', { name: 'Save account' }).click();
  await expect(managed.getByText('East reporting v2')).toBeVisible();
  await managed.getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('dialog', { name: 'Delete Schemii-owned account?' }).getByRole('button', { name: 'Delete account' }).click();
  await expect(managed).toContainText('No Schemii-owned accounts yet');
});

test('role editor offers Schemii-owned profiles but only lists legacy personal grants as inactive', async ({ page, request }) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');
  const managed = { id: `pg_${'b'.repeat(32)}`, owner_id: 'user_schemii_connection_pool', ownership: 'schemii', name: 'East shared', database: 'organization', username: 'report_east' };
  const personal = { id: `pg_${'c'.repeat(32)}`, owner_id: 'user_personal', ownership: 'user', name: 'Personal login', database: 'organization', username: 'private_writer' };
  const role = { id: 'role_legacy_fixture', name: 'Legacy role', capabilities: ['schemii:access'], user_ids: [],
    connections: [{ connection_id: personal.id, owner_id: personal.owner_id, allow_authoring: true }], dashboards: [] };
  let savedRole = null;
  await page.route('**/api/v1/admin/resources', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ connections: [managed, personal], dashboards: [] }) }));
  await page.route('**/api/v1/admin/roles**', route => {
    if (route.request().method() === 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([role]) });
    savedRole = route.request().postDataJSON();
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...role, ...savedRole }) });
  });
  await page.goto('/admin');
  await page.getByRole('region', { name: 'Roles' }).getByRole('button', { name: 'Create role' }).click();
  const create = page.getByRole('dialog', { name: 'Create role' });
  await expect(create.getByRole('checkbox', { name: /Schemii-owned read-only · East shared/ })).toBeVisible();
  await expect(create).not.toContainText('Personal login');
  await create.getByRole('button', { name: 'Cancel' }).click();
  await page.getByRole('region', { name: 'Roles' }).getByRole('button', { name: 'Edit' }).click();
  const edit = page.getByRole('dialog', { name: 'Edit role' });
  await expect(edit).toContainText('Inactive legacy user-owned grants');
  await expect(edit).toContainText(personal.id);
  await expect(edit).not.toContainText('Personal login');
  await expect(edit.getByRole('checkbox', { name: new RegExp(personal.id) })).toHaveCount(0);
  await edit.getByRole('button', { name: 'Save role' }).click();
  await expect.poll(() => savedRole).not.toBeNull();
  expect(savedRole.connections).toEqual([]);
});

test('a provisioned viewer can sign in, see an empty report library, and sign out', async ({ browser, request, baseURL }, testInfo) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');
  const suffix = randomUUID().replaceAll('-', '');
  const username = `qa_viewer_${suffix}`, password = `QA-${randomUUID()}`;
  const created = await request.post('/api/v1/admin/accounts', { data: { username, display_name: 'QA isolated report viewer', password, is_admin: false } });
  expect(created.ok()).toBeTruthy();
  const user = await created.json();
  const granted = await request.post('/api/v1/admin/roles', { data: {
    name: `QA report viewer ${suffix}`, capabilities: ['schemer:access'], user_ids: [user.id],
  } });
  expect(granted.ok()).toBeTruthy();
  const role = await granted.json();
  const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = testInfo.project.use;
  const context = await browser.newContext({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch, baseURL, ignoreHTTPSErrors: true, storageState: { cookies: [], origins: [] } });
  try {
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
    await context.close();
    // Accounts are retained for auditing; only this test's newly created account is disabled.
    const cleanup = await request.patch(`/api/v1/admin/accounts/${user.id}`, { data: { disabled: true } });
    expect(cleanup.ok()).toBeTruthy();
    const deletedRole = await request.delete(`/api/v1/admin/roles/${role.id}`);
    expect(deletedRole.ok()).toBeTruthy();
  }
});
