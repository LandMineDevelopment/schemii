import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

test('bulk jobs commit explicit batches, show elapsed time and retain their records', async ({ request }) => {
  const { workspaces } = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  expect(workspace).toBeTruthy();
  const settings = await (await request.get('/api/v1/schemii/console/settings')).json();
  const table = `e2e_bulk_${randomUUID().replaceAll('-', '')}`;
  const base = `/api/v1/schemii/workspaces/${workspace.id}/console/bulk-jobs`;
  const common = { consoleId: `con_${randomUUID().replaceAll('-', '')}`, expectedWorkspaceRevision: workspace.revision, expectedSettingsRevision: settings.revision };
  let job;
  async function current(id) { return (await request.get(`${base}/${id}`)).json(); }
  async function completed(id) {
    await expect.poll(async () => (await current(id)).status, { timeout: 15000 }).toBe('completed');
    return current(id);
  }
  try {
    const created = await request.post(base, { data: { ...common, name: `Bulk test ${table}`, batches: [
      { sql: `CREATE TABLE bookstore.${table} (id integer PRIMARY KEY); INSERT INTO bookstore.${table} VALUES (1);` },
      { sql: `SELECT pg_sleep(1); INSERT INTO bookstore.${table} VALUES (2);` },
    ] } });
    expect(created.status(), await created.text()).toBe(201);
    job = await created.json();
    const done = await completed(job.id);
    expect(done.completedBatches).toBe(2);
    expect(done.elapsedMs).toBeGreaterThanOrEqual(1000);
    expect((await current(job.id)).name).toBe(`Bulk test ${table}`);
    const response = await request.post(`/api/v1/schemii/workspaces/${workspace.id}/console/executions`, { data: { ...common, mode: 'managed_read', statements: [`SELECT id FROM bookstore.${table} ORDER BY id`] } });
    const receipt = await response.json();
    const readBase = `/api/v1/common/query-executions/${receipt.id}`;
    let execution;
    await expect.poll(async () => { execution = await (await request.get(readBase)).json(); return execution.status; }).toBe('succeeded');
    const resultPath = `${readBase}/results/${execution.results[0].id}`;
    try { expect((await (await request.get(resultPath)).json()).rows).toEqual([[1], [2]]); }
    finally { await request.delete(resultPath); }
  } finally {
    // Only the uniquely named test table is removed; retain no test job records.
    const cleanupResponse = await request.post(base, { data: { ...common, name: `Cleanup ${table}`, batches: [{ sql: `DROP TABLE IF EXISTS bookstore.${table}` }] } });
    expect(cleanupResponse.status()).toBe(201);
    const cleanup = await cleanupResponse.json();
    const cleanupDone = await completed(cleanup.id);
    await request.delete(`${base}/${cleanup.id}?expectedRevision=${cleanupDone.revision}`);
    if (job?.id) {
      const latest = await current(job.id);
      await request.delete(`${base}/${job.id}?expectedRevision=${latest.revision}`);
    }
  }
});

test('stopping a bulk job rolls back its active batch and resume skips committed batches', async ({ request }) => {
  const { workspaces } = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  const settings = await (await request.get('/api/v1/schemii/console/settings')).json();
  const common = { consoleId: `con_${randomUUID().replaceAll('-', '')}`, expectedWorkspaceRevision: workspace.revision, expectedSettingsRevision: settings.revision };
  const base = `/api/v1/schemii/workspaces/${workspace.id}/console/bulk-jobs`;
  const table = `e2e_bulk_${randomUUID().replaceAll('-', '')}`;
  let job;
  async function current(id) { return (await request.get(`${base}/${id}`)).json(); }
  try {
    const response = await request.post(base, { data: { ...common, name: `Resume ${table}`, batches: [
      { sql: `CREATE TABLE bookstore.${table} (id integer PRIMARY KEY); INSERT INTO bookstore.${table} VALUES(1)` },
      { sql: `INSERT INTO bookstore.${table} VALUES(2); SELECT pg_sleep(3)` },
    ] } });
    expect(response.status()).toBe(201); job = await response.json();
    let running;
    await expect.poll(async () => { running = await current(job.id); return running.batches[1].status; }).toBe('running');
    const cancelled = await request.post(`${base}/${job.id}/cancel`, { data: { expectedRevision: running.revision }, timeout: 5000 });
    expect(cancelled.ok()).toBeTruthy();
    let paused;
    await expect.poll(async () => { paused = await current(job.id); return paused.status; }).toBe('paused');
    expect(paused.completedBatches).toBe(1);
    const resumed = await request.post(`${base}/${job.id}/resume`, { data: { expectedRevision: paused.revision, expectedWorkspaceRevision: workspace.revision, expectedSettingsRevision: settings.revision } });
    expect(resumed.ok(), await resumed.text()).toBeTruthy();
    await expect.poll(async () => (await current(job.id)).status, { timeout: 10000 }).toBe('completed');
    // Replaying either committed batch or an unrolled-back insert would violate the primary key.
    expect((await current(job.id)).completedBatches).toBe(2);
  } finally {
    if (job?.id) {
      const latest = await current(job.id);
      if (['running', 'queued', 'cancelling'].includes(latest.status)) {
        await request.post(`${base}/${job.id}/cancel`, { data: { expectedRevision: latest.revision } });
        await expect.poll(async () => (await current(job.id)).status).not.toBe('running');
      }
    }
    const response = await request.post(base, { data: { ...common, name: `Cleanup ${table}`, batches: [{ sql: `DROP TABLE IF EXISTS bookstore.${table}` }] } });
    expect(response.status()).toBe(201);
    const cleanup = await response.json();
    await expect.poll(async () => (await current(cleanup.id)).status).toBe('completed');
    for (const id of [cleanup.id, job?.id].filter(Boolean)) {
      const latest = await current(id);
      await request.delete(`${base}/${id}?expectedRevision=${latest.revision}`);
    }
  }
});
