import { expect, test } from '@playwright/test';

// All task metadata and writes are mocked; these cases require the normal
// deployed asset origin but never create fixtures or query a live source.
test.use({ trace: 'off', video: 'off' });
const ownerId = 'workflow-owner', connectionId = `pg_${'c'.repeat(32)}`, modelId = `model_${'d'.repeat(32)}`;
const field = (table, column, aggregate = 'none') => ({ table, column, aggregate });
function fixture() {
  const account = { user: { id: ownerId, username: 'workflow-owner' }, is_admin: false,
    capabilities: ['schemii:access', 'schemoo:access', 'schemer:access', 'schemer:author'] };
  const scope = (id, kind, requirement) => ({ id, label: id, kind, requirement,
    alternatives: [{ id: 'choose', label: 'Choose region', inputs: [{ id: 'region', label: 'Region', type: 'text' }],
      conditions: [{ table: 'orders', column: 'region', operator: 'eq', parameterId: 'region' }] }] });
  const choice = (value, active = true) => ({ alternativeId: 'choose', active, values: { region: value } });
  const explore = { root: 'orders', fields: [field('orders_archive', 'region'), field('orders', 'amount', 'sum')],
    selections: { always: choice('HQ'), conditional: choice('Branch'), optional: choice('Private region', false) },
    reportFilters: [{ id: 'report-region', label: 'Archive region', mode: 'exists', conditions: [{ table: 'orders_archive', column: 'region', operator: 'eq', value: 'East' }] }], limit: 25 };
  const model = { id: modelId, ownerId, name: 'Saved sales model', revision: 4, layoutRevision: 2, exploreRevision: 3,
    database: 'sales_db', namespace: 'sales', connectionId, catalogFingerprint: 'workflow-fingerprint',
    definition: { root: 'orders', nodes: [{ id: 'orders', table: 'orders', namespace: 'sales', label: 'Sales orders' },
      { id: 'orders_archive', table: 'orders', namespace: 'archive', label: 'Archived orders' }], edges: [],
    exposedFields: [field('orders', 'region'), field('orders', 'amount'), field('orders_archive', 'region')],
    scopes: [scope('always', 'required', 'required'), scope('conditional', 'conditional', 'required'), scope('optional', 'conditional', 'optional')] }, layout: {}, explore };
  const catalog = { database: 'sales_db', namespace: 'sales', namespaces: ['sales', 'archive'], fingerprint: 'workflow-fingerprint', notice: '', relationships: [],
    tables: ['sales', 'archive'].map(namespace => ({ name: 'orders', namespace, primaryKey: [], columns: [{ name: 'region', dataType: 'text' }, { name: 'amount', dataType: 'numeric' }] })) };
  return { account, model, catalog, explore, source: { connectionId, connectionOwnerId: ownerId, connectionRevision: 3, namespace: 'sales' },
    connections: [{ id: connectionId, ownerId, revision: 3, name: 'Sales source', database: 'sales_db', username: 'reader' }],
    calls: [], creates: [], saved: [], uncertain: false };
}

