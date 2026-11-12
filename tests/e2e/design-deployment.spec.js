import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

const ROOT = '/api/v1/schemii/workspaces';
const connectionId = `pg_${'7'.repeat(32)}`, targetId = `ws_${'8'.repeat(32)}`;

// Mounted application/controller interactions with controlled target responses.
// Actual repository atomicity, live PostgreSQL and native QA are separate layers.
test('detached design copy requires exact target and collision review, preserves source, and opens fresh migration review', async ({ page, request }) => {
  const created = await request.post(ROOT, { data: { name: `Detached deployment ${randomUUID()}` } });
  expect(created.ok()).toBe(true); const workspace = await created.json();
  try {
    const snapshotResponse = await request.get(`${ROOT}/${workspace.id}/design/snapshot`);
    expect(snapshotResponse.ok()).toBe(true); const snapshot = await snapshotResponse.json();
    const target = { ...workspace, id: targetId, name: 'Reviewed target', connectionId, database: 'reporting', namespace: 'A "schema"' };
    const calls = []; let previewCount = 0;
    const preview = ready => ({ sourceWorkspaceId: workspace.id, targetWorkspaceId: targetId,
      sourceRevision: snapshot.design.revision, targetRevision: 3, targetLabel: 'reporting · A "schema" · author',
      summary: { title: '1 changed column', changeCount: 1, changes: [{ operation: 'changed', kind: 'column', name: 'Orders.id' }] },
      collisions: [{ key: 'collision', label: 'Orders / id', target: { dataType: 'integer' }, incoming: { dataType: 'bigint' }, resolution: ready ? 'incoming' : null }],
      warnings: ['Unqualified SQL names use target namespace A "schema".'], ready, reviewDigest: 'd'.repeat(64) });
    await page.route('**/api/v1/connections?product=schemii', route => route.fulfill({ json: { connections: [{ id: connectionId, revision: 1, name: 'Reporting', database: 'reporting', username: 'author' }] } }));
    await page.route(`**/api/v1/connections/${connectionId}/namespaces?product=schemii`, route => route.fulfill({ json: { namespaces: [{ name: target.namespace }] } }));
    await page.route(`**${ROOT}/postgres`, route => {
      calls.push(['target', route.request().postDataJSON()]); return route.fulfill({ json: { workspace: target, created: false } });
    });
    await page.route(`**${ROOT}/${workspace.id}/design/deployment-review`, route => {
      const body = route.request().postDataJSON(); calls.push(['review', body]); previewCount++;
      return route.fulfill({ json: preview(body.resolutions.collision === 'incoming') });
    });
    await page.route(`**${ROOT}/${workspace.id}/design/deployment-copy`, route => {
      calls.push(['copy', route.request().postDataJSON()]); return route.fulfill({ json: snapshot });
    });
    await page.route(`**${ROOT}/${targetId}/design/snapshot`, route => route.fulfill({ json: {
      ...snapshot, design: { ...snapshot.design, workspaceId: targetId, revision: 4 },
      layout: { ...snapshot.layout, workspaceId: targetId, designRevision: 4 },
      history: { ...snapshot.history, designRevision: 4 },
    } }));
    await page.route(`**${ROOT}/${targetId}/migration-executions*`, route => route.fulfill({ json: { executions: [] } }));
    await page.route(`**${ROOT}/${targetId}/migration-plans`, route => route.fulfill({ status: 409, json: { error: { code: 'fixture_review', message: 'Fresh target migration review requested', details: {} } } }));
    await page.goto(`/?workspace=${workspace.id}`);
    const open = page.getByRole('button', { name: 'Copy design to PostgreSQL target', exact: true });
    await expect(open).toBeEnabled(); await open.click();
    const dialog = page.getByRole('dialog', { name: 'Copy design to PostgreSQL target', exact: true });
    await expect(dialog).toBeVisible(); await page.locator('#deployment-connection').selectOption(connectionId);
    await page.locator('#deployment-namespace').selectOption(target.namespace);
    await dialog.getByRole('button', { name: 'Review copy', exact: true }).click();
    await expect(dialog).toContainText('Orders / id'); await expect(dialog).toContainText('Target-only objects');
    const copy = dialog.getByRole('button', { name: 'Copy and review migration', exact: true }); await expect(copy).toBeDisabled();
    await dialog.getByRole('combobox', { name: 'Resolve Orders / id' }).selectOption('incoming');
    await expect(copy).toBeDisabled(); await dialog.getByRole('button', { name: 'Review copy', exact: true }).click();
    await expect(copy).toBeEnabled(); await copy.click();
    await expect(dialog).toBeHidden(); await expect(page.locator('#migration-dialog')).toBeVisible();
    expect(previewCount).toBe(2); expect(calls.filter(([name]) => name === 'copy')).toHaveLength(1);
    expect(calls[0][1]).toEqual({ connectionId, namespace: target.namespace });
    expect(calls.at(-1)[1].reviewDigest).toBe('d'.repeat(64));
    const original = await request.get(`${ROOT}/${workspace.id}/design/snapshot`); expect(await original.json()).toEqual(snapshot);
  } finally {
    const current = await request.get(`${ROOT}/${workspace.id}`);
    if (current.ok()) await request.delete(`${ROOT}/${workspace.id}?expectedRevision=${(await current.json()).revision}`);
  }
});
