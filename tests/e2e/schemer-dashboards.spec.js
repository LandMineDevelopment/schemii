import { expect, test } from '@playwright/test';

const modelId = `model_${'a'.repeat(32)}`, dashboardId = `dashboard_${'b'.repeat(32)}`;
const field = (column, aggregate = 'none') => ({ table: 'person', column, aggregate });
function fixtures() {
  const model = { id: modelId, revision: 1, name: 'Workforce', database: 'organization', namespace: 'public', connectionId: `pg_${'c'.repeat(32)}`,
    definition: { root: 'person', nodes: [{ id: 'person', table: 'personnel', label: 'Personnel' }], edges: [], scopes: [{ id: 'organization', label: 'Organization', kind: 'required', alternatives: [{ id: 'choose', label: 'Choose organization', inputs: [{ id: 'org', label: 'Root organization', type: 'text' }], conditions: [{ table: 'person', column: 'org', operator: 'eq', parameterId: 'org' }] }] }] }, explore: { fields: [], selections: {} } };
  const tile = { id: 'positions', title: 'Positions by org', kind: 'bar', dimensions: [field('org')], measures: [field('id', 'count')], detailFields: [field('id'), field('name')], reportFilters: [], selections: {}, limit: 2 };
  const dashboard = { id: dashboardId, revision: 1, modelId, modelRevision: 1, name: 'Workforce overview', optionalFilters: [], selections: { organization: { alternativeId: 'choose', values: { org: 'HQ' } } }, tiles: [tile] };
  return { model, dashboard, catalog: { tables: [{ name: 'personnel', columns: [{ name: 'id', dataType: 'integer' }, { name: 'name', dataType: 'text' }, { name: 'org', dataType: 'text' }] }], relationships: [] } };
}
async function mock(page, { emptySlicers = false, chartTypes = false, manyBars = false, aggregationWarning = false, manyRows = false, modelRevision = 1, optionalFilter = false } = {}) {
  const fixture = fixtures();
  fixture.model.revision = modelRevision;
  if (optionalFilter) fixture.model.definition.scopes.push({ id: 'region', label: 'Region', kind: 'conditional', requirement: 'optional', alternatives: [{ id: 'choose', label: 'Choose region', inputs: [{ id: 'region', label: 'Region', type: 'text', defaultValue: '' }], conditions: [{ table: 'person', column: 'org', operator: 'eq', parameterId: 'region' }] }] });
  const warnings = aggregationWarning ? ["COUNT of Personnel.id may be affected by repeated rows from joins. The selected aggregation runs as configured."] : [];
  if (emptySlicers) fixture.dashboard.selections = {};
  if (chartTypes) fixture.dashboard.tiles = ['bar', 'line', 'donut', 'aggregate', 'kpi', 'detail'].map(kind => ({ ...fixture.dashboard.tiles[0], id: kind, kind, title: `${kind} view`, dimensions: ['kpi', 'detail'].includes(kind) ? [] : [field('org')], measures: kind === 'detail' ? [] : [field('id', 'count')] }));
  if (manyBars) fixture.dashboard.tiles[0].limit = 20;
  if (manyRows) {
    fixture.dashboard.tiles[0].kind = 'aggregate';
    fixture.dashboard.tiles[0].limit = 20;
  }
  const executions = new Map(), fetched = new Set();
  function dataFor(tile, drill) {
    return drill || tile.kind === 'detail' ? { rows: [[1, 'Alex'], [2, 'Blake'], [3, 'Casey']], columns: [{ name: 'id', dataType: 'integer' }, { name: 'name', dataType: 'text' }], size: tile.limit }
      : tile.kind === 'kpi' ? { rows: [[3]], columns: [{ name: 'COUNT id', dataType: 'bigint' }], size: tile.limit }
        : { rows: manyBars || manyRows ? Array.from({ length: 500 }, (_, index) => [`Organization ${index + 1}`, index + 1]) : [['HQ', 3], ['Branch', 2]], columns: [{ name: 'org', dataType: 'text' }, { name: 'COUNT id', dataType: 'bigint' }], size: tile.limit };
  }
  const requests = [], errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), body = request.postDataJSON();
    let response;
    if (url.pathname === '/api/v1/schemoo/models') response = { models: [fixture.model] };
    else if (url.pathname === `/api/v1/schemoo/models/${modelId}`) response = fixture.model;
    else if (url.pathname === '/api/v1/schemoo/catalog') response = fixture.catalog;
    else if (url.pathname === '/api/v1/schemer/dashboards') response = { dashboards: [fixture.dashboard] };
    else if (url.pathname === `/api/v1/schemer/dashboards/${dashboardId}`) {
      if (request.method() === 'PUT') fixture.dashboard = { ...fixture.dashboard, ...body, revision: fixture.dashboard.revision + 1 };
      response = fixture.dashboard;
    } else if (url.pathname === `/api/v1/schemer/dashboards/${dashboardId}/executions`) {
      requests.push({ body, path: url.pathname });
      const id = `e${executions.size + 1}`, records = {};
      fixture.dashboard.tiles.forEach(tile => { records[`result-${tile.id}`] = dataFor(tile, false); });
      executions.set(id, { records });
      response = { execution: { id, status: 'succeeded', results: fixture.dashboard.tiles.map((tile, index) => ({ id: `result-${tile.id}`, statementIndex: index })) }, executionUrl: `/api/v1/common/query-executions/${id}`,
        tiles: fixture.dashboard.tiles.map((tile, index) => ({ tileId: tile.id, statementIndex: index, rowLimit: tile.limit, plan: { sql: 'SELECT org, COUNT(id) FROM personnel GROUP BY org;', warnings, rowLimit: tile.limit } })), tileErrors: [] };
    } else if (/\/tiles\/.+\/(executions|plan)$/.test(url.pathname)) {
      requests.push({ body, path: url.pathname });
      const tileId = url.pathname.split('/').at(-2), tile = fixture.dashboard.tiles.find(t => t.id === tileId);
      const drill = body.selection;
      const plan = { sql: drill ? "SELECT id, name FROM personnel WHERE org = 'HQ' AND id IS NOT NULL;" : 'SELECT org, COUNT(id) FROM personnel GROUP BY org;', warnings, rowLimit: tile.limit };
      const rowData = dataFor(tile, drill);
      if (url.pathname.endsWith('/plan')) response = plan;
      else {
        const id = `e${executions.size + 1}`; executions.set(id, { ...rowData, size: tile.limit });
        response = { plan, execution: { id, status: 'succeeded', results: [{ id: 'result' }] }, executionUrl: `/api/v1/common/query-executions/${id}` };
      }
    } else if (url.pathname.startsWith('/api/v1/common/query-executions/')) {
      requests.push({ path: url.pathname, search: url.search, method: request.method() });
      const id = url.pathname.split('/')[5], execution = executions.get(id);
      if (request.method() === 'DELETE') response = {};
      else if (url.pathname.includes('/results/')) {
        const resultId = url.pathname.split('/').at(-1), rows = execution.records ? execution.records[resultId] : execution;
        const size = Number(url.searchParams.get('page_size')) || rows.size;
        const offset = Number(url.searchParams.get('cursor') || 0), key = `${id}:${resultId}:${offset}`;
        if (fetched.has(key)) { await route.fulfill({ status: 410, json: { error: { message: 'Cursor already used' } } }); return; }
        fetched.add(key);
        response = { columns: rows.columns, rows: rows.rows.slice(offset, offset + size), nextCursor: offset + size < rows.rows.length ? String(offset + size) : null };
      } else response = { id, status: 'succeeded', results: [{ id: 'result' }] };
    } else response = {};
    await route.fulfill({ status: 200, json: response });
  });
  return { fixture, requests, errors };
}

