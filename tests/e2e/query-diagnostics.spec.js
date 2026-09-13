import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { installConsoleCleanup } from './helpers/console-cleanup.js';

installConsoleCleanup(test);

async function context(request) {
  const workspaces = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  expect(workspace, 'local bookstore seed is required for isolated diagnostics').toBeTruthy();
  const settings = await (await request.get('/api/v1/schemii/console/settings')).json();
  return { workspace, body: {
    consoleId: `con_${randomUUID().replaceAll('-', '')}`,
    expectedWorkspaceRevision: workspace.revision,
    expectedSettingsRevision: settings.revision,
  }, settings };
}

async function reserve(request, ctx, sql) {
  const response = await request.post(`/api/v1/schemii/workspaces/${ctx.workspace.id}/console/executions`, {
    data: { ...ctx.body, mode: 'managed_read', statements: [sql] },
  });
  expect(response.status()).toBe(201);
  const receipt = await response.json();
  const base = `/api/v1/common/query-executions/${receipt.id}`;
  let execution;
  await expect.poll(async () => {
    execution = await (await request.get(base)).json();
    return execution.status;
  }).toBe('succeeded');
  return { base, result: `${base}/results/${execution.results[0].id}`, execution };
}

for (const phase of ['first page', 'later page', 'export']) {
  test(`live monitoring and cancellation reach a slow ${phase}`, async ({ request }) => {
    const ctx = await context(request);
    const sql = phase === 'later page'
      ? `SELECT i, CASE WHEN i > ${ctx.settings.rowPageSize + 2} THEN pg_sleep(10) END FROM generate_series(1, 1000) i`
      : 'SELECT pg_sleep(10), 42 AS answer';
    const run = await reserve(request, ctx, sql);
    let url = phase === 'export' ? `${run.result}/export.csv` : run.result;
    if (phase === 'later page') {
      const first = await (await request.get(url)).json();
      expect(first.rows).toHaveLength(ctx.settings.rowPageSize);
      url += `?cursor=${encodeURIComponent(first.nextCursor)}`;
    }
    const pending = request.get(url, { timeout: 20000 }).catch(error => error);
    try {
      await expect.poll(async () => {
        const activity = await (await request.get(`${run.base}/activity`)).json();
        return activity.waitEvent;
      }).toBe('PgSleep');
      const activity = await (await request.get(`${run.base}/activity`)).json();
      expect(activity.cancellable).toBe(true);
      expect(activity.phase).toBe(phase === 'export' ? 'exporting' : 'fetching');
      expect(activity.completedStatementIndexes).toEqual([]);
      expect(activity.configuredStatementTimeoutMs).toBeGreaterThan(0);
      // An unrelated query and its page must remain responsive during the wait.
      const healthy = await reserve(request, { ...ctx, body: { ...ctx.body, consoleId: `con_${randomUUID().replaceAll('-', '')}` } }, 'SELECT 7 AS healthy');
      expect((await (await request.get(healthy.result, { timeout: 3000 })).json()).rows).toEqual([[7]]);
      await request.delete(healthy.result);
      const started = Date.now();
      const cancelled = await request.delete(run.base, { timeout: 3000 });
      expect(cancelled.ok()).toBeTruthy();
      await pending;
      expect(Date.now() - started).toBeLessThan(5000);
    } finally {
      await request.delete(run.base);
      await pending;
      await request.delete(run.result);
    }
  });
}

