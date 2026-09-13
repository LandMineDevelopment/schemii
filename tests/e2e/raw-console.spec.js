import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';

async function context(request) {
  const workspaces = await (await request.get('/api/v1/schemii/workspaces')).json();
  const workspace = workspaces.workspaces.find(item => item.database === 'schemii_test' && item.namespace === 'bookstore');
  expect(workspace, 'isolated bookstore seed workspace is required').toBeTruthy();
  const settings = await (await request.get('/api/v1/schemii/console/settings')).json();
  return { workspace, base: `/api/v1/schemii/workspaces/${workspace.id}/console/sessions`, body: {
    consoleId: `con_${randomUUID().replaceAll('-', '')}`,
    expectedWorkspaceRevision: workspace.revision,
    expectedSettingsRevision: settings.revision,
  } };
}

async function open(request, ctx) {
  const response = await request.post(ctx.base, { data: ctx.body });
  expect(response.status(), await response.text()).toBe(201);
  return `${ctx.base}/${(await response.json()).id}`;
}

async function completed(request, session, receipt) {
  let execution;
  await expect.poll(async () => {
    const response = await request.get(`${session}/executions/${receipt.id}`);
    expect(response.ok(), await response.text()).toBeTruthy();
    execution = await response.json();
    return ['succeeded', 'failed', 'cancelled', 'uncertain'].includes(execution.status);
  }).toBe(true);
  return execution;
}

async function run(request, session, sql, commitMode = 'manual') {
  const response = await request.post(`${session}/executions`, { data: { sql, commitMode } });
  expect(response.status(), await response.text()).toBe(201);
  return completed(request, session, await response.json());
}

function rows(execution) { return execution.results.flatMap(result => result.rows || []); }

async function succeeds(request, session, sql, mode = 'manual') {
  const result = await run(request, session, sql, mode);
  expect(result.status, JSON.stringify(result)).toBe('succeeded');
  return result;
}

test('raw session preserves temporary objects, settings, savepoints, and same-session plans', async ({ request }) => {
  const ctx = await context(request), session = await open(request, ctx);
  const table = `raw_${randomUUID().replaceAll('-', '')}`;
  try {
    await succeeds(request, session, `CREATE TEMP TABLE ${table} (id int); SET application_name = 'schemii raw session test';`, 'each_statement');
    const setting = await succeeds(request, session, "SELECT current_setting('application_name')", 'each_statement');
    expect(rows(setting)).toEqual([['schemii raw session test']]);
    await succeeds(request, session, `INSERT INTO ${table} VALUES (8)`);
    expect((await (await request.get(session)).json()).transactionStatus).toBe('intrans');
    await succeeds(request, session, 'ROLLBACK');
    await succeeds(request, session, `BEGIN; SAVEPOINT before_insert; INSERT INTO ${table} VALUES (1);`);
    expect((await run(request, session, 'SELECT 1 / 0')).sqlstate).toBe('22012');
    expect((await (await request.get(session)).json()).transactionStatus).toBe('inerror');
    await succeeds(request, session, 'ROLLBACK TO SAVEPOINT before_insert');
    expect(rows(await succeeds(request, session, `SELECT count(*) FROM ${table}`))).toEqual([['0']]);
    await succeeds(request, session, `INSERT INTO ${table} VALUES (2); COMMIT AND CHAIN;`);
    expect((await (await request.get(session)).json()).transactionStatus).toBe('intrans');
    await succeeds(request, session, 'COMMIT');
    expect((await (await request.get(session)).json()).transactionStatus).toBe('idle');
    const explain = await request.post(`${session}/explain`, { data: { sql: `SELECT * FROM ${table}`, analyze: true, commitMode: 'manual' } });
    expect(explain.status(), await explain.text()).toBe(201);
    const measured = await completed(request, session, await explain.json());
    expect(measured.status, JSON.stringify(measured)).toBe('succeeded');
    const plan = JSON.parse(rows(measured)[0][0]);
    expect(plan[0].Plan['Relation Name']).toBe(table);
    expect(plan[0].Plan['Actual Rows']).toBe(1);
  } finally { await request.delete(session); }
});