test('a changed Schemoo model blocks query refresh until the dashboard is revalidated and updated', async ({ page }) => {
  const { fixture, requests, errors } = await mock(page, { modelRevision: 2 });
  await page.goto('/schemer');

  const update = page.locator('#model-update');
  await expect(update).toBeVisible();
  await expect(update).toContainText('Workforce v1; v2 is available');
  await expect(page.locator('.tile-status')).toContainText('Not run');
  expect(requests).toHaveLength(0);

  await page.getByRole('button', { name: 'Update to v2', exact: true }).click();
  await expect(update).toBeHidden();
  await expect(page.locator('.analytics-tile')).toContainText('2 groups');
  expect(fixture.dashboard.modelRevision).toBe(2);
  expect(errors).toEqual([]);
});

test('refresh checks for a Schemoo model update before it reruns dashboard queries', async ({ page }) => {
  const { fixture, requests, errors } = await mock(page);
  await page.goto('/schemer');
  await expect(page.locator('.analytics-tile')).toContainText('2 groups');
  const executionsBeforeRefresh = requests.filter(request => request.path.endsWith('/executions')).length;

  fixture.model.revision = 2;
  await page.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
  await expect(page.locator('#model-update')).toBeVisible();
  expect(requests.filter(request => request.path.endsWith('/executions')).length).toBe(executionsBeforeRefresh);

  await page.getByRole('button', { name: 'Update to v2', exact: true }).click();
  await expect(page.locator('#model-update')).toBeHidden();
  expect(fixture.dashboard.modelRevision).toBe(2);
  expect(errors).toEqual([]);
});

