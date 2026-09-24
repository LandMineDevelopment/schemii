import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';
test.use({ trace: 'off', video: 'off' });

test('account navigation fits narrow screens and keeps every destination reachable', async ({ page, request }) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    for (const path of ['/account', '/admin']) {
      await page.goto(path);
      const desktop = page.locator('#account-navigation');
      await expect(desktop.locator('a[href="/account"]')).toHaveCount(1);

      const trigger = page.locator('#account-mobile-navigation summary');
      const surface = page.getByRole('navigation', { name: 'Schemii applications' });
      await expect(trigger).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const brand = await page.locator('.accounts-header .ui-brand').boundingBox();
      const menu = await trigger.boundingBox();
      expect(brand.x + brand.width).toBeLessThan(menu.x);

      await trigger.focus();
      await page.keyboard.press('Enter');
      await expect(surface).toBeVisible();
      const desktopHrefs = await desktop.locator('a').evaluateAll(links => links.map(link => link.getAttribute('href')));
      const menuHrefs = await surface.locator('a').evaluateAll(links => links.map(link => link.getAttribute('href')));
      expect(menuHrefs).toEqual(desktopHrefs);
      await expect(surface.getByRole('link', { name: 'Account', exact: true })).toBeVisible();
      await expect(surface.getByRole('link', { name: 'Administration', exact: true })).toBeVisible();
      const signOut = surface.getByRole('button', { name: 'Sign out', exact: true });
      await expect(signOut).toBeVisible();
      expect((await signOut.boundingBox()).height).toBeGreaterThanOrEqual(44);
      const bounds = await surface.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(7);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width - 7);
      await page.keyboard.press('Tab');
      await expect(surface.locator('a').first()).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(surface).toBeHidden();
      await expect(trigger).toBeFocused();
    }

    await page.locator('#account-mobile-navigation summary').click();
    await page.getByRole('navigation', { name: 'Schemii applications' }).getByRole('link', { name: 'Account', exact: true }).click();
    await expect(page).toHaveURL(/\/account$/);
    await page.locator('#account-mobile-navigation summary').click();
    await page.getByRole('navigation', { name: 'Schemii applications' }).getByRole('link', { name: 'Administration', exact: true }).click();
    await expect(page).toHaveURL(/\/admin$/);
  }
});

test('sign-in explains invalid credentials and distinguishes service failures', async ({ page, request }) => {
  const status = await (await request.get('/api/v1/auth/status')).json();
  test.skip(!status.enabled, 'Account authentication is disabled for this installation.');
  const username = `qa_recovery_${randomUUID().replaceAll('-', '')}`;
  const password = `QA-${randomUUID()}`;
  const created = await request.post('/api/v1/admin/accounts', { data: {
    username, display_name: 'QA sign-in recovery', password, is_admin: false,
  } });
  expect(created.ok()).toBeTruthy();
  const user = await created.json();
  try {
    await page.context().clearCookies();
    await page.goto('/login');
    const usernameField = page.getByRole('textbox', { name: 'Username', exact: true });
    const passwordField = page.getByLabel('Password', { exact: true });
    const submit = page.getByRole('button', { name: 'Sign in', exact: true });
    const error = page.getByRole('alert');

    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await usernameField.fill(`missing_${randomUUID().slice(0, 8)}`);
      await passwordField.fill('nonempty-password');
      await submit.click();
      await expect(error).toHaveText('Incorrect username or password. Check both fields and try again.');
      await expect(error).toBeInViewport();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }

    await page.route('**/api/v1/auth/login', route => route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'service_unavailable', message: 'The request could not be completed', retryable: true } }),
    }));
    await submit.click();
    await expect(error).toHaveText('The sign-in service is unavailable. Try again shortly.');
    await page.unroute('**/api/v1/auth/login');

    await page.route('**/api/v1/auth/login', route => route.abort('failed'));
    await submit.click();
    await expect(error).toHaveText('Could not reach the sign-in service. Check your connection and try again.');
    await page.unroute('**/api/v1/auth/login');

    await usernameField.fill(username);
    await passwordField.fill(password);
    await submit.click();
    await expect(page).toHaveURL(/\/account$/);
  } finally {
    const disabled = await request.patch(`/api/v1/admin/accounts/${user.id}`, { data: { disabled: true } });
    expect(disabled.ok()).toBeTruthy();
  }
});