test('commit timing preserves per-statement commits and rolls back a failed whole run', async ({ request }) => {
  const ctx = await context(request), session = await open(request, ctx);
  const table = `raw_${randomUUID().replaceAll('-', '')}`;
  try {
    await succeeds(request, session, `CREATE TEMP TABLE ${table} (id int)`, 'each_statement');
    const partial = await run(request, session, `INSERT INTO ${table} VALUES (1); SELECT 1 / 0;`, 'each_statement');
    expect(partial.status).toBe('failed');
    expect(rows(await succeeds(request, session, `SELECT id FROM ${table}`, 'each_statement'))).toEqual([['1']]);
    const atomic = await run(request, session, `INSERT INTO ${table} VALUES (2); SELECT 1 / 0;`, 'whole_run');
    expect(atomic.status).toBe('failed');
    expect((await (await request.get(session)).json()).transactionStatus).toBe('idle');
    expect(rows(await succeeds(request, session, `SELECT id FROM ${table}`, 'each_statement'))).toEqual([['1']]);
    await succeeds(request, session, `VACUUM ANALYZE ${table}`, 'each_statement');
    const vacuum = await run(request, session, `VACUUM ${table}`, 'whole_run');
    expect(vacuum.status).toBe('failed');
    expect(vacuum.sqlstate).toBe('25001');
    await succeeds(request, session, 'BEGIN');
    const conflict = await request.post(`${session}/executions`, { data: { sql: `INSERT INTO ${table} VALUES (3)`, commitMode: 'each_statement' } });
    expect(conflict.status()).toBe(409);
    await succeeds(request, session, `INSERT INTO ${table} VALUES (3)`);
    expect((await (await request.get(session)).json()).transactionStatus).toBe('intrans');
    await succeeds(request, session, 'ROLLBACK');
    expect(rows(await succeeds(request, session, `SELECT id FROM ${table}`, 'each_statement'))).toEqual([['1']]);
  } finally { await request.delete(session); }
});

test('COPY streams CSV through the current session and preserves the explicit transaction', async ({ request }) => {
  const ctx = await context(request), session = await open(request, ctx);
  const table = `raw_${randomUUID().replaceAll('-', '')}`;
  try {
    await succeeds(request, session, `CREATE TEMP TABLE ${table} (id int, label text)`, 'each_statement');
    await succeeds(request, session, 'BEGIN');
    const upload = await request.post(`${session}/copy/uploads`, { data: { sql: `COPY ${table} FROM STDIN WITH (FORMAT CSV)` } });
    expect(upload.ok(), await upload.text()).toBeTruthy();
    const uploaded = await request.put(`${session}/copy/uploads/${(await upload.json()).id}`, {
      headers: { 'Content-Type': 'application/octet-stream' }, data: Buffer.from('1,"hello, world"\n2,café\n'),
    });
    expect(uploaded.ok(), await uploaded.text()).toBeTruthy();
    expect((await (await request.get(session)).json()).transactionStatus).toBe('intrans');
    const ticket = await request.post(`${session}/copy/downloads`, {
      data: { sql: `COPY (SELECT * FROM ${table} ORDER BY id) TO STDOUT WITH (FORMAT CSV)` },
    });
    expect(ticket.ok(), await ticket.text()).toBeTruthy();
    const path = `${session}/copy/downloads/${(await ticket.json()).id}`;
    const download = await request.get(path);
    expect(download.ok(), await download.text()).toBeTruthy();
    expect(download.headers()['content-disposition']).toContain('attachment');
    expect((await download.body()).toString('utf8')).toBe('1,"hello, world"\n2,café\n');
    expect((await (await request.get(`${path}/status`)).json()).status).toBe('succeeded');
    await succeeds(request, session, 'ROLLBACK');
    expect(rows(await succeeds(request, session, `SELECT count(*) FROM ${table}`))).toEqual([['0']]);
  } finally { await request.delete(session); }
});

async function editor(page, sql) {
  const toggle = page.locator('#show-sql-editor');
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  await page.getByRole('textbox', { name: 'Unsaved SQL draft' }).fill(sql);
}

async function uiRun(page, sql, all = false) {
  await editor(page, sql);
  await page.getByRole('button', { name: all ? 'Run all statements' : 'Run current statement', exact: true }).click();
  await expect(page.getByRole('region', { name: 'SQL workspace', exact: true }).getByLabel('Query duration')).toContainText(/Finished in|Time to first results|Completed in/);
}