test('dashboard creators expose optional model filters and viewers explicitly activate them', async ({ page }) => {
  const { fixture, errors } = await mock(page, { optionalFilter: true });
  await page.goto('/schemer');

  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  const chooser = page.getByRole('dialog', { name: 'Choose dashboard filters' });
  await chooser.getByRole('checkbox', { name: 'Offer Region' }).check();
  await chooser.getByRole('button', { name: 'Save available filters' }).click();

  const activate = page.getByRole('checkbox', { name: 'Activate Region' });
  await expect(activate).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Region: Region' })).toBeDisabled();
  await activate.check();
  await page.getByRole('textbox', { name: 'Region: Region' }).fill('Branch');
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();

  expect(fixture.dashboard.optionalFilters).toEqual(['region']);
  expect(fixture.dashboard.selections.region).toMatchObject({ active: true, values: { region: 'Branch' } });
  expect(errors).toEqual([]);
});

test('square tiles expand into chart, SQL, and correctly filtered paged detail rows', async ({ page }) => {
  const { requests, errors } = await mock(page);
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile).toContainText('2 groups');
  const box = await tile.boundingBox(); expect(Math.abs(box.width - box.height)).toBeLessThan(2);
  await tile.getByRole('heading', { name: 'Positions by org' }).click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true }); await expect(expanded).toBeVisible();
  await expanded.getByRole('button', { name: 'HQ: 3. View records', exact: true }).click();
  await expect(expanded.locator('.drill-body')).toContainText('Alex');
  expect(requests.findLast(r => r.body?.selection).body.selection).toEqual({ dimensions: [{ table: 'person', column: 'org', value: 'HQ' }], measureIndex: 0 });
  await expanded.getByRole('button', { name: 'Show detail SQL' }).click();
  const sql = page.getByRole('dialog', { name: 'Detail rows · SQL' }); await expect(sql.locator('code')).toContainText("org = 'HQ'");
  await sql.getByRole('button', { name: 'Close SQL' }).click();
  await expect(expanded.locator('.drill-body')).toContainText('Casey');
  expect(requests.some(r => r.search?.includes('cursor=2'))).toBe(true);
  const fetchedPages = requests.filter(r => r.method === 'GET').length;
  await expanded.locator('.drill-body .data-grid-viewport').evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event('scroll')); });
  await expect(expanded.locator('.drill-body')).toContainText('Alex');
  expect(requests.filter(r => r.method === 'GET').length).toBe(fetchedPages);
  await expect(expanded.getByRole('button', { name: 'Next', exact: true })).toHaveCount(0);
  await expanded.getByRole('button', { name: 'Positions by org', exact: true }).click();
  await expect(expanded.locator('.expanded-chart-body')).toBeVisible();
  await expanded.getByRole('button', { name: 'Close expanded tile' }).click(); await expect(expanded).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('required slicers gate every tile and tile editor validates dimensions before saving', async ({ page }) => {
  const { requests, errors } = await mock(page, { emptySlicers: true });
  await page.goto('/schemer'); await expect(page.locator('.analytics-tile')).toBeVisible();
  expect(requests).toHaveLength(0);
  await page.getByRole('textbox', { name: 'Organization: Root organization' }).fill('HQ');
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();
  await expect(page.locator('.tile-status')).toContainText('2 groups');
  await page.getByRole('button', { name: 'Edit Positions by org', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Configure analytics tile' });
  await editor.getByRole('button', { name: 'Remove Dimensions 1', exact: true }).click();
  await editor.getByRole('button', { name: 'Apply & run' }).click();
  await expect(editor.getByRole('alert')).toContainText('Choose one dimension');
  await editor.getByRole('combobox', { name: 'Add dimensions', exact: true }).fill('org');
  await page.getByRole('option', { name: 'Personnel · org', exact: true }).click();
  await editor.getByRole('textbox', { name: 'Tile title', exact: true }).fill('Manager positions');
  await editor.getByRole('button', { name: 'Apply & run' }).click();
  await expect(editor).toHaveCount(0); await expect(page.locator('.analytics-tile')).toContainText('Manager positions');
  await page.reload(); await expect(page.locator('.analytics-tile')).toContainText('Manager positions');
  expect(errors).toEqual([]);
});

test('all supported tile views render in a responsive square grid', async ({ page }, testInfo) => {
  const { errors } = await mock(page, { chartTypes: true });
  await page.goto('/schemer');
  await expect(page.locator('.analytics-tile')).toHaveCount(6);
  await expect(page.locator('.tile-status').filter({ hasText: 'Not run' })).toHaveCount(0);
  await expect(page.locator('.tile-status').filter({ hasText: 'Running' })).toHaveCount(0);
  await expect(page.locator('.tile-error')).toHaveCount(0);
  await expect(page.locator('.line-chart')).toHaveCount(1); await expect(page.locator('.donut-chart')).toHaveCount(1);
  for (const tile of await page.locator('.analytics-tile').all()) { const box = await tile.boundingBox(); expect(Math.abs(box.width - box.height)).toBeLessThan(2); }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('dashboard.png'), fullPage: true });
  expect(errors).toEqual([]);
});


