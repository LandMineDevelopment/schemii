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
  test.skip(!resources.connections.length, 'A saved connection is needed to display connection grants.');
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Create role' }).click();
  const roleEditor = page.getByRole('dialog', { name: 'Create role' });
  await roleEditor.locator('.account-grant').first().getByRole('checkbox').first().check();
  await expect(roleEditor.getByRole('checkbox', { name: 'Use in Schemii and Schemoo tools or Schemer editing' })).toBeVisible();
  await expect(roleEditor).toContainText('Enable this even for read-only PostgreSQL profiles.');
  await expect(roleEditor).toContainText('PostgreSQL still controls visible rows, columns, and write privileges.');
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