test('raw console shows notices, same-session plans, elapsed time, and Stop', async ({ page, request }) => {
  const ctx = await context(request);
  let session;
  await page.goto(`/?workspace=${ctx.workspace.id}&layer=sql`);
  await page.locator('#write-mode-tool').click();
  await page.getByLabel('Auto-commit', { exact: true }).check();
  const opened = page.waitForResponse(response => response.url().endsWith('/console/sessions') && response.request().method() === 'POST');
  try {
    await uiRun(page, "CREATE TEMP TABLE raw_ui_probe (id int); INSERT INTO raw_ui_probe VALUES (7); DO $$ BEGIN RAISE NOTICE 'raw notice visible'; END $$;", true);
    session = `${ctx.base}/${(await (await opened).json()).id}`;
    const sqlWorkspace = page.getByRole('region', { name: 'SQL workspace', exact: true });
    await expect(sqlWorkspace.locator('.sql-query-notices')).toContainText('raw notice visible');
    await editor(page, 'SELECT * FROM raw_ui_probe');
    await page.getByRole('button', { name: 'Explain', exact: true }).click();
    await expect(page.locator('.query-plan-view')).toContainText('raw_ui_probe');
    await editor(page, 'SELECT pg_sleep(20)');
    await page.getByRole('button', { name: 'Run current statement', exact: true }).click();
    const timer = page.getByRole('region', { name: 'SQL workspace', exact: true }).getByLabel('Query duration');
    await expect(timer).toContainText('Elapsed:');
    const before = await timer.textContent();
    await expect.poll(() => timer.textContent()).not.toBe(before);
    await expect(sqlWorkspace.locator('.sql-query-activity')).toContainText('PgSleep');
    await page.locator('#cancel-sql-button').click();
    await expect(timer).toContainText(/Stopped after|Cancelled after/);
    await uiRun(page, 'SELECT 9 AS recovered');
    await expect(page.locator('#sql-results')).toContainText('9');
  } finally {
    if (!session) session = `${ctx.base}/${(await (await opened).json()).id}`;
    await request.delete(session);
  }
});

test('COPY dialog streams an uploaded file back through the native browser download', async ({ page, request }) => {
  const ctx = await context(request);
  // Exercise the native streaming fallback on desktop and mobile alike.
  await page.addInitScript(() => { window.showSaveFilePicker = undefined; });
  await page.goto(`/?workspace=${ctx.workspace.id}&layer=sql`);
  await page.locator('#write-mode-tool').click();
  await page.getByLabel('Auto-commit', { exact: true }).check();
  const opened = page.waitForResponse(response => response.url().endsWith('/console/sessions') && response.request().method() === 'POST');
  let session;
  try {
    await uiRun(page, 'CREATE TEMP TABLE raw_copy_ui (id int, label text)');
    session = `${ctx.base}/${(await (await opened).json()).id}`;
    await editor(page, 'COPY raw_copy_ui FROM STDIN WITH (FORMAT CSV)');
    await page.getByRole('button', { name: 'COPY file', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'COPY data' });
    await dialog.getByLabel('COPY input file').setInputFiles({ name: 'rows.csv', mimeType: 'text/csv', buffer: Buffer.from('1,first\n2,second\n') });
    await dialog.getByRole('button', { name: 'Upload file · FROM STDIN' }).click();
    await expect(dialog).toContainText('COPY upload completed.');
    await page.screenshot({ path: `artifacts/raw-copy-${test.info().project.name}.png`, fullPage: true });
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await editor(page, 'COPY (SELECT * FROM raw_copy_ui ORDER BY id) TO STDOUT WITH (FORMAT CSV)');
    await page.getByRole('button', { name: 'COPY file', exact: true }).click();
    const downloading = page.waitForEvent('download');
    await dialog.getByRole('button', { name: 'Download · TO STDOUT' }).click();
    const downloaded = await downloading;
    expect(await downloaded.failure()).toBeNull();
    expect(await readFile(await downloaded.path(), 'utf8')).toBe('1,first\n2,second\n');
    await expect(dialog).toContainText('COPY completed.');
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  } finally {
    if (!session) session = `${ctx.base}/${(await (await opened).json()).id}`;
    await request.delete(session);
  }
});

