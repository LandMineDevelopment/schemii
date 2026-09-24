import { expect, test } from '@playwright/test';

const modelId = `model_${'a'.repeat(32)}`, dashboardId = `dashboard_${'b'.repeat(32)}`;
const field = (column, aggregate = 'none') => ({ table: 'person', column, aggregate });
function fixtures() {
  const model = { id: modelId, revision: 1, name: 'Workforce', database: 'organization', namespace: 'public', connectionId: `pg_${'c'.repeat(32)}`,
    definition: { root: 'person', nodes: [{ id: 'person', table: 'personnel', label: 'Personnel' }], edges: [], scopes: [{ id: 'organization', label: 'Organization', kind: 'required', alternatives: [{ id: 'choose', label: 'Choose organization', inputs: [{ id: 'org', label: 'Root organization', type: 'text' }], conditions: [{ table: 'person', column: 'org', operator: 'eq', parameterId: 'org' }] }] }] }, explore: { fields: [], selections: {} } };
  const tile = { id: 'positions', title: 'Positions by org', kind: 'bar', dimensions: [field('org')], measures: [field('id', 'count')], detailFields: [field('id'), field('name')], reportFilters: [], selections: {}, limit: 2 };
  const dashboard = { id: dashboardId, revision: 1, modelId, modelRevision: 1, name: 'Workforce overview', optionalFilters: [], selections: { organization: { alternativeId: 'choose', values: { org: 'HQ' } } }, tiles: [tile] };
  return { model, dashboard, catalog: { tables: [{ name: 'personnel', columns: [{ name: 'id', dataType: 'integer' }, { name: 'name', dataType: 'text' }, { name: 'org', dataType: 'text' }, { name: 'hired_at', dataType: 'date' }] }], relationships: [] } };
}
async function mock(page, { emptySlicers = false, chartTypes = false, manyBars = false, aggregationWarning = false, manyRows = false, modelRevision = 1, optionalFilter = false, capped = false, streamError = false, emptyRows = false, multidimensional = false } = {}) {
  const fixture = fixtures();
  fixture.model.revision = modelRevision;
  if (optionalFilter) fixture.model.definition.scopes.push({ id: 'region', label: 'Region', kind: 'conditional', requirement: 'optional', alternatives: [{ id: 'choose', label: 'Choose region', inputs: [{ id: 'region', label: 'Region', type: 'text', defaultValue: '' }], conditions: [{ table: 'person', column: 'org', operator: 'eq', parameterId: 'region' }] }] });
  const warnings = aggregationWarning ? ["COUNT of Personnel.id may be affected by repeated rows from joins. The selected aggregation runs as configured."] : [];
  const repetitionDiagnostics = aggregationWarning ? [{ code: 'measure_repetition', outputIndex: 1,
    measure: { table: 'person', column: 'id', aggregate: 'count', label: 'COUNT of Personnel.id' },
    relationships: [{ id: 'assignment_person', fromNode: 'person', toNode: 'assignment',
      source: { node: 'assignment', label: 'Assignments', column: 'person_id' },
      target: { node: 'person', label: 'Personnel', column: 'id' },
      path: [{ node: 'person', label: 'Personnel' }, { node: 'assignment', label: 'Assignments' }] }] }] : [];
  if (emptySlicers) fixture.dashboard.selections = {};
  if (chartTypes) fixture.dashboard.tiles = ['bar', 'line', 'donut', 'aggregate', 'kpi', 'detail'].map(kind => ({ ...fixture.dashboard.tiles[0], id: kind, kind, title: `${kind} view`, dimensions: ['kpi', 'detail'].includes(kind) ? [] : [field('org')], measures: kind === 'detail' ? [] : [field('id', 'count')] }));
  if (multidimensional) fixture.dashboard.tiles = ['bar', 'line', 'donut', 'aggregate'].map(kind => ({
    ...fixture.dashboard.tiles[0], id: kind, kind, title: `${kind} grouped`, dimensions: [field('org'), field('name')],
  }));
  if (manyBars) fixture.dashboard.tiles[0].limit = 20;
  if (manyRows) {
    fixture.dashboard.tiles[0].kind = 'aggregate';
    fixture.dashboard.tiles[0].limit = 20;
  }
  function framesFor(tiles, selection) {
    const snapshotAt = '2026-09-20T12:00:00Z';
    const planFor = tile => ({ sql: selection ? "SELECT id, name FROM personnel WHERE org = 'HQ' AND id IS NOT NULL;" : 'SELECT org, COUNT(id) FROM personnel GROUP BY org;', warnings, repetitionDiagnostics, rowLimit: tile.limit });
    const frames = [{ type: 'start', snapshotAt, tiles: tiles.map(tile => ({ tileId: tile.id, plan: planFor(tile) })), tileErrors: [], limits: { rows: 10000, bytes: 8388608, totalBytes: 33554432 } }];
    for (const tile of tiles) {
      const data = dataFor(tile, selection);
      const rows = emptyRows ? [] : capped ? data.rows.slice(0, 40) : data.rows;
      for (let offset = 0; offset < rows.length; offset += tile.limit) {
        frames.push({ type: 'rows', tileId: tile.id, columns: data.columns, rows: rows.slice(offset, offset + tile.limit) });
        if (streamError) break;
      }
      frames.push(streamError ? { type: 'error', tileId: tile.id, message: 'Source connection interrupted', code: 'query_failed' }
        : { type: 'complete', tileId: tile.id, rowCount: rows.length, limitReached: capped, reason: capped ? 'row_limit' : null, snapshotAt });
    }
    frames.push({ type: 'end' });
    return frames.map(frame => JSON.stringify(frame)).join('\n') + '\n';
  }
  function dataFor(tile, drill) {
    return drill || tile.kind === 'detail' ? { rows: [[1, 'Alex'], [2, 'Blake'], [3, 'Casey']], columns: [{ name: 'id', dataType: 'integer' }, { name: 'name', dataType: 'text' }], size: tile.limit }
      : multidimensional ? { rows: multidimensional === 'wide' ? Array.from({ length: 150 }, (_, index) => [index, 'Group', index + 1]) : multidimensional === 'many' ? Array.from({ length: 150 }, (_, index) => ['HQ', `Person ${index}`, index + 1]) : [['Branch', 'Alex', 2], ['Branch', 'Blake', 3], ['HQ', 'Alex', 4], ['Remote', 'Alex', 1], ['Remote', 'Blake', 5]], columns: [{ name: 'org', dataType: 'text' }, { name: 'name', dataType: 'text' }, { name: 'COUNT id', dataType: 'bigint' }] }
      : tile.kind === 'kpi' ? { rows: [[3]], columns: [{ name: 'COUNT id', dataType: 'bigint' }], size: tile.limit }
        : { rows: manyBars || manyRows ? Array.from({ length: 500 }, (_, index) => [`Organization ${index + 1}`, index + 1]) : [['HQ', 3], ['Branch', 2]], columns: [{ name: 'org', dataType: 'text' }, { name: 'COUNT id', dataType: 'bigint' }], size: tile.limit };
  }
  const requests = [], errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.context().route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), body = request.headers()['content-type']?.includes('application/x-www-form-urlencoded') ? JSON.parse(new URLSearchParams(request.postData()).get('payload')) : request.postDataJSON();
    let response;
    if (url.pathname.startsWith('/api/v1/auth/')) { await route.fallback(); return; }
    if (url.pathname === `/api/v1/schemer/dashboards/${dashboardId}/context`) response = { model: fixture.model, catalog: fixture.catalog, permissions: { edit: true, export: true, drill: true } };
    else if (url.pathname === '/api/v1/schemoo/models') response = { models: [fixture.model] };
    else if (url.pathname === `/api/v1/schemoo/models/${modelId}`) response = fixture.model;
    else if (url.pathname === '/api/v1/schemoo/catalog') response = fixture.catalog;
    else if (url.pathname === '/api/v1/schemer/dashboards') response = { dashboards: multidimensional === 'many' ? [fixture.dashboard, ...Array.from({ length: 4 }, (_, index) => ({ ...fixture.dashboard, id: `other-${index}`, name: `Long multidimensional dashboard ${index}` }))] : [fixture.dashboard] };
    else if (url.pathname === `/api/v1/schemer/dashboards/${dashboardId}`) {
      if (request.method() === 'PUT') fixture.dashboard = { ...fixture.dashboard, ...body, revision: fixture.dashboard.revision + 1 };
      response = fixture.dashboard;
    } else if (url.pathname === `/api/v1/schemer/dashboards/${dashboardId}/executions/stream`) {
      requests.push({ body, path: url.pathname });
      await route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: framesFor(fixture.dashboard.tiles) }); return;
    } else if (/\/tiles\/.+\/(executions\/stream|plan|export)$/.test(url.pathname)) {
      requests.push({ body, path: url.pathname });
      const tileId = url.pathname.split('/')[7], tile = fixture.dashboard.tiles.find(t => t.id === tileId);
      if (url.pathname.endsWith('/plan')) response = { sql: 'SELECT org, COUNT(id) FROM personnel GROUP BY org;', warnings, rowLimit: tile.limit };
      else if (url.pathname.endsWith('/export')) {
        if (body.expectedRevision !== fixture.dashboard.revision) { await route.fulfill({ status: 409, json: { error: { message: 'Dashboard revision changed' } } }); return; }
        await route.fulfill({ status: 200, contentType: 'text/csv', headers: { 'Content-Disposition': 'attachment; filename="schemer-results.csv"' }, body: 'org,COUNT id\nHQ,3\nBranch,2\n' }); return;
      } else {
        await route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: framesFor([tile], body.selection) }); return;
      }
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

