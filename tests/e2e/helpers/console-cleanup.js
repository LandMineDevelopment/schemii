import { expect } from '@playwright/test';

/** Release only resources created by this test page, never other browser sessions. */
export function installConsoleCleanup(test) {
  const tracked = new WeakMap();
  test.beforeEach(async ({ page }) => {
    const state = { executions: new Set(), sessions: new Set(), pending: new Set() };
    state.listener = response => {
      const path = new URL(response.url()).pathname;
      if (response.request().method() !== 'POST' || !response.ok()) return;
      const raw = /^\/api\/v1\/schemii\/workspaces\/[^/]+\/console\/sessions$/.test(path);
      const managed = /^\/api\/v1\/schemii\/workspaces\/[^/]+\/console\/(executions|explain)$/.test(path);
      if (!raw && !managed) return;
      const pending = response.json().then(body => {
        if (raw && /^raw_[a-f0-9]{32}$/.test(body.id)) state.sessions.add(`${path}/${body.id}`);
        if (managed && /^cex_[a-f0-9]{32}$/.test(body.id)) state.executions.add(body.id);
      });
      state.pending.add(pending);
      // Keep rejected promises observed while retaining them for afterEach reporting.
      pending.catch(() => {});
    };
    page.on('response', state.listener);
    tracked.set(page, state);
  });
  test.afterEach(async ({ page, request }) => {
    const state = tracked.get(page);
    if (!state) return;
    page.off('response', state.listener);
    await Promise.all(state.pending);
    for (const session of state.sessions) {
      const response = await request.delete(session);
      expect(response.ok() || [404, 410].includes(response.status()), `close test raw session: ${await response.text()}`).toBeTruthy();
    }
    for (const id of state.executions) {
      const base = `/api/v1/common/query-executions/${id}`;
      const cancelled = await request.delete(base);
      if ([404, 410].includes(cancelled.status())) continue;
      expect(cancelled.ok(), `cancel test execution: ${await cancelled.text()}`).toBeTruthy();
      const response = await request.get(base);
      if ([404, 410].includes(response.status())) continue;
      expect(response.ok(), `get test execution: ${await response.text()}`).toBeTruthy();
      for (const result of (await response.json()).results || []) {
        const closed = await request.delete(`${base}/results/${result.id}`);
        expect(closed.ok() || [404, 410].includes(closed.status()), `close test result: ${await closed.text()}`).toBeTruthy();
      }
    }
    tracked.delete(page);
  });
}