test('500-bar chart scrolls forward without rerunning SQL or refetching cached groups', async ({ page }) => {
  const { requests, errors } = await mock(page, { manyBars: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile.locator('.tile-status')).toContainText('20 groups cached');
  const starts = () => requests.filter(r => r.path.endsWith('/executions')).length;
  expect(starts()).toBe(1);
  await tile.locator('.tile-body').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect(tile.locator('.tile-status')).toContainText('40 groups cached');
  expect(starts()).toBe(1);
  const gets = requests.filter(r => r.method === 'GET').length;
  await tile.locator('.tile-body').evaluate(node => { node.scrollTop = 0; });
  await tile.getByRole('heading').click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  await expect(expanded.locator('.viewer-status').first()).toContainText('40 groups cached');
  expect(requests.filter(r => r.method === 'GET').length).toBe(gets);
  await expanded.locator('.expanded-chart-body').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expect(expanded.locator('.viewer-status').first()).toContainText('60 groups cached');
  expect(starts()).toBe(1);
  await expanded.getByRole('button', { name: 'Close expanded tile' }).click();
  await expect(tile.locator('.tile-status')).toContainText('60 groups cached');
  expect(errors).toEqual([]);
});

test('aggregation warnings leave results usable without acknowledgement', async ({ page }) => {
  const { errors } = await mock(page, { aggregationWarning: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile).toContainText('2 groups');
  await tile.getByText('Aggregation warning', { exact: true }).click();
  await expect(tile.locator('.aggregation-warning')).toContainText('runs as configured');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await tile.getByRole('heading', { name: 'Positions by org' }).click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  await expect(expanded.getByText('Aggregation warning', { exact: true })).toBeVisible();
  await expanded.getByRole('button', { name: 'HQ: 3. View records', exact: true }).click();
  await expect(expanded.locator('.drill-body')).toContainText('Alex');
  expect(errors).toEqual([]);
});

test('row reports append on scroll and reuse cached rows when expanded', async ({ page }) => {
  const { requests, errors } = await mock(page, { manyRows: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  const grid = tile.locator('.data-grid-viewport');
  await expect(grid.locator('tbody tr')).toHaveCount(20);
  await grid.evaluate(node => { node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll')); });
  await expect(grid.locator('tbody tr')).toHaveCount(40);
  await expect(grid.locator('tbody tr').first()).toContainText('Organization 1');
  expect(await grid.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  const reads = requests.filter(r => r.method === 'GET').length;
  await grid.evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event('scroll')); });
  expect(requests.filter(r => r.method === 'GET').length).toBe(reads);
  await tile.getByRole('heading', { name: 'Positions by org' }).click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  const expandedGrid = expanded.locator('.data-grid-viewport');
  await expect(expandedGrid.locator('tbody tr')).toHaveCount(40);
  await expandedGrid.evaluate(node => { node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll')); });
  await expect(expandedGrid.locator('tbody tr')).toHaveCount(60);
  expect(requests.filter(r => r.path.endsWith('/executions'))).toHaveLength(1);
  await expect(expanded.getByRole('button', { name: 'Next', exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});