test('searchable tile fields and footer stay connected and reachable at phone and desktop widths', async ({ page }) => {
  await mock(page);
  await page.goto('/schemer');
  await page.getByRole('button', { name: 'Edit Positions by org', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Configure analytics tile' });
  const controls = editor.locator('.ui-searchable-select');
  await expect(controls.first()).toBeVisible();

  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
    const geometry = await controls.evaluateAll(elements => elements.filter(element => element.offsetParent !== null).map(element => {
      const wrapper = element.getBoundingClientRect();
      const input = element.querySelector('input').getBoundingClientRect();
      const toggle = element.querySelector('.ui-searchable-select__toggle').getBoundingClientRect();
      return {
        wrapper: { left: wrapper.left, right: wrapper.right },
        input: { left: input.left, right: input.right },
        toggle: { left: toggle.left, right: toggle.right },
      };
    }));
    expect(geometry.length).toBeGreaterThanOrEqual(2);
    for (const { wrapper, input, toggle } of geometry) {
      expect(Math.abs(wrapper.left - input.left)).toBeLessThan(2);
      expect(Math.abs(wrapper.right - input.right)).toBeLessThan(2);
      expect(toggle.left).toBeGreaterThanOrEqual(input.left);
      expect(toggle.right).toBeLessThanOrEqual(input.right + 2);
    }
    const actions = editor.locator('.ui-dialog__actions');
    for (const label of ['Cancel', 'Apply & run']) {
      const button = actions.getByRole('button', { name: label });
      await expect(button).toBeVisible();
      const box = await button.boundingBox();
      expect(box.y + box.height).toBeLessThanOrEqual(width === 1440 ? 900 : 844);
    }
  }
});

test('refresh checks for a Schemoo model update before it reruns dashboard queries', async ({ page }) => {
  const { fixture, requests, errors } = await mock(page);
  await page.goto('/schemer');
  await expect(page.locator('.analytics-tile')).toContainText('2 groups');
  const executionsBeforeRefresh = requests.filter(request => request.path.endsWith('/executions/stream')).length;

  fixture.model.revision = 2;
  await page.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
  await expect(page.locator('#model-update')).toBeVisible();
  expect(requests.filter(request => request.path.endsWith('/executions/stream')).length).toBe(executionsBeforeRefresh);

  await page.getByRole('button', { name: 'Update to v2', exact: true }).click();
  await expect(page.locator('#model-update')).toBeHidden();
  expect(fixture.dashboard.modelRevision).toBe(2);
  expect(errors).toEqual([]);
});

test('dashboards without configured filters still expose filter configuration', async ({ page }) => {
  const { fixture, errors } = await mock(page, { optionalFilter: true });
  fixture.model.definition.scopes = fixture.model.definition.scopes.filter(scope => scope.requirement === 'optional');
  fixture.dashboard.selections = {};
  await page.goto('/schemer');
  await expect(page.locator('#filter-summary')).toHaveText('No filters applied');
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Apply filters', exact: true })).toBeHidden();
  await page.getByRole('button', { name: 'Choose filters', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: 'Offer Region' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('a failed model reload hides the previous applied filter summary', async ({ page }) => {
  const { errors } = await mock(page);
  await page.goto('/schemer');
  await expect(page.locator('#filter-summary')).toContainText('Organization: HQ');
  await page.route(`**/api/v1/schemer/dashboards/${dashboardId}/context`, route => route.fulfill({ status: 503, json: { error: { message: 'Model unavailable' } } }));
  await page.locator('.dashboard-link').click();
  await expect(page.locator('#tile-grid')).toContainText('The source model could not be loaded');
  await expect(page.locator('#filter-summary')).toBeHidden();
  await expect(page.locator('#slicer-panel')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Filters', exact: true })).toHaveAttribute('aria-expanded', 'false');
  expect(errors).toEqual([]);
});

test('toolbar filters toggle the panel without losing edits or claiming drafts are applied', async ({ page }) => {
  const { fixture, requests, errors } = await mock(page);
  await page.goto('/schemer');
  await expect(page.locator('.tile-status')).toContainText('2 groups');
  const toggle = page.getByRole('button', { name: 'Filters', exact: true });
  const panel = page.getByRole('region', { name: 'Dashboard filters', exact: true });
  const summary = page.getByRole('status', { name: 'Applied dashboard filters' });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toBeHidden();
  await expect(summary).toContainText('Organization: HQ');
  const before = requests.length;
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(panel).toBeVisible();
  await expect(summary).toBeHidden();
  const input = page.getByRole('textbox', { name: 'Organization: Root organization' });
  await input.fill('Branch');
  await toggle.click();
  await expect(summary).toContainText('Organization: HQ');
  await expect(summary).toContainText('Unapplied changes');
  await expect(summary).not.toContainText('Branch');
  await toggle.click();
  await expect(input).toHaveValue('Branch');
  expect(requests).toHaveLength(before);
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();
  await expect(page.locator('.tile-status')).toContainText('2 groups');
  await toggle.click();
  await expect(summary).toContainText('Organization: Branch');
  await expect(summary).not.toContainText('Unapplied changes');
  expect(fixture.dashboard.selections.organization.values.org).toBe('Branch');
  expect(errors).toEqual([]);
});

test('dashboard creators expose optional model filters and viewers explicitly activate them', async ({ page }) => {
  const { fixture, errors } = await mock(page, { optionalFilter: true });
  await page.goto('/schemer');

  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await page.getByRole('button', { name: 'Choose filters', exact: true }).click();
  const chooser = page.getByRole('dialog', { name: 'Choose dashboard filters' });
  await chooser.getByRole('checkbox', { name: 'Offer Region' }).check();
  await chooser.getByRole('button', { name: 'Save available filters' }).click();

  const activate = page.getByRole('checkbox', { name: 'Activate Region' });
  await expect(activate).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Region: Region' })).toBeDisabled();
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await expect(page.locator('#filter-summary')).not.toContainText('Region');
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await activate.check();
  await page.getByRole('textbox', { name: 'Region: Region' }).fill('Branch');
  await page.getByRole('button', { name: 'Apply filters', exact: true }).click();

  expect(fixture.dashboard.optionalFilters).toEqual(['region']);
  expect(fixture.dashboard.selections.region).toMatchObject({ active: true, values: { region: 'Branch' } });
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await expect(page.locator('#filter-summary')).toContainText('Region: Branch');
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
  expect(requests.filter(r => r.body?.selection)).toHaveLength(1);
  const fetchedPages = requests.filter(r => r.method === 'GET').length;
  await expanded.locator('.drill-body .data-grid-viewport').evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event('scroll')); });
  await expect(expanded.locator('.drill-body')).toContainText('Alex');
  expect(requests.filter(r => r.method === 'GET').length).toBe(fetchedPages);
  await expect(expanded.getByRole('button', { name: 'Next', exact: true })).toHaveCount(0);
  await expanded.getByRole('button', { name: 'Positions by org', exact: true }).click();
  await expect(expanded.locator('.expanded-chart-body')).toBeVisible();
  await expanded.getByRole('button', { name: 'HQ: 3. View records', exact: true }).click();
  await expect(expanded.locator('.drill-body')).toContainText('Casey');
  expect(requests.filter(r => r.body?.selection)).toHaveLength(1);
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
  await expect(editor.getByRole('alert')).toContainText('Choose at least one dimension');
  await editor.getByRole('combobox', { name: 'Add dimensions', exact: true }).fill('org');
  await page.getByRole('option', { name: 'Personnel · org', exact: true }).click();
  await editor.getByRole('textbox', { name: 'Tile title', exact: true }).fill('Manager positions');
  await editor.getByRole('button', { name: 'Apply & run' }).click();
  await expect(editor).toHaveCount(0); await expect(page.locator('.analytics-tile')).toContainText('Manager positions');
  await page.reload(); await expect(page.locator('.analytics-tile')).toContainText('Manager positions');
  expect(errors).toEqual([]);
});

test('date dimensions offer inline grouping and retain saved year settings', async ({ page }) => {
  const { fixture, errors } = await mock(page);
  await page.goto('/schemer');
  await page.getByRole('button', { name: 'Edit Positions by org', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Configure analytics tile' });
  await expect(editor.getByRole('tab', { name: 'Time analysis', exact: true })).toHaveCount(0);
  await expect(editor.getByRole('combobox', { name: 'Dimension 1 group by', exact: true })).toHaveCount(0);
  await editor.getByRole('button', { name: 'Remove Dimensions 1', exact: true }).click();
  await editor.getByRole('combobox', { name: 'Add dimensions', exact: true }).fill('hired');
  await page.getByRole('option', { name: 'Personnel · hired_at', exact: true }).click();
  const grouping = editor.getByRole('combobox', { name: 'Dimension 1 group by', exact: true });
  await expect(grouping).toHaveValue('Exact date / time');
  await grouping.click();
  await page.getByRole('option', { name: 'Month', exact: true }).click();
  await expect(grouping).toHaveValue('Month');
  await grouping.click();
  await page.getByRole('option', { name: 'Year', exact: true }).click();
  await editor.getByText('Date grouping options', { exact: true }).click();
  await expect(editor.getByRole('textbox', { name: 'Time zone', exact: true })).toHaveValue('UTC');
  await editor.getByRole('button', { name: 'Apply & run' }).click();
  await expect(editor).toHaveCount(0);
  expect(fixture.dashboard.tiles[0].dimensions).toEqual([field('hired_at')]);
  expect(fixture.dashboard.tiles[0].timeAnalysis).toMatchObject({ table: 'person', column: 'hired_at', granularity: 'year' });
  await page.reload();
  await page.getByRole('button', { name: 'Edit Positions by org', exact: true }).click();
  await expect(grouping).toHaveValue('Year');
  await grouping.click();
  await page.getByRole('option', { name: 'Exact date / time', exact: true }).click();
  await expect(editor.getByText('Date grouping options', { exact: true })).toHaveCount(0);
  await editor.getByRole('button', { name: 'Apply & run' }).click();
  await expect(editor).toHaveCount(0);
  expect(fixture.dashboard.tiles[0].timeAnalysis).toBeNull();
  expect(fixture.dashboard.tiles[0].dimensions).toEqual([field('hired_at')]);
  expect(errors).toEqual([]);
});

test('removing a grouped date clears its time settings', async ({ page }) => {
  const { fixture, errors } = await mock(page);
  fixture.dashboard.tiles[0].dimensions = [field('hired_at')];
  fixture.dashboard.tiles[0].timeAnalysis = { table: 'person', column: 'hired_at', granularity: 'month', timezone: 'UTC', weekStart: 'monday', comparison: 'none', runningTotal: false };
  await page.goto('/schemer');
  await page.getByRole('button', { name: 'Edit Positions by org', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Configure analytics tile' });
  await expect(editor.getByRole('combobox', { name: 'Dimension 1 group by', exact: true })).toHaveValue('Month');
  await editor.getByRole('button', { name: 'Remove Dimensions 1', exact: true }).click();
  await editor.getByRole('combobox', { name: 'Add dimensions', exact: true }).fill('org');
  await page.getByRole('option', { name: 'Personnel · org', exact: true }).click();
  await editor.getByRole('button', { name: 'Apply & run' }).click();
  await expect(editor).toHaveCount(0);
  expect(fixture.dashboard.tiles[0].timeAnalysis).toBeNull();
  expect(errors).toEqual([]);
});

test('every tile editor explains bounded streaming without an ineffective batch control', async ({ page }) => {
  const { fixture, errors } = await mock(page, { chartTypes: true });
  await page.goto('/schemer');
  await expect(page.locator('.analytics-tile')).toHaveCount(6);

  for (const tile of [...fixture.dashboard.tiles]) {
    await page.getByRole('button', { name: `Edit ${tile.title}`, exact: true }).click();
    const editor = page.getByRole('dialog', { name: 'Configure analytics tile' });
    await expect(editor.getByRole('spinbutton')).toHaveCount(0);
    await expect(editor).not.toContainText('per batch');
    await expect(editor).toContainText('Results stream automatically into a bounded browser cache.');
    await expect(editor).toContainText('scrolling does not run another query');
    await expect(editor).toContainText('Previews stop at row or memory limits');
    await expect(editor).toContainText('Refresh runs the query again.');
    await expect(editor).toContainText('Download full results to run the full query against a fresh snapshot.');
    await editor.getByRole('textbox', { name: 'Tile title', exact: true }).fill(`${tile.title} updated`);
    await editor.getByRole('button', { name: 'Apply & run' }).click();
    await expect(editor).toHaveCount(0);
    const saved = fixture.dashboard.tiles.find(saved => saved.id === tile.id);
    expect(saved.title).toBe(`${tile.title} updated`);
    expect(saved.limit).toBe(tile.limit);
  }
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


test('500-bar chart arrives eagerly and expanded views reuse every cached group', async ({ page }) => {
  const { requests, errors } = await mock(page, { manyBars: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile.locator('.tile-status')).toContainText('500 groups');
  expect(requests).toHaveLength(1);
  await tile.locator('.tile-body').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await tile.getByRole('heading').click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  await expect(expanded.locator('.viewer-status').first()).toContainText('500 groups');
  await expanded.locator('.expanded-chart-body').evaluate(node => { node.scrollTop = node.scrollHeight; });
  await expanded.getByRole('button', { name: 'Close expanded tile' }).click();
  await expect(tile.locator('.tile-status')).toContainText('500 groups');
  expect(requests).toHaveLength(1);
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

test('row reports arrive without scrolling and reuse all cached rows when expanded', async ({ page }) => {
  const { requests, errors } = await mock(page, { manyRows: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  const grid = tile.locator('.data-grid-viewport');
  await expect(tile.locator('.tile-status')).toContainText('500 groups');
  await expect(grid.locator('tr[data-result-index]').first()).toContainText('Organization 1');
  await grid.evaluate(node => { node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll')); });
  await expect(grid.locator('tr[data-result-index]').last()).toContainText('Organization 500');
  await tile.getByRole('heading', { name: 'Positions by org' }).click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  await expect(expanded.locator('.viewer-status').first()).toContainText('500 rows');
  expect(requests).toHaveLength(1);
  await expect(expanded.getByRole('button', { name: 'Next', exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

// Keep the response open after the first batch, independent of scrolling. This
// exercises incremental Fetch parsing instead of a fully buffered route body.
async function pauseAfterFirstBatch(page) {
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const response = await fetch(...args);
      const url = typeof args[0] === 'string' ? args[0] : args[0].url;
      if (!url.endsWith('/executions/stream')) return response;
      const frames = (await response.text()).trim().split('\n');
      const encoder = new TextEncoder();
      return new Response(new ReadableStream({
        start(controller) {
          const boundary = frames.findIndex(frame => JSON.parse(frame).type === 'rows') + 1;
          controller.enqueue(encoder.encode(frames.slice(0, boundary).join('\n') + '\n'));
          window.finishReportStream = () => {
            controller.enqueue(encoder.encode(frames.slice(boundary).join('\n') + '\n'));
            controller.close();
          };
        },
      }), { headers: { 'Content-Type': 'application/x-ndjson' } });
    };
  });
}

test('incoming rows show a bottom loading indicator and keep arriving without scrolling', async ({ page }) => {
  await pauseAfterFirstBatch(page);
  const { requests, errors } = await mock(page, { manyRows: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile.locator('.tile-status')).toContainText('20 groups');
  await expect(tile.locator('tr[data-result-index]').first()).toContainText('Organization 1');
  await expect(tile.locator('.stream-progress.is-loading')).toBeVisible();
  await tile.getByRole('heading').click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  await expect(expanded.locator('.viewer-status').first()).toContainText('20 rows');
  await expect(expanded.locator('.stream-progress.is-loading')).toBeVisible();
  expect(requests).toHaveLength(1);
  await page.evaluate(() => window.finishReportStream());
  await expect(expanded.locator('.viewer-status').first()).toContainText('500 rows');
  expect(await expanded.locator('tr[data-result-index]').count()).toBeLessThanOrEqual(100);
  await expect(expanded.locator('.stream-progress.is-loading')).toHaveCount(0);
  await expanded.getByRole('button', { name: 'Close expanded tile' }).click();
  await expect(tile.locator('.tile-status')).toContainText('500 groups');
  expect(await tile.locator('tr[data-result-index]').count()).toBeLessThanOrEqual(100);
  expect(requests).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('capped previews explain the limit and offer a full-result file download', async ({ page }) => {
  const { requests, errors } = await mock(page, { manyRows: true, capped: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile.locator('.tile-status')).toContainText('40 groups');
  await tile.getByRole('heading').click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  await expect(expanded).toContainText('Preview limit reached');
  const downloadEvent = new Promise(resolve => {
    page.once('download', resolve);
    page.context().once('page', popup => popup.once('download', resolve));
  });
  await expanded.getByRole('button', { name: 'Download full results', exact: true }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('schemer-results.csv');
  expect(requests.filter(request => request.path.endsWith('/export'))).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('a failed stream preserves partial rows and refresh replaces the browser cache', async ({ page }) => {
  await pauseAfterFirstBatch(page);
  const { requests, errors } = await mock(page, { manyRows: true, streamError: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile.locator('.tile-status')).toContainText('20 groups');
  await expect(tile.locator('tr[data-result-index]').first()).toContainText('Organization 1');
  await page.evaluate(() => window.finishReportStream());
  await expect(tile).toContainText('Source connection interrupted');
  await expect(tile.locator('.tile-status')).toContainText('20 groups');
  await expect(tile.locator('tr[data-result-index]').first()).toContainText('Organization 1');
  await expect(tile.locator('.stream-progress.is-loading')).toHaveCount(0);
  await page.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  await expect(tile.locator('.tile-status')).toContainText('20 groups');
  await expect(tile.locator('tr[data-result-index]').first()).toContainText('Organization 1');
  await page.evaluate(() => window.finishReportStream());
  await expect(tile).toContainText('Source connection interrupted');
  await expect(tile.locator('.tile-status')).toContainText('20 groups');
  await expect(tile.locator('tr[data-result-index]').first()).toContainText('Organization 1');
  expect(errors).toEqual([]);
});

test('SQL inspection generates a plan after a stream fails before returning one', async ({ page }) => {
  const { requests, errors } = await mock(page);
  await page.context().route(`**/api/v1/schemer/dashboards/${dashboardId}/executions/stream`, route =>
    route.fulfill({ status: 503, json: { error: { message: 'Report execution unavailable' } } }));
  await page.goto('/schemer');
  await expect(page.locator('.analytics-tile')).toContainText('Report execution unavailable');
  await page.getByRole('button', { name: 'SQL for Positions by org', exact: true }).click();
  const sql = page.getByRole('dialog', { name: 'Positions by org · generated SQL' });
  await expect(sql.locator('code')).toContainText('SELECT org, COUNT(id)');
  expect(requests.some(request => request.path.endsWith('/plan'))).toBe(true);
  expect(errors).toEqual([]);
});

test('an empty stream completes without a perpetual loading indicator', async ({ page }) => {
  const { requests, errors } = await mock(page, { manyRows: true, emptyRows: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile.locator('.tile-status')).toContainText('0 groups');
  await expect(tile.locator('.stream-progress.is-loading')).toHaveCount(0);
  await expect(tile.locator('.tile-error')).toHaveCount(0);
  expect(requests).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('renaming a dashboard keeps cached full-result downloads valid without rerunning previews', async ({ page }) => {
  const { fixture, requests, errors } = await mock(page);
  await page.goto('/schemer');
  await expect(page.locator('.tile-status')).toContainText('2 groups');
  await page.getByRole('button', { name: 'Rename dashboard', exact: true }).click();
  const rename = page.getByRole('dialog', { name: 'Rename dashboard', exact: true });
  await rename.getByRole('textbox', { name: 'Dashboard name', exact: true }).fill('Renamed workforce');
  await rename.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(rename).toHaveCount(0);
  expect(fixture.dashboard.revision).toBe(2);
  await page.locator('.analytics-tile').getByRole('heading').click();
  const expanded = page.getByRole('dialog', { name: 'Positions by org', exact: true });
  const downloadEvent = new Promise(resolve => {
    page.once('download', resolve);
    page.context().once('page', popup => popup.once('download', resolve));
  });
  await expanded.getByRole('button', { name: 'Download full results', exact: true }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('schemer-results.csv');
  expect(requests.filter(request => request.path.endsWith('/executions/stream'))).toHaveLength(1);
  expect(requests.find(request => request.path.endsWith('/export')).body.expectedRevision).toBe(2);
  expect(errors).toEqual([]);
});

test('stopping a partial stream keeps cached rows and ignores late batches until refresh', async ({ page }) => {
  await pauseAfterFirstBatch(page);
  const { requests, errors } = await mock(page, { manyRows: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile');
  await expect(tile.locator('.tile-status')).toContainText('20 groups');
  await expect(tile.locator('.stream-progress.is-loading')).toBeVisible();
  await page.getByRole('button', { name: 'Stop dashboard queries', exact: true }).click();
  await expect(tile).toContainText('Query stopped');
  await expect(tile.locator('.stream-progress.is-loading')).toHaveCount(0);
  await expect(tile.locator('tr[data-result-index]')).toHaveCount(20);
  // Deliberately deliver frames after cancellation: a closed cache must ignore
  // already in-flight data even if the transport delivers it late.
  await page.evaluate(() => window.finishReportStream());
  await expect(tile.locator('.tile-status')).toContainText('20 groups');
  await expect(tile.locator('tr[data-result-index]')).toHaveCount(20);
  await page.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  await expect(tile.locator('.stream-progress.is-loading')).toBeVisible();
  await page.evaluate(() => window.finishReportStream());
  await expect(tile.locator('.tile-status')).toContainText('500 groups');
  await expect(tile).not.toContainText('Query stopped');
  expect(errors).toEqual([]);
});


test('multidimensional charts label complete groups, separate lines, persist fields, and drill exact tuples', async ({ page }) => {
  const { fixture, requests, errors } = await mock(page, { multidimensional: true });
  await page.goto('/schemer');
  await expect(page.locator('.analytics-tile')).toHaveCount(4);
  await expect(page.locator('.tile-status').filter({ hasText: '5 groups' })).toHaveCount(4);
  const bar = page.locator('.analytics-tile').filter({ has: page.getByRole('heading', { name: 'bar grouped', exact: true }) });
  await expect(bar.locator('.bar-label').first()).toContainText('Branch');
  await expect(bar.locator('.bar-series-label').first()).toContainText('Alex');
  const line = page.locator('.analytics-tile').filter({ has: page.getByRole('heading', { name: 'line grouped', exact: true }) });
  await expect(line.locator('.line-chart path')).toHaveCount(2);
  await expect(line.locator('.line-chart circle')).toHaveCount(5);
  const paths = await line.locator('.line-chart path').evaluateAll(nodes => nodes.map(node => node.getAttribute('d')));
  expect(paths.some(path => (path.match(/M/g) || []).length === 2)).toBe(true);
  await expect(page.locator('.donut-entry').first()).toContainText('Alex');
  await bar.getByRole('heading').click();
  const expanded = page.getByRole('dialog', { name: 'bar grouped', exact: true });
  await expanded.locator('.bar-value-row').first().click();
  await expect(expanded.locator('.drill-body')).toContainText('Alex');
  const drill = requests.find(request => request.body.selection);
  expect(drill.body.selection).toEqual({ dimensions: [
    { table: 'person', column: 'org', value: 'Branch' },
    { table: 'person', column: 'name', value: 'Alex' },
  ], measureIndex: 0 });
  await expanded.getByRole('button', { name: 'Close expanded tile' }).click();
  await page.getByRole('button', { name: 'Edit bar grouped', exact: true }).click();
  const editor = page.getByRole('dialog', { name: 'Configure analytics tile' });
  for (const kind of ['Line chart', 'Donut chart', 'Aggregation report', 'Bar chart']) {
    await editor.getByRole('combobox', { name: 'Analytics view', exact: true }).fill(kind);
    await page.getByRole('option', { name: kind, exact: true }).click();
    await expect(editor.getByRole('button', { name: 'Remove Dimensions 2', exact: true })).toBeVisible();
  }
  await editor.getByRole('button', { name: 'Apply & run' }).click();
  await expect(editor).toHaveCount(0);
  expect(fixture.dashboard.tiles[0].dimensions).toEqual([field('org'), field('name')]);
  await page.reload();
  await expect(bar.locator('.bar-series-label').first()).toContainText('Alex');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});


test('large dimension legends scroll while compact plots stay visible and results do not overlap', async ({ page }) => {
  const { errors } = await mock(page, { multidimensional: 'many' });
  await page.goto('/schemer');
  await expect(page.locator('.tile-status').filter({ hasText: '150 groups' })).toHaveCount(4);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  for (const kind of ['line', 'donut']) {
    const tile = page.locator('.analytics-tile').filter({ has: page.getByRole('heading', { name: `${kind} grouped`, exact: true }) });
    const bounds = await tile.evaluate(node => {
      const body = node.querySelector('.tile-body').getBoundingClientRect();
      const plot = node.querySelector('.line-chart, .donut-chart').getBoundingClientRect();
      const legend = node.querySelector('.chart-legend, .donut-legend');
      return { bodyBottom: body.bottom, plotTop: plot.top, plotBottom: plot.bottom, legendHeight: legend.clientHeight, legendContent: legend.scrollHeight };
    });
    expect(bounds.plotTop).toBeLessThan(bounds.bodyBottom - 80);
    expect(bounds.legendContent).toBeGreaterThan(bounds.legendHeight);
    if (kind === 'donut') {
      await tile.locator('.tile-body').evaluate(node => { node.scrollTop = node.scrollHeight; });
      const legend = await tile.locator('.donut-legend').boundingBox();
      const status = await tile.locator('.stream-progress').boundingBox();
      expect(status.y).toBeGreaterThanOrEqual(legend.y + legend.height);
    }
  }
  expect(errors).toEqual([]);
});


test('wide line axes keep their first points visible before horizontal scrolling', async ({ page }) => {
  await mock(page, { multidimensional: 'wide' });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile').filter({ has: page.getByRole('heading', { name: 'line grouped', exact: true }) });
  await expect(tile.locator('.tile-status')).toContainText('150 groups');
  const body = await tile.locator('.tile-body').boundingBox();
  const point = await tile.locator('.line-chart circle').first().boundingBox();
  expect(point.x).toBeGreaterThanOrEqual(body.x);
  expect(point.x + point.width).toBeLessThan(body.x + body.width);
  expect(point.y + point.height).toBeLessThan(body.y + body.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});


test('bars cluster under one dimension with consistent colors and regroup without a query', async ({ page }) => {
  const { requests, errors } = await mock(page, { multidimensional: true });
  await page.goto('/schemer');
  const tile = page.locator('.analytics-tile').filter({ has: page.getByRole('heading', { name: 'bar grouped', exact: true }) });
  await expect(tile.locator('.bar-group')).toHaveCount(3);
  await expect(tile.locator('.bar-group').first().locator('.bar-value-row')).toHaveCount(2);
  const colors = await tile.locator('.bar-value-row').evaluateAll(nodes => nodes.map(node => [node.dataset.seriesKey, node.querySelector('.bar-mark').style.background]));
  const byKey = new Map();
  for (const [key, color] of colors) {
    if (byKey.has(key)) expect(color).toBe(byKey.get(key));
    else byKey.set(key, color);
  }
  expect(new Set(byKey.values()).size).toBe(2);
  await tile.getByRole('heading').click();
  const expanded = page.getByRole('dialog', { name: 'bar grouped', exact: true });
  const before = requests.length;
  await expanded.getByRole('combobox', { name: 'Group bars by' }).selectOption('1');
  await expect(expanded.locator('.bar-group')).toHaveCount(2);
  await expect(expanded.locator('.bar-label').first()).toHaveText('Alex');
  await expect(expanded.locator('.bar-group').first().locator('.bar-value-row')).toHaveCount(3);
  await expect(expanded.locator('.stream-progress')).toContainText('End of result');
  expect(requests).toHaveLength(before);
  await expanded.locator('.bar-group').first().locator('.bar-value-row').nth(1).click();
  await expect(expanded.locator('.drill-body')).toContainText('Alex');
  expect(requests.at(-1).body.selection.dimensions).toEqual([
    { table: 'person', column: 'org', value: 'HQ' },
    { table: 'person', column: 'name', value: 'Alex' },
  ]);
  expect(errors).toEqual([]);
});
