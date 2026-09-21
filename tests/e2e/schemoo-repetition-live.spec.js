import { expect, test } from '@playwright/test';
import { findOrganizationConnection, deleteModel } from './helpers/schemoo-model.js';

test.use({ actionTimeout: 10000 });

async function revealWarning(host) {
  const notice = host.locator('.aggregation-warning');
  if (!(await notice.evaluate(node => node.open))) {
    await notice.getByText('Aggregation warning', { exact: true }).click();
  }
}

// Real API, database, compiler, streaming results, and browser interactions.
// Only disposable model/dashboard metadata is created; source records stay intact.
test('repetition inspection and optional summary preserve intentional joined reporting', async ({ page, request }, testInfo) => {
  test.setTimeout(90000);
  const connection = findOrganizationConnection((await (await request.get('/api/v1/connections')).json()).connections);
  expect(connection, 'Existing Organization browser fixture is required').toBeTruthy();
  const catalog = await (await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const person = 'personnel_dim', child = 'personnel_certification_fact';
  const relationship = catalog.relationships.find(item => item.sourceTable === child && item.targetTable === person);
  expect(relationship).toBeTruthy();
  const fields = [{ table: child, column: 'certification_id', aggregate: 'none' }, { table: person, column: 'id', aggregate: 'count' }];
  const definition = {
    root: person,
    nodes: [{ id: person, table: person, label: 'Personnel' }, { id: child, table: child, label: 'Employee certifications' }],
    edges: [{ id: relationship.id, relationshipId: relationship.id, source: child, target: person, enabled: true }],
    scopes: [], exposedFields: null,
  };
  let modelId, dashboardId;
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    const created = await request.post('/api/v1/schemoo/models', { data: {
      name: `Repetition UI ${testInfo.project.name} ${Date.now()}`, connectionId: connection.id,
      namespace: 'public', catalogFingerprint: catalog.fingerprint, definition,
      layout: { positions: [{ id: person, x: 40, y: 40 }, { id: child, x: 400, y: 40 }] },
      explore: { root: person, fields, limit: 100 },
    } });
    expect(created.status(), await created.text()).toBe(201);
    const model = await created.json(); modelId = model.id;
    await page.goto(`/schemoo?model=${modelId}`);
    await page.getByRole('button', { name: 'Run preview', exact: true }).click();
    await expect(page.locator('#results table')).toBeVisible({ timeout: 30000 });
    await expect(page.locator('#error')).toBeHidden();
    const originalRows = await page.locator('#results tbody').innerText();
    expect(originalRows.length).toBeGreaterThan(0);
    await page.getByRole('button', { name: 'Explore model', exact: true }).click();
    await revealWarning(page);
    await page.getByRole('button', { name: 'Inspect repetition', exact: true }).click();
    const inspector = page.getByRole('dialog', { name: 'Inspect repetition', exact: true });
    await expect(inspector).toContainText('Personnel');
    await expect(inspector).toContainText('Employee certifications');
    const bounds = await inspector.boundingBox();
    const viewport = page.viewportSize();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
    await page.screenshot({ path: testInfo.outputPath('repetition-inspector.png') });
    await inspector.getByRole('button', { name: 'Create grouped summary', exact: true }).click();
    const summary = page.getByRole('dialog', { name: 'Add calculated source', exact: true });
    await expect(summary.getByRole('combobox', { name: 'Calculation kind', exact: true })).toHaveValue('Grouped summary · one row per group');
    await summary.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(summary).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Save model', exact: true })).toBeDisabled();
    const afterCancel = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
    expect(afterCancel.definition).toEqual(model.definition);
    expect(afterCancel.revision).toBe(model.revision);
    await page.getByRole('button', { name: 'Run preview', exact: true }).click();
    await expect(page.locator('#results tbody')).toHaveText(originalRows, { useInnerText: true });

    // Applying an optional summary is an explicit modeling choice. It does not
    // replace the author's selected joined-row measure or silence its warning.
    await page.getByRole('button', { name: 'Explore model', exact: true }).click();
    await revealWarning(page);
    await page.getByRole('button', { name: 'Inspect repetition', exact: true }).click();
    await inspector.getByRole('button', { name: 'Create grouped summary', exact: true }).click();
    await summary.getByRole('textbox', { name: 'Calculated source name', exact: true }).fill('Optional personnel summary');
    await summary.getByRole('checkbox', { name: 'id', exact: true }).check();
    await summary.getByRole('button', { name: 'Apply to model', exact: true }).click();
    await page.getByRole('button', { name: 'Save model', exact: true }).click();
    await expect(page.locator('#draft-status')).toContainText('Saved');
    const saved = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
    expect(saved.definition.nodes.find(node => node.label === 'Optional personnel summary').derivation.groupBy).toEqual(['id']);
    expect(saved.explore.fields).toEqual(model.explore.fields);
    await page.getByRole('button', { name: 'Run preview', exact: true }).click();
    await expect(page.locator('#results tbody')).toHaveText(originalRows, { useInnerText: true });
    await page.getByRole('button', { name: 'Explore model', exact: true }).click();
    await revealWarning(page);
    await page.getByRole('button', { name: 'Inspect repetition', exact: true }).click();
    await inspector.getByRole('button', { name: 'Edit Optional personnel summary', exact: true }).click();
    await page.getByRole('dialog', { name: 'Edit calculated source', exact: true }).getByRole('button', { name: 'Cancel', exact: true }).click();

    const dashboardResponse = await request.post('/api/v1/schemer/dashboards', { data: {
      name: `Intentional repetition ${testInfo.project.name} ${Date.now()}`, modelId, modelRevision: saved.revision,
      tiles: [{ id: 'counts', title: 'Personnel counts by certification', kind: 'aggregate',
        dimensions: [fields[0]], measures: [fields[1]],
        detailFields: [{ table: person, column: 'name', aggregate: 'none' }, fields[0]], limit: 100 }],
    } });
    expect(dashboardResponse.status(), await dashboardResponse.text()).toBe(201);
    dashboardId = (await dashboardResponse.json()).id;
    await page.goto(`/schemer?dashboard=${dashboardId}`);
    const tile = page.locator('.analytics-tile');
    await expect(tile.locator('.tile-status')).toContainText('groups', { timeout: 30000 });
    const reportRows = await tile.locator('tbody').innerText();
    await revealWarning(tile);
    await tile.getByRole('button', { name: 'Inspect repetition', exact: true }).click();
    await expect(inspector).toContainText('Personnel');
    await expect(inspector.getByRole('link', { name: 'Open model in Schemoo', exact: true })).toHaveAttribute('href', `/schemoo?model=${modelId}`);
    await inspector.getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByRole('button', { name: 'Refresh dashboard', exact: true }).click();
    await expect(tile.locator('tbody')).toHaveText(reportRows, { useInnerText: true });
    await tile.getByRole('heading').click();
    const expanded = page.getByRole('dialog', { name: 'Personnel counts by certification', exact: true });
    await expanded.getByRole('button', { name: /^View records for / }).first().click();
    await expect(expanded.locator('.drill-body tbody tr').first()).toBeVisible({ timeout: 30000 });
    await expanded.getByRole('button', { name: 'Personnel counts by certification', exact: true }).click();
    const downloaded = new Promise(resolve => {
      page.once('download', resolve);
      page.context().once('page', popup => popup.once('download', resolve));
    });
    await expanded.locator('.expanded-chart-pane').getByRole('button', { name: 'Download full results', exact: true }).click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    expect(await download.failure()).toBeNull();
    await page.screenshot({ path: testInfo.outputPath('repetition-report.png'), fullPage: true });
    expect(errors).toEqual([]);
  } finally {
    if (dashboardId) {
      const response = await request.get(`/api/v1/schemer/dashboards/${dashboardId}`);
      if (response.ok()) {
        const dashboard = await response.json();
        expect((await request.delete(`/api/v1/schemer/dashboards/${dashboardId}?expectedRevision=${dashboard.revision}`)).status()).toBe(204);
      }
    }
    await deleteModel(request, modelId);
  }
});
