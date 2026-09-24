import { expect, test } from '@playwright/test';

async function expectInsideVisualViewport(locator) {
  await expect.poll(() => locator.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const viewport = window.visualViewport;
    const viewportTop = viewport?.offsetTop ?? 0;
    const viewportBottom = viewportTop + (viewport?.height ?? window.innerHeight);
    const viewportWidth = viewport?.width ?? window.innerWidth;
    return Math.max(viewportTop - rect.top, rect.bottom - viewportBottom, -rect.left, rect.right - viewportWidth);
  })).toBeLessThanOrEqual(2);
}

for (const route of ['/system-map', '/api-map', '/db-map']) {
  test(`entry picker stays usable in narrow viewports on ${route}`, async ({ page }) => {
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(route);
      const trigger = page.locator('#entry-picker-trigger');
      await expect(trigger).toBeEnabled();
      await trigger.click();
      const dialog = page.locator('#entry-dialog');
      await expect(dialog).toBeVisible();
      for (const locator of [
        dialog.locator('#entry-dialog-title'),
        dialog.locator('#close-entry-dialog'),
        dialog.locator('#dialog-entry-search'),
      ]) await expectInsideVisualViewport(locator);
      const searchFontSize = await dialog.locator('#dialog-entry-search').evaluate(element => parseFloat(getComputedStyle(element).fontSize));
      expect(searchFontSize).toBeGreaterThanOrEqual(12);
      const search = dialog.locator('#dialog-entry-search');
      await search.fill('zzzz-no-matching-route');
      await expect(dialog.locator('.entry-empty')).toBeVisible();
      await search.fill('');
      await expect(dialog.locator('.entry-button').first()).toBeVisible();
      await dialog.locator('.entry-button').first().click();
      await expect(dialog).toBeHidden();
      await trigger.click();
      await search.press('Escape');
      await expect(dialog).toBeHidden();
      await trigger.click();
      await dialog.locator('#close-entry-dialog').click();
      await expect(dialog).toBeHidden();
    }
  });
}

test('entry picker stays centered on desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/system-map');
  await page.locator('#entry-picker-trigger').click();
  const placement = await page.locator('#entry-dialog').evaluate(element => {
    const rect = element.getBoundingClientRect();
    return { center: rect.top + rect.height / 2, viewportCenter: window.innerHeight / 2 };
  });
  expect(Math.abs(placement.center - placement.viewportCenter)).toBeLessThan(3);
});

test('conditional schema rows follow the selected form state', async ({ page }) => {
  const connectionsFixture = [false, true].map((credentialStored, index) => ({
    id: `pg_${String(index + 1).repeat(32)}`,
    revision: 1,
    name: credentialStored ? 'Stored credential' : 'No credential',
    host: 'localhost',
    port: 5432,
    database: 'sample',
    username: 'reader',
    sslMode: 'disable',
    connectTimeout: 10,
    credentialStored,
  }));
  await page.route('**/api/v1/connections?product=schemii', route => route.fulfill({ json: { connections: connectionsFixture } }));
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/');
  const viewDialog = page.locator('#design-view-dialog');
  await viewDialog.evaluate(dialog => dialog.showModal());
  const populateRow = viewDialog.locator('#design-view-population-row');
  await expect(populateRow).toBeHidden();
  await expect(populateRow).toHaveJSProperty('hidden', true);
  await viewDialog.locator('#design-view-kind').selectOption('materialized_view');
  await expect(populateRow).toBeVisible();
  await expect(populateRow).toHaveJSProperty('hidden', false);
  await viewDialog.locator('#design-view-kind').selectOption('view');
  await expect(populateRow).toBeHidden();
  for (const width of [320, 390, 1440]) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 740 });
    const query = await viewDialog.locator('.design-view-query').evaluate(element => {
      const textarea = element.querySelector('textarea').getBoundingClientRect();
      const helper = element.querySelector('small').getBoundingClientRect();
      return { textareaBottom: textarea.bottom, helperTop: helper.top };
    });
    expect(query.helperTop).toBeGreaterThanOrEqual(query.textareaBottom);
    await expectInsideVisualViewport(viewDialog.getByRole('button', { name: 'Create view' }));
  }
  await page.setViewportSize({ width: 320, height: 740 });
  await page.evaluate(() => {
    const style = document.documentElement.style;
    style.setProperty('--ui-type-label', '15px');
    style.setProperty('--ui-type-control', '15px');
    style.setProperty('--ui-type-helper', '14px');
  });
  const enlargedQuery = await viewDialog.locator('.design-view-query').evaluate(element => ({
    textareaBottom: element.querySelector('textarea').getBoundingClientRect().bottom,
    helperTop: element.querySelector('small').getBoundingClientRect().top,
  }));
  expect(enlargedQuery.helperTop).toBeGreaterThanOrEqual(enlargedQuery.textareaBottom);
  await expectInsideVisualViewport(viewDialog.getByRole('button', { name: 'Create view' }));
  const editorSurfaces = await page.evaluate(() => {
    const view = getComputedStyle(document.querySelector('#design-view-definition'));
    const routine = getComputedStyle(document.querySelector('.design-routine-source textarea'));
    return { viewBackground: view.backgroundColor, routineBackground: routine.backgroundColor, fontSize: parseFloat(view.fontSize) };
  });
  expect(editorSurfaces.viewBackground).toBe(editorSurfaces.routineBackground);
  expect(editorSurfaces.fontSize).toBeGreaterThanOrEqual(11);
  await viewDialog.locator('[data-close-dialog]').first().click();

  await page.getByRole('button', { name: 'PostgreSQL connections', exact: true }).first().click();
  const connections = page.locator('#connections-dialog');
  await connections.locator('#add-connection-button').click();
  const editor = page.locator('#connection-editor-dialog');
  const removalRow = editor.locator('#remove-credential-row');
  await expect(removalRow).toBeHidden();
  await expect(removalRow).toHaveJSProperty('hidden', true);
  await editor.getByRole('button', { name: 'Close connection editor' }).click();
  await connections.getByRole('button', { name: 'Edit No credential' }).click();
  await expect(removalRow).toBeHidden();
  await expect(removalRow).toHaveJSProperty('hidden', true);
  await editor.getByRole('button', { name: 'Close connection editor' }).click();
  await connections.getByRole('button', { name: 'Edit Stored credential' }).click();
  await expect(removalRow).toBeVisible();
  await expect(removalRow).toHaveJSProperty('hidden', false);
});
