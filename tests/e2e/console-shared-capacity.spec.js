import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
const id = () => `con_${randomUUID().replaceAll('-', '')}`;

test('retained reads and a raw transaction leave capacity for catalog access', async ({ request }) => {
  const { workspaces } = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.find(w => w.database === 'schemii_test' && w.namespace === 'bookstore');
  expect(workspace).toBeTruthy();
  const settings = await (await request.get('/api/v1/schemii/console/settings')).json();
  const base = `/api/v1/schemii/workspaces/${workspace.id}/console`;
  const revision = { expectedWorkspaceRevision: workspace.revision, expectedSettingsRevision: settings.revision };
  const retained = []; let session;
  async function read() {
    const response = await request.post(`${base}/executions`, { data: { ...revision, consoleId: id(), mode: 'managed_read', statements: ['SELECT generate_series(1, 10000) AS n'] } });
    expect(response.status(), await response.text()).toBe(201);
    const execution = await response.json(); let result;
    await expect.poll(async () => {
      result = await (await request.get(`${base}/executions/${execution.id}`)).json();
      return result.status;
    }).toBe('succeeded');
    retained.push(result);
  }
  try {
    for (let i = 0; i < 3; i++) await read();
    const opened = await request.post(`${base}/sessions`, { data: { ...revision, consoleId: id() } });
    expect(opened.status(), await opened.text()).toBe(201);
    session = await opened.json();
    const tx = await request.post(`${base}/sessions/${session.id}/executions`, { data: { sql: 'BEGIN', expectedRevision: session.revision, commitMode: 'manual' } });
    expect(tx.status(), await tx.text()).toBe(201);
    const started = await tx.json();
    await expect.poll(async () => (await (await request.get(`${base}/sessions/${session.id}/executions/${started.id}`)).json()).status).toBe('succeeded');
    await read();
    const catalog = await request.get(`/api/v1/schemii/workspaces/${workspace.id}/catalog`);
    expect(catalog.ok(), await catalog.text()).toBeTruthy();
    const current = await (await request.get(`${base}/sessions/${session.id}`)).json();
    expect(current.backendPid).toBe(session.backendPid);
    expect(current.transactionStatus).toBe('intrans');
  } finally {
    if (session) await request.delete(`${base}/sessions/${session.id}`);
    for (const execution of retained) for (const result of execution.results || []) {
      const response = await request.delete(`${base}/executions/${execution.id}/results/${result.id}`);
      expect([204, 404, 410]).toContain(response.status());
    }
  }
});
