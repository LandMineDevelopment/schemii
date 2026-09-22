import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

// This test submits an ephemeral password: never retain a trace of form input/API bodies.
test.use({ trace: 'off', video: 'off' });

test('a provisioned viewer can sign in, see an empty report library, and sign out', async ({ browser, request, baseURL }, testInfo) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');
  const suffix = randomUUID().replaceAll('-', '');
  const username = `qa_viewer_${suffix}`, password = `QA-${randomUUID()}`;
  const created = await request.post('/api/v1/admin/accounts', { data: { username, display_name: 'QA isolated report viewer', password, is_admin: false } });
  expect(created.ok()).toBeTruthy();
  const user = await created.json();
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
  } finally {
    await context.close();
    // Accounts are retained for auditing; only this test's newly created account is disabled.
    const cleanup = await request.patch(`/api/v1/admin/accounts/${user.id}`, { data: { disabled: true } });
    expect(cleanup.ok()).toBeTruthy();
  }
});
