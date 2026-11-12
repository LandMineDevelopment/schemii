import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const modelId = `model_${'d'.repeat(32)}`, dashboardId = `dashboard_${'e'.repeat(32)}`;
const reportAt = '2026-10-13T12:00:00Z', drillAt = '2026-10-13T12:01:00Z', refreshedAt = '2026-10-13T12:02:00Z';
const field = (column, aggregate = 'none') => ({ table: 'person', column, aggregate });

// Mounted browser module/interaction regression with a controlled mutable source
// oracle. Real PostgreSQL snapshot/cancellation and native QA remain separate.
async function mutableReport(page) {
  const model = { id: modelId, revision: 1, name: 'Freshness fixture', database: 'organization', namespace: 'public',
    connectionId: `pg_${'f'.repeat(32)}`, definition: { root: 'person', nodes: [{ id: 'person', table: 'personnel', label: 'Personnel' }], edges: [], scopes: [] }, explore: { fields: [], selections: {} } };
  const tile = { id: 'people', title: 'People by org', kind: 'aggregate', dimensions: [field('org')], measures: [field('id', 'count')],
    detailFields: [field('id'), field('name')], reportFilters: [], selections: {}, limit: 100 };
  const dashboard = { id: dashboardId, revision: 1, modelId, modelRevision: 1, name: 'Freshness report', optionalFilters: [], selections: {}, tiles: [tile] };
  let source = [[1, 'Original']], drills = 0;
  const requests = [], errors = []; page.on('pageerror', error => errors.push(error.message));
  const catalog = { tables: [{ name: 'personnel', columns: [{ name: 'id', dataType: 'integer' }, { name: 'name', dataType: 'text' }, { name: 'org', dataType: 'text' }] }], relationships: [] };
  await page.context().route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/api/v1/auth/')) { await route.fallback(); return; }
    let response = {};
    if (url.pathname === '/api/v1/schemer/dashboards') response = { dashboards: [dashboard] };
    else if (url.pathname === `/api/v1/schemer/dashboards/${dashboardId}`) response = dashboard;
    else if (url.pathname.endsWith('/context')) response = { model, catalog, permissions: { edit: false, export: true, drill: true } };
    else if (url.pathname === '/api/v1/schemoo/models') response = { models: [model] };
    else if (url.pathname === `/api/v1/schemoo/models/${modelId}`) response = model;
    else if (url.pathname === '/api/v1/schemoo/catalog') response = catalog;
    else if (url.pathname.endsWith('/executions/stream')) {
      const body = request.postDataJSON(), detail = Boolean(body.selection);
      requests.push({ path: url.pathname, body });
      const snapshotAt = detail ? (++drills === 1 ? drillAt : refreshedAt) : reportAt;
      const data = detail ? source.map(row => [...row]) : [['HQ', source.length]];
      const columns = detail ? [{ name: 'id' }, { name: 'name' }] : [{ name: 'org' }, { name: 'COUNT id' }];
      const frames = [{ type: 'start', snapshotAt, tiles: [{ tileId: tile.id, plan: { sql: 'SELECT current source data', warnings: [] } }] },
        { type: 'rows', tileId: tile.id, columns, rows: data },
        { type: 'complete', tileId: tile.id, limitReached: false, snapshotAt }, { type: 'end' }];
      await route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: frames.map(frame => JSON.stringify(frame)).join('\n') + '\n' }); return;
    } else if (url.pathname.endsWith('/export')) {
      const body = JSON.parse(new URLSearchParams(request.postData()).get('payload'));
      requests.push({ path: url.pathname, body });
      await route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'Content-Disposition': 'attachment; filename="schemer-full-results.csv"', 'X-Schemer-Snapshot': 'fresh' },
        body: 'id,name\n' + source.map(row => row.join(',')).join('\n') + '\n' }); return;
    }
    await route.fulfill({ status: 200, json: response });
  });
  return { requests, errors, change: rows => { source = rows; } };
}

test('current drill discloses report/drill times, cached reopen is query-free, Refresh and full export read newer rows', async ({ page }) => {
  const f = await mutableReport(page);
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile'); await expect(tile.locator('.tile-status')).toContainText('1 groups');
  await tile.getByRole('heading', { name: 'People by org' }).click();
  const dialog = page.getByRole('dialog', { name: 'People by org', exact: true });
  const main = dialog.locator('.expanded-chart-pane'), details = dialog.locator('.drill-pane');
  const reportTime = await main.locator('.viewer-status').textContent();
  expect(reportTime).toContain('Report request started:');
  f.change([[1, 'Original'], [2, 'Arrived after report']]);
  await main.getByRole('button', { name: 'View records for 1', exact: true }).click();
  await expect(details).toBeVisible(); await expect(details.locator('.drill-body')).toContainText('Arrived after report');
  await expect(details.locator('.drill-freshness')).toContainText('current source data when requested');
  await expect(details.locator('.drill-freshness')).toContainText('may differ from the report');
  const firstTimes = await details.locator('.drill-request-times').textContent();
  expect(firstTimes).toContain(reportTime.split(' · ')[1]); expect(firstTimes).toContain('Drill request started:');
  const expectedTimes = await page.evaluate(values => values.map(value =>
    new Date(value).toLocaleString(undefined, { timeZoneName: 'short' })), [reportAt, drillAt, refreshedAt]);
  expect(firstTimes).toContain(`Report request started: ${expectedTimes[0]}`);
  expect(firstTimes).toContain(`Drill request started: ${expectedTimes[1]}`);
  expect(f.requests.filter(request => request.body.selection)).toHaveLength(1);
  await details.getByRole('button', { name: 'Close detail rows', exact: true }).click();
  f.change([[3, 'Changed while closed']]);
  await main.getByRole('button', { name: 'View records for 1', exact: true }).click();
  await expect(details.locator('.drill-freshness')).toContainText('Earlier cached drill result');
  await expect(details.locator('.drill-request-times')).toHaveText(firstTimes);
  await expect(details.locator('.drill-body')).toContainText('Arrived after report');
  await expect(details.locator('.drill-body')).not.toContainText('Changed while closed');
  expect(f.requests.filter(request => request.body.selection)).toHaveLength(1);
  await details.getByRole('button', { name: 'Refresh detail rows', exact: true }).click();
  await expect(details.locator('.drill-body')).toContainText('Changed while closed');
  await expect(details.locator('.drill-request-times')).not.toHaveText(firstTimes);
  await expect(details.locator('.drill-request-times')).toContainText(`Drill request started: ${expectedTimes[2]}`);
  expect(f.requests.filter(request => request.body.selection)).toHaveLength(2);
  await expect(details.locator('.export-snapshot-note')).toHaveText('Full download uses a fresh snapshot.');
  f.change([[4, 'Arrived before full export']]);
  const downloadEvent = new Promise(resolve => {
    page.once('download', resolve); page.context().once('page', popup => popup.once('download', resolve));
  });
  await details.getByRole('button', { name: 'Download full results · RAW CSV', exact: true }).click();
  const download = await downloadEvent;
  expect(await readFile(await download.path(), 'utf8')).toContain('Arrived before full export');
  await expect(details.locator('.drill-body')).toContainText('Changed while closed');
  expect(f.requests.filter(request => request.path.endsWith('/export'))).toHaveLength(1);
  expect(f.requests.filter(request => !request.body.selection && request.path.endsWith('/executions/stream'))).toHaveLength(1);
  expect(f.errors).toEqual([]);
});
