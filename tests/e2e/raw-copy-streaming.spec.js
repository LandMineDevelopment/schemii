import { createHash, randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

// The ingress formerly buffered COPY into a 16 MiB /tmp filesystem. Exercise
// both directions above that boundary without creating any permanent tables.
test('COPY upload and download preserve 24 MiB through the canonical HTTPS ingress', async ({ request }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-chromium', 'API transport is independent of browser device; run the large transfer once.');
  test.setTimeout(120_000);
  const workspaces = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  expect(workspace, 'isolated bookstore seed workspace is required').toBeTruthy();
  const settings = await (await request.get('/api/v1/schemii/console/settings')).json();
  const base = `/api/v1/schemii/workspaces/${workspace.id}/console/sessions`;
  const opened = await request.post(base, { data: {
    consoleId: `con_${randomUUID().replaceAll('-', '')}`,
    expectedWorkspaceRevision: workspace.revision, expectedSettingsRevision: settings.revision,
  } });
  expect(opened.status(), await opened.text()).toBe(201);
  const session = `${base}/${(await opened.json()).id}`;
  const table = `copy_large_${randomUUID().replaceAll('-', '')}`;
  async function sql(source) {
    const response = await request.post(`${session}/executions`, { data: { sql: source, commitMode: 'each_statement' } });
    expect(response.status(), await response.text()).toBe(201);
    const receipt = await response.json();
    let execution;
    await expect.poll(async () => {
      execution = await (await request.get(`${session}/executions/${receipt.id}`)).json();
      return ['succeeded', 'failed', 'cancelled', 'uncertain'].includes(execution.status);
    }, { timeout: 30_000 }).toBe(true);
    expect(execution.status, JSON.stringify(execution)).toBe('succeeded');
    return execution;
  }
  try {
    await sql(`CREATE TEMP TABLE ${table} (seq text, payload text)`);
    const rowCount = 24_576, bytesPerRow = 1024;
    const payload = Buffer.alloc(rowCount * bytesPerRow);
    const value = 'x'.repeat(1017);
    for (let index = 0; index < rowCount; index++) {
      payload.write(`${String(index).padStart(5, '0')},${value}\n`, index * bytesPerRow, bytesPerRow, 'utf8');
    }
    expect(payload.byteLength).toBe(24 * 1024 * 1024);
    const upload = await request.post(`${session}/copy/uploads`, {
      data: { sql: `COPY ${table} FROM STDIN WITH (FORMAT CSV)`, commitMode: 'each_statement' },
    });
    expect(upload.status(), await upload.text()).toBe(201);
    const uploaded = await request.put(`${session}/copy/uploads/${(await upload.json()).id}`, {
      headers: { 'Content-Type': 'application/octet-stream' }, data: payload, timeout: 60_000,
    });
    expect(uploaded.ok(), await uploaded.text()).toBeTruthy();
    const count = await sql(`SELECT count(*), min(seq), max(seq), min(length(payload)), max(length(payload)) FROM ${table}`);
    expect(count.results.flatMap(result => result.rows || [])).toEqual([['24576', '00000', '24575', '1017', '1017']]);
    const ticket = await request.post(`${session}/copy/downloads`, {
      data: { sql: `COPY (SELECT seq, payload FROM ${table} ORDER BY seq) TO STDOUT WITH (FORMAT CSV)`, commitMode: 'each_statement' },
    });
    expect(ticket.status(), await ticket.text()).toBe(201);
    const path = `${session}/copy/downloads/${(await ticket.json()).id}`;
    const downloaded = await request.get(path, { timeout: 60_000 });
    expect(downloaded.status()).toBe(200);
    expect(downloaded.headers()['content-disposition']).toContain('attachment');
    const output = await downloaded.body();
    expect(output.byteLength).toBe(payload.byteLength);
    const digest = bytes => createHash('sha256').update(bytes).digest('hex');
    expect(digest(output)).toBe(digest(payload));
    expect((await (await request.get(`${path}/status`)).json()).status).toBe('succeeded');
    await downloaded.dispose();
  } finally {
    const closed = await request.delete(session);
    expect(closed.ok() || [404, 410].includes(closed.status()), await closed.text()).toBeTruthy();
  }
});