test('Open transactions shows SQL and commits or rolls back individual and listed sessions', async ({ page, request }) => {
  const ctx = await context(request), sessions = [];
  const table = `raw_popup_${randomUUID().replaceAll('-', '')}`;
  try {
    for (let index = 0; index < 2; index++) {
      const session = await open(request, { ...ctx, body: { ...ctx.body, consoleId: `con_${randomUUID().replaceAll('-', '')}` } });
      sessions.push(session);
      await succeeds(request, session, `CREATE TEMP TABLE ${table} (id int)`, 'each_statement');
      await succeeds(request, session, `INSERT INTO ${table} VALUES (${index + 1})`);
    }
    const ids = sessions.map(session => session.split('/').at(-1));
    // "All" must never act on an unrelated user's existing database session.
    // The list is filtered to test-owned live sessions; every mutation stays real.
    await page.route(`**${ctx.base}`, async route => {
      if (route.request().method() !== 'GET') return route.continue();
      const response = await route.fetch();
      const body = await response.json();
      const allowed = item => ids.includes(item.id);
      const filtered = Array.isArray(body) ? body.filter(allowed) : { ...body, sessions: body.sessions.filter(allowed) };
      await route.fulfill({ response, json: filtered });
    });
    await page.goto(`/?workspace=${ctx.workspace.id}&layer=sql`);
    await page.getByRole('button', { name: 'Open transactions', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: 'Open transactions', exact: true });
    await expect(dialog).toContainText(`INSERT INTO ${table} VALUES (1)`);
    await expect(dialog).toContainText(`INSERT INTO ${table} VALUES (2)`);
    for (const details of await dialog.locator('article details').all()) await details.locator('summary').click();
    await page.screenshot({ path: `artifacts/raw-transactions-${test.info().project.name}.png`, fullPage: true });
    await dialog.locator(`[data-session-id="${ids[0]}"]`).getByRole('button', { name: 'Commit', exact: true }).click();
    await page.getByRole('dialog', { name: /^Commit (all )?transactions?\?$/ }).getByRole('button', { name: 'Commit', exact: true }).click();
    await expect.poll(async () => (await (await request.get(sessions[0])).json()).transactionStatus).toBe('idle');
    await dialog.getByRole('button', { name: 'Roll back all', exact: true }).click();
    await page.getByRole('dialog', { name: /^Roll back (all )?transactions?\?$/ }).getByRole('button', { name: 'Roll back', exact: true }).click();
    await expect.poll(async () => (await (await request.get(sessions[1])).json()).transactionStatus).toBe('idle');
    expect(rows(await succeeds(request, sessions[0], `SELECT id FROM ${table}`, 'each_statement'))).toEqual([['1']]);
    expect(rows(await succeeds(request, sessions[1], `SELECT id FROM ${table}`, 'each_statement'))).toEqual([]);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await succeeds(request, sessions[0], `INSERT INTO ${table} VALUES (3)`);
    await succeeds(request, sessions[1], `INSERT INTO ${table} VALUES (4)`);
    await page.getByRole('button', { name: 'Open transactions', exact: true }).click();
    dialog = page.getByRole('dialog', { name: 'Open transactions', exact: true });
    await dialog.locator(`[data-session-id="${ids[0]}"]`).getByRole('button', { name: 'Roll back', exact: true }).click();
    await page.getByRole('dialog', { name: /^Roll back (all )?transactions?\?$/ }).getByRole('button', { name: 'Roll back', exact: true }).click();
    await expect.poll(async () => (await (await request.get(sessions[0])).json()).transactionStatus).toBe('idle');
    await dialog.getByRole('button', { name: 'Commit all', exact: true }).click();
    await page.getByRole('dialog', { name: /^Commit (all )?transactions?\?$/ }).getByRole('button', { name: 'Commit', exact: true }).click();
    await expect.poll(async () => (await (await request.get(sessions[1])).json()).transactionStatus).toBe('idle');
    expect(rows(await succeeds(request, sessions[0], `SELECT id FROM ${table}`, 'each_statement'))).toEqual([['1']]);
    expect(rows(await succeeds(request, sessions[1], `SELECT id FROM ${table}`, 'each_statement'))).toEqual([['4']]);
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  } finally { for (const session of sessions) await request.delete(session); }
});
