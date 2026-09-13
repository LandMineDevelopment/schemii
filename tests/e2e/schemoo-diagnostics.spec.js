import { expect, test } from '@playwright/test';
import { importedDraft } from '../../src/schemii/schemoo/web/model-draft.js';
import { splitDraft } from '../../src/schemii/schemoo/web/model-state.js';
let modelId;
test.beforeEach(async ({ request }) => {
  const connection = (await (await request.get('/api/v1/connections')).json()).connections.find(c => c.database === 'organization');
  const catalog = await (await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const draft = importedDraft(catalog);
  draft.edges.forEach(e => { e.enabled = false; });
  draft.root = 'personnel_dim'; draft.fields = [{ table: 'personnel_dim', column: 'name' }]; draft.scopes = [];
  const response = await request.post('/api/v1/schemoo/models', { data: { name: `Diagnostics ${Date.now()}`, connectionId: connection.id, namespace: 'public', catalogFingerprint: catalog.fingerprint, ...splitDraft(draft) } });
  expect(response.ok(), await response.text()).toBeTruthy(); modelId = (await response.json()).id;
});
test.afterEach(async ({ request }) => {
  if (!modelId) return;
  const response = await request.get(`/api/v1/schemoo/models/${modelId}`);
  if (response.ok()) await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${(await response.json()).revision}`);
});
test('preview and physical plan show elapsed duration and release results', async ({ page }) => {
  const closed = [];
  page.on('request', request => { if (request.method() === 'DELETE' && /query-executions\/[^/]+\/results\//.test(request.url())) closed.push(request.url()); });
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator('#run')).toBeEnabled();
  await page.locator('#run').click();
  await expect(page.locator('#result-status')).toContainText(/rows · preview · .*s/);
  await expect.poll(() => closed.length).toBe(1);
  await page.getByRole('button', { name: 'Explain', exact: true }).click();
  await expect(page.locator('.query-plan-view')).toBeVisible();
  await expect(page.locator('#result-status')).toContainText(/Estimated plan · .*s/);
  await expect.poll(() => closed.length).toBe(2);
});
test('Stop stays available while lazy results wait', async ({ page }) => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  await page.route('**/api/v1/common/query-executions/*/results/*', async route => {
    if (route.request().method() === 'GET') { await waiting; await route.abort().catch(() => {}); }
    else await route.continue();
  });
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator('#run')).toBeEnabled();
  await page.locator('#run').click();
  try {
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeEnabled();
    await expect(page.locator('#result-status')).toContainText(/s/);
    await page.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect(page.locator('#result-status')).toContainText('Cancelled');
  } finally { release(); }
});