async function mock(page, state) {
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    state.calls.push({ path, method, body, search: url.search });
    let json = {};
    if (path === '/api/v1/auth/me') json = state.account;
    else if (path === '/api/v1/connections') json = { connections: state.connections };
    else if (path.endsWith('/namespaces')) json = { namespaces: [{ name: 'sales', system: false }, { name: 'archive', system: false }] };
    else if (path === '/api/v1/schemoo/catalog') {
      const namespaces = [...new Set([url.searchParams.get('namespace'), ...url.searchParams.getAll('namespaces')].filter(Boolean))];
      json = { ...state.catalog, namespaces, tables: state.catalog.tables.filter(table => namespaces.includes(table.namespace)) };
    }
    else if (path === '/api/v1/schemoo/models' && method === 'GET') json = { models: [state.model, ...state.saved.filter(item => item.id.startsWith('model_'))] };
    else if (path === `/api/v1/schemoo/models/${modelId}`) json = state.model;
    else if (path.includes('/previews')) json = { previews: [] };
    else if (path.endsWith('/validate') || path.endsWith('/plan')) json = { sql: 'SELECT compiler_output', warnings: [], usedRelationships: [], columns: [] };
    else if (path === '/api/v1/schemer/dashboards' && method === 'GET') json = { dashboards: [
      { id: 'unrelated-dashboard', ownerId, name: 'Unrelated existing dashboard', modelId, modelRevision: 4, revision: 1, tiles: [] },
      ...state.saved.filter(item => item.id.startsWith('dashboard_'))] };
    else if (['/api/v1/schemoo/models', '/api/v1/schemer/dashboards'].includes(path) && method === 'POST') {
      const id = path.endsWith('/models') ? `model_${'e'.repeat(32)}` : `dashboard_${'f'.repeat(32)}`;
      json = { id, ownerId, database: 'sales_db', revision: 1, layoutRevision: 1, exploreRevision: 1, catalogFingerprint: state.catalog.fingerprint, ...body };
      state.creates.push({ path, body }); state.saved.push(json);
      if (state.uncertain) return route.fulfill({ status: 503, json: { error: { message: 'Response lost after controlled save' } } });
    } else if (path.endsWith('/context')) json = { model: state.model, catalog: state.catalog, permissions: { edit: true, export: true, drill: true } };
    else if (path.endsWith('/executions/stream')) return route.fulfill({ contentType: 'application/x-ndjson', body: JSON.stringify({ type: 'start', snapshotAt: new Date().toISOString(), tiles: [], tileErrors: [] }) + '\n' + JSON.stringify({ type: 'end' }) + '\n' });
    else if (/\/schemer\/dashboards\//.test(path) || /\/schemoo\/models\//.test(path)) json = state.saved.find(item => path.endsWith(item.id)) || {};
    else if (path === '/api/v1/ai/status') json = { enabled: false, healthy: true, providers: [] };
    await route.fulfill({ json });
  });
  await page.route('**/workflow-context-fixture', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><head><script type="importmap">{"imports":{"#common/":"/assets/common/"}}</script></head><body><script type="module">import { createProductContext } from '/assets/common/product-context.js'; window.stageContext = (kind, account, payload) => createProductContext(kind, account, payload);</script></body></html>` }));
}
async function stage(page, state, kind, { age = 0 } = {}) {
  await page.goto('/workflow-context-fixture');
  await expect.poll(() => page.evaluate(() => typeof window.stageContext)).toBe('function');
  const payload = kind === 'schema' ? state.source : { modelId, modelRevision: 4, explore: state.explore };
  return page.evaluate(({ account, payload, kind, age }) => {
    const path = window.stageContext(kind, account, payload);
    if (age) {
      const key = 'schemii:product-context:v1', records = JSON.parse(sessionStorage.getItem(key));
      for (const value of Object.values(records)) { value.createdAt -= age; value.expiresAt -= age; }
      sessionStorage.setItem(key, JSON.stringify(records));
    }
    return path;
  }, { account: state.account, payload, kind, age });
}
const noFallback = state => expect(state.calls.some(call => /\/dashboards\/unrelated-dashboard/.test(call.path))).toBe(false);

test('Schemii live-source context opens Schemoo source and naming review with no arrival create', async ({ page }) => {
  const state = fixture(); await mock(page, state);
  await page.goto(await stage(page, state, 'schema'));
  const review = page.locator('#model-library');
  await expect(review).toContainText('Review schema context from Schemii');
  await expect(review.getByRole('combobox', { name: 'Source connection', exact: true })).toBeDisabled();
  await expect(review.getByRole('combobox', { name: 'Source schema', exact: true })).toBeDisabled();
  await expect(review.getByRole('button', { name: 'Create model', exact: true })).toBeEnabled();
  expect(state.creates).toEqual([]);
  await review.getByRole('checkbox', { name: 'Include schema archive', exact: true }).check();
  await review.getByRole('checkbox', { name: 'Include source archive.orders', exact: true }).check();
  await review.getByRole('textbox', { name: 'Model name', exact: true }).fill('Reviewed source model');
  await review.getByRole('button', { name: 'Create model', exact: true }).click();
  await expect.poll(() => state.creates.length).toBe(1);
  expect(state.creates[0].body).toMatchObject({ name: 'Reviewed source model', connectionId, namespace: 'sales', catalogNamespaces: ['sales', 'archive'] });
  expect(state.creates[0].body.definition.nodes.map(node => [node.namespace, node.table])).toEqual([['sales', 'orders'], ['archive', 'orders']]);
});

test('changed connection authority blocks Schemoo confirmation instead of choosing a similarly named source', async ({ page }) => {
  const state = fixture(); await mock(page, state);
  await page.goto(await stage(page, state, 'schema'));
  const review = page.locator('#model-library'), confirm = review.getByRole('button', { name: 'Create model', exact: true });
  await expect(confirm).toBeEnabled();
  state.connections[0].revision++;
  state.connections.push({ ...state.connections[0], id: `pg_${'b'.repeat(32)}`, revision: 3 });
  await confirm.click();
  await expect(review).toContainText('saved source connection changed');
  await expect(confirm).toBeDisabled(); expect(state.creates).toEqual([]);
});

test('uncertain model creation survives review-list refresh without a second POST', async ({ page }) => {
  const state = fixture(); state.uncertain = true; await mock(page, state);
  await page.goto(await stage(page, state, 'schema'));
  const review = page.locator('#model-library'), confirm = review.getByRole('button', { name: 'Create model', exact: true });
  await expect(confirm).toBeEnabled(); await confirm.click();
  await expect(review).toContainText('uncertain request will not be repeated');
  await review.getByRole('button', { name: 'Review model list', exact: true }).click();
  await expect(confirm).toBeDisabled();
  await expect(review).toContainText('possible saved model');
  expect(state.creates).toHaveLength(1);
});

test('saved preview reaches Schemer naming and tile review before unrelated-dashboard fallback', async ({ page }) => {
  const state = fixture(); await mock(page, state);
  const path = await stage(page, state, 'preview');
  expect(path).toMatch(/^\/schemer\?handoff=ctx_[a-f0-9]{32}$/);
  expect(path).not.toContain('Private');
  await page.goto(path);
  const review = page.getByRole('dialog', { name: 'Create dashboard from saved preview', exact: true });
  await expect(review.getByRole('button', { name: 'Create dashboard', exact: true })).toBeEnabled();
  await expect(review).toContainText('Archived orders [orders_archive]');
  await expect(review).toContainText('inactive');
  await expect(review.getByLabel('Initial report type', { exact: true })).toHaveValue('aggregate');
  expect(state.creates).toEqual([]); noFallback(state);
  expect(state.calls.some(call => /executions|\/query|\/console/.test(call.path))).toBe(false);
  await review.getByRole('textbox', { name: 'Dashboard name', exact: true }).fill('Reviewed sales');
  await review.getByRole('textbox', { name: 'Tile name', exact: true }).fill('Archive totals');
  await review.getByRole('button', { name: 'Create dashboard', exact: true }).click();
  await expect.poll(() => state.creates.length).toBe(1);
  const body = state.creates[0].body;
  expect(body).toMatchObject({ name: 'Reviewed sales', modelId, modelRevision: 4, optionalFilters: ['optional'],
    selections: { always: state.explore.selections.always, optional: state.explore.selections.optional } });
  expect(body.tiles[0]).toMatchObject({ title: 'Archive totals', kind: 'aggregate', dimensions: [state.explore.fields[0]], measures: [state.explore.fields[1]],
    selections: { conditional: state.explore.selections.conditional }, reportFilters: state.explore.reportFilters, limit: 25 });
  noFallback(state);
});

for (const failure of ['expired', 'wrong-account', 'denied', 'stale', 'root-mismatch']) {
  test(`contextual Schemer arrival rejects ${failure} without creating or opening unrelated saved work`, async ({ page }) => {
    const state = fixture(); await mock(page, state);
    const path = await stage(page, state, 'preview', { age: failure === 'expired' ? 300001 : 0 });
    if (failure === 'wrong-account') state.account.user.id = 'another-account';
    if (failure === 'denied') state.account.capabilities = ['schemii:access', 'schemoo:access'];
    if (failure === 'stale') state.model.revision = 5;
    if (failure === 'root-mismatch') state.model.definition.root = 'orders_archive';
    await page.goto(path);
    await expect(page.getByText(/expired|account changed|cannot create|saved model changed|preview root differs/i).first()).toBeVisible();
    expect(state.creates).toEqual([]); noFallback(state);
    expect(state.calls.some(call => /executions|\/query|\/console/.test(call.path))).toBe(false);
    if (['stale', 'root-mismatch'].includes(failure)) await expect(page.getByRole('button', { name: 'Create dashboard', exact: true }).last()).toBeDisabled();
  });
}

test('cancelled or replayed handoff never opens the first existing dashboard', async ({ page }) => {
  const state = fixture(); await mock(page, state);
  const path = await stage(page, state, 'preview'); await page.goto(path);
  const review = page.getByRole('dialog', { name: 'Create dashboard from saved preview', exact: true });
  await expect(review.getByRole('button', { name: 'Create dashboard', exact: true })).toBeEnabled();
  await review.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.reload();
  await expect(page.locator('#notice')).toContainText('already used');
  expect(state.creates).toEqual([]); noFallback(state);
});

test('leaving a contextual destination abandons its mounted review without creating', async ({ page }) => {
  const state = fixture(); await mock(page, state);
  for (const kind of ['schema', 'preview']) {
    await page.goto(await stage(page, state, kind));
    const review = kind === 'schema' ? page.locator('#model-library') : page.getByRole('dialog', { name: 'Create dashboard from saved preview', exact: true });
    await expect(review.getByRole('button', { name: kind === 'schema' ? 'Create model' : 'Create dashboard', exact: true })).toBeEnabled();
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
    await expect(review).toBeHidden();
  }
  expect(state.creates).toEqual([]); noFallback(state);
});

test('revision changes after review block confirmation and keep recovery in the review', async ({ page }) => {
  const state = fixture(); await mock(page, state);
  await page.goto(await stage(page, state, 'preview'));
  const review = page.getByRole('dialog', { name: 'Create dashboard from saved preview', exact: true });
  const confirm = review.getByRole('button', { name: 'Create dashboard', exact: true });
  await expect(confirm).toBeEnabled(); state.model.revision++;
  await confirm.click();
  await expect(review.getByRole('alert')).toContainText('saved model changed');
  await expect(confirm).toBeDisabled(); expect(state.creates).toEqual([]); noFallback(state);
});

test('raw summary outputs permit an explicit detail report choice in the destination review', async ({ page }) => {
  const state = fixture();
  state.model.definition.nodes.push({ id: 'summary', label: 'Order totals', derivation: { kind: 'aggregate', source: 'orders',
    groupBy: [], outputs: [{ id: 'total', operation: 'sum', column: 'amount' }] } });
  state.explore.fields = [field('summary', 'total')];
  await mock(page, state); await page.goto(await stage(page, state, 'preview'));
  const review = page.getByRole('dialog', { name: 'Create dashboard from saved preview', exact: true });
  await expect(review.getByRole('button', { name: 'Create dashboard', exact: true })).toBeEnabled();
  await expect(review.getByLabel('Initial report type', { exact: true })).toHaveValue('aggregate');
  await review.getByLabel('Initial report type', { exact: true }).selectOption('detail');
  await review.getByRole('button', { name: 'Create dashboard', exact: true }).click();
  await expect.poll(() => state.creates.length).toBe(1);
  expect(state.creates[0].body.tiles[0]).toMatchObject({ kind: 'detail', measures: [], dimensions: [], detailFields: [field('summary', 'total')] });
});

test('uncertain dashboard creation requires checking owned saved state and cannot repeat POST', async ({ page }) => {
  const state = fixture(); state.uncertain = true; await mock(page, state);
  await page.goto(await stage(page, state, 'preview'));
  const review = page.getByRole('dialog', { name: 'Create dashboard from saved preview', exact: true });
  const confirm = review.getByRole('button', { name: 'Create dashboard', exact: true });
  await expect(confirm).toBeEnabled(); await confirm.click();
  await expect(review.getByRole('alert')).toContainText('outcome is uncertain');
  await expect(confirm).toBeDisabled();
  await review.getByRole('button', { name: 'Check saved dashboards', exact: true }).click();
  await expect(review.getByRole('button', { name: 'Open saved dashboard Saved sales model dashboard', exact: true })).toBeVisible();
  expect(state.creates).toHaveLength(1); noFallback(state);
});

test('Schemoo preview handoff requires saving local changes and validates the saved starting object', async ({ page }) => {
  const state = fixture(); await mock(page, state);
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator('#model-name')).toHaveValue('Saved sales model');
  await page.locator('#model-name').fill('Local unsaved name');
  await page.getByRole('button', { name: 'Create dashboard from preview', exact: true }).click();
  await expect(page.locator('#error')).toContainText('Save model, layout and preview changes');
  expect(new URL(page.url()).pathname).toBe('/schemoo');
  expect(state.calls.some(call => call.path === '/api/v1/schemer/plan')).toBe(false);
  expect(state.creates).toEqual([]);
});
