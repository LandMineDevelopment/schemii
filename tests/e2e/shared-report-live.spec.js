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
      await expect(page.getByRole('button', { name: 'Open dashboard assistant', exact: true })).toBeEnabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const originalViewport = page.viewportSize();
      await page.setViewportSize({ width: 390, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.setViewportSize(originalViewport);
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
    await expect(expanded.getByRole('button', { name: 'Download full results · RAW CSV', exact: true })).toBeHidden();
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
    await expanded.getByRole('button', { name: 'Download full results · RAW CSV', exact: true }).click();
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
    await expect(eastPage.getByRole('button', { name: 'Open dashboard assistant', exact: true })).toBeDisabled();
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


// The coordinator provisions this schema and scoped login on a disposable source
// server. The suite never grants privileges or modifies launcher/user schemas.
test('managed developer profile keeps its exact identity through both HTTPS origins and revocation', async ({ browser, request, baseURL }, testInfo) => {
  test.skip(!process.env.SCHEMII_E2E_MANAGED_WRITER_FILE,
    'Requires an explicitly provisioned disposable managed-writer fixture');
  test.setTimeout(180_000);
  const { stat } = await import('node:fs/promises');
  const { randomUUID } = await import('node:crypto');
  const fixtureFile = process.env.SCHEMII_E2E_MANAGED_WRITER_FILE;
  expect((await stat(fixtureFile)).mode & 0o077, 'fixture credentials must remain private').toBe(0);
  const fixture = JSON.parse(await readFile(fixtureFile, 'utf8'));
  expect(fixture.namespace).toMatch(/^qa_managed_writer_[a-f0-9]{16,32}$/);
  for (const value of [fixture.table, fixture.allowedColumn, fixture.deniedColumn]) expect(value).toMatch(/^[a-z][a-z0-9_]*$/);
  const suffix = randomUUID().replaceAll('-', '');
  const credentials = { username: `qa_writer_${suffix}`, password: `QA-${randomUUID()}` };
  const contexts = [], resources = [], failures = [];
  let profile, user, role, roleBody;
  try {
    profile = await checked(await request.post('/api/v1/admin/schemii-connections', { data: {
      ...fixture.connection, name: `QA scoped developer ${suffix}`,
    } }), 201);
    expect(JSON.stringify(profile)).not.toContain(fixture.connection.password);
    user = await checked(await request.post('/api/v1/admin/accounts', { data: {
      ...credentials, display_name: 'QA scoped developer', is_admin: false,
    } }), 201);
    roleBody = { name: `QA developer ${suffix}`, capabilities: ['schemii:access'],
      user_ids: [user.id], connections: [{ connection_id: profile.id, owner_id: profile.ownerId,
        allow_authoring: true }], dashboards: [] };
    role = await checked(await request.post('/api/v1/admin/roles', { data: roleBody }), 201);
    const origins = [...new Set([new URL(baseURL).origin, 'https://omarchy.taile4f57f.ts.net'])];
    for (const origin of origins) {
      const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = testInfo.project.use;
      const context = await browser.newContext({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch,
        baseURL: origin, ignoreHTTPSErrors: true, extraHTTPHeaders: { Origin: origin },
        storageState: { cookies: [], origins: [] } });
      contexts.push(context);
      const page = await context.newPage();
      await page.goto('/login');
      await page.getByRole('textbox', { name: 'Username', exact: true }).fill(credentials.username);
      await page.getByLabel('Password', { exact: true }).fill(credentials.password);
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await expect(page).not.toHaveURL(/\/login/);
      const visible = await checked(await context.request.get('/api/v1/connections'));
      expect(visible.connections.map(item => item.id)).toEqual([profile.id]);
      expect(JSON.stringify(visible)).not.toContain(fixture.connection.password);
      expect((await context.request.get('/api/v1/schemoo/models')).status()).toBe(403);
      expect((await context.request.get('/api/v1/schemer/dashboards')).status()).toBe(403);
      const importedResponse = await context.request.post('/api/v1/schemii/workspaces/postgres', {
        data: { connectionId: profile.id, namespace: fixture.namespace },
      });
      expect([200, 201]).toContain(importedResponse.status());
      const workspace = (await importedResponse.json()).workspace;
      const owned = { context, workspace, session: null, executions: [] };
      resources.push(owned);
      expect(workspace.ownerId).toBe(user.id);
      expect(workspace.connectionOwnerId).toBe(profile.ownerId);
      await page.goto(`/?workspace=${workspace.id}&layer=sql`);
      await expect(page.getByRole('heading', { name: 'SQL Console' })).toBeVisible();
      const editor = page.getByRole('textbox', { name: 'Unsaved SQL draft' });
      await editor.fill('SELECT current_user::text AS exact_database_identity;');
      const response = page.waitForResponse(item => item.request().method() === 'POST' && /\/console\/executions$/.test(new URL(item.url()).pathname));
      await page.getByRole('button', { name: 'Run current statement', exact: true }).click();
      owned.executions.push((await (await response).json()).id);
      await expect(page.locator('.sql-result-card tbody')).toContainText(fixture.connection.username);
      const settings = await checked(await context.request.get('/api/v1/schemii/console/settings'));
      const base = `/api/v1/schemii/workspaces/${workspace.id}/console/sessions`;
      const session = await checked(await context.request.post(base, { data: {
        consoleId: `con_${randomUUID().replaceAll('-', '')}`, expectedWorkspaceRevision: workspace.revision,
        expectedSettingsRevision: settings.revision,
      } }), 201);
      owned.session = `${base}/${session.id}`;
      const run = async sql => {
        const receipt = await checked(await context.request.post(`${owned.session}/executions`, {
          data: { sql, commitMode: 'each_statement' },
        }), 201);
        let execution;
        await expect.poll(async () => {
          execution = await checked(await context.request.get(`${owned.session}/executions/${receipt.id}`));
          return ['succeeded', 'failed', 'cancelled', 'uncertain'].includes(execution.status);
        }).toBe(true);
        return execution;
      };
      const table = `"${fixture.namespace}"."${fixture.table}"`;
      expect((await run(`UPDATE ${table} SET "${fixture.allowedColumn}"="${fixture.allowedColumn}" WHERE false`)).status).toBe('succeeded');
      for (const sql of [
        `SELECT "${fixture.deniedColumn}" FROM ${table}`,
        `UPDATE ${table} SET "${fixture.deniedColumn}"="${fixture.deniedColumn}" WHERE false`,
        `ALTER TABLE ${table} DROP COLUMN IF EXISTS qa_absent_${suffix}`,
      ]) {
        const denied = await run(sql);
        expect(denied.status).toBe('failed');
        expect(denied.sqlstate).toBe('42501');
      }
    }
    await checked(await request.put(`/api/v1/admin/roles/${role.id}`, { data: { ...roleBody, connections: [] } }));
    for (const { context, session, workspace } of resources) {
      const denied = await context.request.post(`${session}/executions`, { data: { sql: 'SELECT current_user', commitMode: 'each_statement' } });
      expect(denied.ok()).toBe(false);
      expect((await context.request.get('/api/v1/connections')).ok()).toBe(true);
      const visible = await checked(await context.request.get('/api/v1/connections'));
      expect(visible.connections).toEqual([]);
      expect((await context.request.post(`/api/v1/schemii/workspaces/${workspace.id}/console/sessions`, { data: {
        consoleId: `con_${randomUUID().replaceAll('-', '')}`, expectedWorkspaceRevision: workspace.revision,
        expectedSettingsRevision: 1,
      } })).ok()).toBe(false);
    }
  } catch (error) { failures.push(error); }
  finally {
    const cleanup = async action => { try { await action(); } catch (error) { failures.push(error); } };
    // Restore only this test's grant for owner-scoped resource deletion.
    if (role) await cleanup(async () => checked(await request.put(`/api/v1/admin/roles/${role.id}`, { data: roleBody })));
    for (const { context, session, workspace, executions } of resources) {
      if (session) await cleanup(async () => {
        const response = await context.request.delete(session);
        expect(response.ok() || [404, 410].includes(response.status())).toBe(true);
      });
      for (const execution of executions) await cleanup(async () => {
        const base = `/api/v1/common/query-executions/${execution}`;
        const response = await context.request.get(base);
        if ([404, 410].includes(response.status())) return;
        const receipt = await checked(response);
        await context.request.delete(base);
        for (const result of receipt.results || []) await checked(await context.request.delete(`${base}/results/${result.id}`), 204);
      });
    }
    const workspaces = new Map(resources.map(resource => [resource.workspace.id, resource]));
    for (const { context, workspace } of workspaces.values()) {
      await cleanup(async () => checked(await context.request.delete(`/api/v1/schemii/workspaces/${workspace.id}?expectedRevision=${workspace.revision}`), 204));
    }
    if (role) await cleanup(async () => checked(await request.delete(`/api/v1/admin/roles/${role.id}`), 204));
    if (user) await cleanup(async () => checked(await request.patch(`/api/v1/admin/accounts/${user.id}`, { data: { disabled: true } })));
    if (profile) await cleanup(async () => checked(await request.delete(`/api/v1/admin/schemii-connections/${profile.id}?expectedRevision=${profile.revision}`), 204));
    for (const context of contexts) await cleanup(() => context.close());
    if (failures.length === 1) throw failures[0];
    if (failures.length) throw new AggregateError(failures, 'Managed developer acceptance and owned cleanup failed');
  }
});