test('Explain, measured analysis, comparison and export work in the console', async ({ page, request }) => {
  const ctx = await context(request);
  await page.goto(`/?workspace=${ctx.workspace.id}&layer=sql`);
  const editor = page.getByRole('textbox', { name: 'Unsaved SQL draft' });
  await editor.fill('SELECT i FROM generate_series(1, 3) i WHERE i > 1;');
  await page.getByRole('button', { name: 'Explain', exact: true }).click();
  const plan = page.locator('.query-plan-view');
  await expect(plan.getByRole('columnheader', { name: 'Est. rows', exact: true })).toBeVisible();
  await expect(plan).toContainText('Filter');
  await page.getByRole('button', { name: 'Keep for comparison' }).click();
  if (await page.locator('#show-sql-editor').getAttribute('aria-expanded') !== 'true') {
    await page.locator('#show-sql-editor').click();
  }
  await page.getByRole('button', { name: 'Run & Analyze', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Run & Analyze', exact: true }).click();
  await expect(plan.getByRole('columnheader', { name: 'Actual rows', exact: true })).toBeVisible();
  await expect(plan).toContainText('Saved plan');
  await expect(plan).toContainText('Current plan');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export plan + SQL' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('query-plan.json');
  expect(await download.failure()).toBeNull();
  const exported = JSON.parse(await readFile(await download.path(), 'utf8'));
  expect(exported.sql).toContain('generate_series(1, 3)');
  expect(exported.analyze).toBe(true);
  expect(exported.plan.Plan['Actual Rows']).toBe(2);
  await page.screenshot({ path: `artifacts/query-plan-${test.info().project.name}.png`, fullPage: true });
});

test('Cancel remains usable while the first page is being fetched', async ({ page, request }) => {
  const ctx = await context(request);
  await page.goto(`/?workspace=${ctx.workspace.id}&layer=sql`);
  await page.getByRole('textbox', { name: 'Unsaved SQL draft' }).fill('SELECT pg_sleep(10)');
  await page.getByRole('button', { name: 'Run current statement' }).click();
  await expect(page.getByRole('region', { name: 'SQL workspace', exact: true }).locator('.sql-query-activity')).toContainText('PgSleep');
  const timer = page.getByRole('region', { name: 'SQL workspace', exact: true }).getByLabel('Query duration');
  await expect(timer).toContainText('Elapsed:');
  const before = await timer.textContent();
  await expect.poll(() => timer.textContent()).not.toBe(before);
  const cancel = page.locator('#cancel-sql-button');
  await expect(cancel).toBeEnabled();
  await cancel.click();
  await expect(page.locator('#sql-results')).toContainText(/cancel/i);
  await expect(timer).toContainText(/Stopped after|Cancelled after/);
});

test('blocked transaction shows its blocker and completed statement before rollback', async ({ request }) => {
  const ctx = await context(request);
  const path = `/api/v1/schemii/workspaces/${ctx.workspace.id}/console/transactions`;
  const transactions = [];
  const key = Math.floor(Math.random() * 1000000000);
  async function open() {
    const response = await request.post(path, { data: { ...ctx.body, consoleId: `con_${randomUUID().replaceAll('-', '')}` } });
    expect(response.status()).toBe(201);
    const tx = await response.json();
    transactions.push(tx.id);
    return tx;
  }
  async function execute(tx, statements) {
    const response = await request.post(`${path}/${tx.id}/executions`, { data: { expectedRevision: tx.revision, statements } });
    expect(response.status()).toBe(201);
    return `/api/v1/common/query-executions/${(await response.json()).id}`;
  }
  let waiting;
  let held;
  try {
    held = await reserve(request, ctx, `SELECT pg_advisory_xact_lock(${key})`);
    expect((await request.get(held.result)).ok()).toBeTruthy();
    const blocked = await open();
    waiting = await execute(blocked, ['SELECT 1', `SELECT pg_advisory_xact_lock(${key})`]);
    await expect.poll(async () => (await (await request.get(`${waiting}/activity`)).json()).blockerPids.length).toBeGreaterThan(0);
    const activity = await (await request.get(`${waiting}/activity`)).json();
    expect(activity.statementIndex).toBe(1);
    expect(activity.completedStatementIndexes).toContain(0);
    expect(activity.transactionStatus).toBe('open');
    expect(activity.waitEventType).toBe('Lock');
    await request.delete(waiting);
    await expect.poll(async () => (await (await request.get(waiting)).json()).status).toBe('cancelled');
  } finally {
    if (waiting) await request.delete(waiting);
    for (const id of transactions.reverse()) {
      const tx = await (await request.get(`${path}/${id}`)).json();
      if (['open', 'failed'].includes(tx.status)) {
        const rolledBack = await request.post(`${path}/${id}/rollback`, { data: { expectedRevision: tx.revision } });
        expect(rolledBack.ok()).toBeTruthy();
      }
    }
    if (held) await request.delete(held.result);
  }
});

test('reconnect recovers execution status without submitting SQL again', async ({ page, request }) => {
  const ctx = await context(request);
  let submissions = 0;
  page.on('request', req => {
    if (req.method() === 'POST' && req.url().endsWith('/console/executions')) submissions++;
  });
  let interrupted = false;
  await page.route(/\/console\/executions\/cex_[a-f0-9]+$/, async route => {
    if (!interrupted && route.request().method() === 'GET') {
      interrupted = true;
      await route.abort('failed');
    } else await route.continue();
  });
  await page.goto(`/?workspace=${ctx.workspace.id}&layer=sql`);
  await page.getByRole('textbox', { name: 'Unsaved SQL draft' }).fill('SELECT 12345 AS recovered');
  await page.getByRole('button', { name: 'Run current statement' }).click();
  await page.getByRole('button', { name: 'Reconnect to execution' }).click();
  await expect(page.locator('.sql-result-card tbody')).toContainText('12345');
  expect(submissions).toBe(1);
});
