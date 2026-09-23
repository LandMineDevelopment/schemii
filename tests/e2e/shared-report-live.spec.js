import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { checked, sharedReportFixture, streamedRows } from './helpers/shared-report-fixture.js';

// Real backend acceptance; one explicit 503 injection checks transient recovery.
// No target writes or retained passwords.
test.use({ trace: 'off', video: 'off', screenshot: 'off', actionTimeout: 15_000 });

test('granted viewers retain deep links, isolate filters and rows, recover edits, and honor revoked permissions', async ({ browser, request, baseURL }, testInfo) => {
  test.setTimeout(180_000);
  const status = await checked(await request.get('/api/v1/auth/status'));
  expect(status.enabled, 'This acceptance test requires the authenticated launcher stack').toBe(true);
  const fixture = await sharedReportFixture(request);
  const contexts = [];
  const path = `/api/v1/schemer/dashboards/${fixture.dashboard.id}`;
  const deepLink = `/schemer?dashboard=${fixture.dashboard.id}`;
  const body = amount => ({ expectedRevision: fixture.dashboard.revision,
    selections: { minimum: { alternativeId: 'at_least', values: { amount } } } });
  const selection = { dimensions: [{ table: 'sales', column: 'region', value: 'east' }], measureIndex: 0 };
  let bodyFailure;
  try {
    const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = testInfo.project.use;
    for (const user of fixture.users) {
      const context = await browser.newContext({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch,
        baseURL, ignoreHTTPSErrors: true, extraHTTPHeaders: { Origin: new URL(baseURL).origin },
        storageState: { cookies: [], origins: [] } });
      contexts.push(context);
      context.setDefaultTimeout(15_000);
      const page = await context.newPage();
      await page.goto(deepLink);
      await expect(page).toHaveURL(/\/login\?next=/);
      await page.getByRole('textbox', { name: 'Username', exact: true }).fill(user.username);
      await page.getByLabel('Password', { exact: true }).fill(user.password);
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await expect(page).toHaveURL(new URL(deepLink, baseURL).href);
      await expect(page.locator('.analytics-tile .tile-status')).toContainText('1 group');
      await expect(page.getByRole('button', { name: 'Rename dashboard', exact: true })).toBeHidden();
      await expect(page.getByRole('button', { name: 'Edit Sales by region', exact: true })).toBeHidden();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    const [east, west] = contexts;
    const [eastPage] = east.pages(), [westPage] = west.pages();
    // Two real sessions execute concurrently with independent RLS identities and filters.
    const [eastRows, westRows] = await Promise.all([
      streamedRows(east.request, `${path}/executions/stream`, body(200)),
      streamedRows(west.request, `${path}/executions/stream`, body(0)),
    ]);
    expect(eastRows).toEqual([['east', 250]]);
    expect(westRows).toEqual([['west', 800]]);
    expect((await east.request.get(`/api/v1/connections/${fixture.source.id}`)).status()).toBe(403);
    expect((await east.request.get(`/api/v1/schemoo/models/${fixture.model.id}`)).status()).toBe(403);
    expect((await east.request.put(path, { data: { ...fixture.dashboardBody, expectedRevision: 1 } })).status()).toBe(403);
    const context = await checked(await east.request.get(`${path}/context`));
    expect(JSON.stringify(context.catalog)).not.toContain('"secret"');
    expect((await east.request.post(`${path}/tiles/sales/export`, { data: body(0) })).status()).toBe(403);
    expect((await east.request.post(`${path}/tiles/sales/executions/stream`, { data: { ...body(0), selection } })).status()).toBe(403);

    // Apply a filter in the real viewer; neither the saved report nor the other viewer changes.
    await eastPage.getByRole('button', { name: 'Filters', exact: true }).click();
    await eastPage.getByRole('textbox', { name: 'Minimum sale: Minimum amount', exact: true }).fill('200');
    await eastPage.getByRole('button', { name: 'Apply filters', exact: true }).click();
    await expect(eastPage.locator('.analytics-tile .bar-value-row')).toContainText('250');
    await expect(westPage.locator('.analytics-tile .bar-value-row')).toContainText('800');
    const saved = await checked(await request.get(path));
    expect(saved.selections.minimum.values.amount).toBe(0);
    await eastPage.locator('.analytics-tile').getByRole('heading').click();
    let expanded = eastPage.getByRole('dialog', { name: 'Sales by region', exact: true });
    await expect(expanded.getByRole('button', { name: 'Download full results', exact: true })).toBeHidden();
    await expect(expanded.getByRole('button', { name: /View records/ })).toHaveCount(0);
    await expanded.getByRole('button', { name: 'Close expanded tile' }).click();

    const role = fixture.roles[0];
    role.body.dashboards[0].can_export = true;
    role.body.dashboards[0].can_drill = true;
    await checked(await request.put(`/api/v1/admin/roles/${role.id}`, { data: role.body }));
    await eastPage.reload();
    await expect(eastPage.locator('.analytics-tile .tile-status')).toContainText('1 group');
    await eastPage.locator('.analytics-tile').getByRole('heading').click();
    expanded = eastPage.getByRole('dialog', { name: 'Sales by region', exact: true });
    const downloadPromise = new Promise(resolve => {
      eastPage.once('download', resolve);
      east.once('page', popup => popup.once('download', resolve));
    });
    await expanded.getByRole('button', { name: 'Download full results', exact: true }).click();
    let downloadTimeout;
    const download = await Promise.race([downloadPromise, new Promise((_, reject) => {
      downloadTimeout = setTimeout(() => reject(new Error('No browser download received within 15 seconds')), 15_000);
    })]).finally(() => clearTimeout(downloadTimeout));
    expect(await download.failure()).toBeNull();
    const csv = await readFile(await download.path(), 'utf8');
    expect(csv).toContain('east');
    expect(csv).toContain('350');
    expect(csv).not.toContain('west');
    expect(csv).not.toContain('secret');
    await expanded.getByRole('button', { name: /east: 350.*View records/ }).click();
    await expect(expanded.locator('.drill-body')).toContainText('100');
    await expect(expanded.locator('.drill-body')).toContainText('250');
    await expect(expanded.locator('.drill-body')).not.toContainText('west');
    await expanded.getByRole('button', { name: 'Close expanded tile' }).click();

    // Permission reload reset local filters; restore a viewer-only selection so
    // the author update below proves Refresh preserves it against saved zero.
    await eastPage.getByRole('button', { name: 'Filters', exact: true }).click();
    await eastPage.getByRole('textbox', { name: 'Minimum sale: Minimum amount', exact: true }).fill('200');
    await eastPage.getByRole('button', { name: 'Apply filters', exact: true }).click();
    await expect(eastPage.locator('.analytics-tile .bar-value-row')).toContainText('250');

    // A genuine author update must be recoverable by the existing viewer's Refresh action.
    await checked(await request.put(path, { data: { ...fixture.dashboardBody, expectedRevision: saved.revision,
      tiles: [{ ...fixture.dashboardBody.tiles[0], title: 'Updated sales by region' }] } }));
    await eastPage.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
    await expect(eastPage.locator('.analytics-tile').getByRole('heading', { name: 'Updated sales by region', exact: true })).toBeVisible();
    await expect(eastPage.locator('.analytics-tile .tile-status')).toContainText('1 group');
    await expect(eastPage.locator('.tile-error')).toHaveCount(0);
    await expect(eastPage.locator('.analytics-tile .bar-value-row')).toContainText('250');
    await expect(eastPage.getByRole('textbox', { name: 'Minimum sale: Minimum amount', exact: true })).toHaveValue('200');
    await expect(eastPage.getByRole('button', { name: 'Refresh dashboard', exact: true })).toBeEnabled();

    // A transient metadata failure must preserve the last successful view.
    // Only this GET is fault-injected; all report queries and permissions are real.
    const temporaryFailure = route => route.fulfill({ status: 503, json: {
      error: { code: 'metadata_unavailable', message: 'Temporary report metadata outage' },
    } });
    const dashboardURL = new URL(path, baseURL).href;
    await eastPage.route(dashboardURL, temporaryFailure);
    await eastPage.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
    await expect(eastPage.locator('#notice')).toContainText('Temporary report metadata outage');
    await expect(eastPage.locator('.analytics-tile .bar-value-row')).toContainText('250');
    await expect(eastPage.locator('.analytics-tile .tile-status')).toContainText('1 group');
    await eastPage.unroute(dashboardURL, temporaryFailure);
    await eastPage.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
    await expect(eastPage.locator('#notice')).not.toContainText('Temporary report metadata outage');
    await expect(eastPage.locator('.analytics-tile .tile-status')).toContainText('1 group');
    await expect(eastPage.getByRole('button', { name: 'Refresh dashboard', exact: true })).toBeEnabled();

    await checked(await request.delete(`/api/v1/admin/roles/${role.id}`), 204);
    expect((await east.request.get(path)).status()).toBe(403);
    expect((await east.request.post(`${path}/tiles/sales/export`, { data: { ...body(0), expectedRevision: 2 } })).status()).toBe(403);
    await eastPage.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
    await expect(eastPage.locator('.analytics-tile')).toHaveCount(0);
    // Revoking east must not disturb west's existing session or grant.
    expect(await streamedRows(west.request, `${path}/executions/stream`, { ...body(0), expectedRevision: 2 })).toEqual([['west', 800]]);
  } catch (error) {
    bodyFailure = error;
  } finally {
    const failures = bodyFailure ? [bodyFailure] : [];
    // Metadata cleanup must also run when browser teardown fails or times out.
    try {
      await fixture.cleanup();
    } catch (error) {
      failures.push(error);
    }
    try {
      let closeTimeout;
      await Promise.race([Promise.all(contexts.map(context => context.close())), new Promise((_, reject) => {
        closeTimeout = setTimeout(() => reject(new Error('Browser context teardown exceeded 10 seconds after fixture cleanup')), 10_000);
      })]).finally(() => clearTimeout(closeTimeout));
    } catch (error) {
      failures.push(error);
    }
    if (failures.length === 1) throw failures[0];
    if (failures.length) throw new AggregateError(failures, 'Shared-report acceptance and cleanup failures');
  }
});
