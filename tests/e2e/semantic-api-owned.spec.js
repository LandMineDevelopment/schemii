import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';

const CONNECTIONS = '/api/v1/connections';
const WORKSPACES = '/api/v1/schemii/workspaces';
const executionPath = id => `/api/v1/common/query-executions/${id}`;
const consoleId = () => `con_${randomUUID().replaceAll('-', '')}`;

async function json(response, expectedStatus) {
  expect(response.status(), await response.text()).toBe(expectedStatus);
  return response.json();
}

async function complete(request, id, expectedStatus) {
  let receipt;
  await expect.poll(async () => {
    receipt = await json(await request.get(executionPath(id)), 200);
    return receipt.status;
  }).toBe(expectedStatus);
  return receipt;
}

// Called on success and assertion failure. The ledger contains only resources
// whose creation returned to this test; no account-wide/list-based deletion.
async function cleanup(request, owned) {
  const errors = [];
  async function attempt(action) {
    try { await action(); } catch (error) { errors.push(error); }
  }
  for (const id of owned.executions) {
    await attempt(async () => {
      const cancelled = await request.delete(executionPath(id));
      if ([404, 410].includes(cancelled.status())) return;
      expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
    });
  }
  // Cancellation releases a blocked fetch, but closing its result/owner must
  // wait for the client transport too. Observe every rejection while the request
  // is in flight and retain it for cleanup if a prior assertion interrupted us.
  for (const pending of owned.pending) {
    await attempt(async () => {
      let outcome;
      try { outcome = await pending; } finally { owned.pending.delete(pending); }
      if (outcome.error) throw outcome.error;
    });
  }
  await attempt(async () => {
    expect(owned.pending.size, 'no owned result-page transport remains pending').toBe(0);
  });
  for (const id of owned.executions) {
    await attempt(async () => {
      const current = await request.get(executionPath(id));
      if ([404, 410].includes(current.status())) return;
      let receipt;
      await expect.poll(async () => {
        receipt = await json(await request.get(executionPath(id)), 200);
        return ['succeeded', 'failed', 'cancelled', 'expired'].includes(receipt.status);
      }).toBe(true);
      for (const result of receipt.results) {
        owned.results.add(`${executionPath(id)}/results/${result.id}`);
      }
      await expect.poll(async () => (await json(await request.get(`${executionPath(id)}/activity`), 200)).cancellable,
        { message: 'no owned result operation remains active before owner disposal' }).toBe(false);
    });
  }
  for (const result of owned.results) {
    await attempt(async () => {
      const closed = await request.delete(result);
      expect([204, 404, 410], await closed.text()).toContain(closed.status());
      expect([404, 410], 'closed owned result cannot be paged again').toContain((await request.get(result)).status());
    });
  }
  if (owned.session) {
    await attempt(async () => {
      const closed = await request.delete(owned.session);
      expect([204, 404], await closed.text()).toContain(closed.status());
      expect((await request.get(owned.session)).status()).toBe(404);
    });
  }
  for (const [base, resource] of [[WORKSPACES, owned.workspace], [CONNECTIONS, owned.connection]]) {
    if (!resource) continue;
    await attempt(async () => {
      const current = await request.get(`${base}/${resource.id}`);
      if (current.status() !== 404) {
        const document = await json(current, 200);
        const removed = await request.delete(`${base}/${resource.id}?expectedRevision=${document.revision}`);
        expect(removed.status(), await removed.text()).toBe(204);
      }
      expect((await request.get(`${base}/${resource.id}`)).status()).toBe(404);
    });
  }
  if (owned.connection) {
    for (const id of owned.executions) {
      await attempt(async () => {
        // Connection deletion also removes this connection's terminal receipts.
        expect((await request.get(executionPath(id))).status()).toBe(404);
      });
    }
  }
  if (errors.length) throw new AggregateError(errors, 'Owned semantic API fixture cleanup failed');
}

test('authenticated owned SQL completion, cancellation and session cleanup', async ({ request, playwright, baseURL }) => {
  // global-setup logs in normally using the private credential-file convention,
  // or explicitly bootstraps the disposable CI installation. Its session remains
  // owned by the suite; this test owns its SQL resources rather than logging out
  // the shared account session needed by other tests.
  const authentication = await json(await request.get('/api/v1/auth/status'), 200);
  expect(authentication.enabled).toBe(true);
  expect(authentication.authenticated).toBe(true);
  const anonymous = await playwright.request.newContext({
    baseURL, ignoreHTTPSErrors: true, storageState: { cookies: [], origins: [] },
  });
  try {
    expect((await anonymous.get('/api/v1/schemii/workspaces')).status()).toBe(401);
  } finally {
    await anonymous.dispose();
  }

  const secretDirectory = process.env.SCHEMII_SECRET_DIRECTORY || resolve('.schemii/secrets');
  const password = process.env.SCHEMII_TEST_POSTGRES_PASSWORD ||
    (await readFile(resolve(secretDirectory, 'demo_target_password'), 'utf8')).split('\n', 1)[0];
  expect(Boolean(password), 'Use target credentials generated by ./start.sh').toBe(true);
  const owned = { executions: new Set(), results: new Set(), pending: new Set() };
  let primaryFailure;
  let failed = false;
  try {
    owned.connection = await json(await request.post(CONNECTIONS, { data: {
      name: `Owned semantic API ${randomUUID()}`,
      host: 'postgres', port: 5432,
      database: process.env.SCHEMII_TEST_POSTGRES_DATABASE || 'schemii_test',
      username: process.env.SCHEMII_TEST_POSTGRES_USER || 'schemii',
      password, sslMode: 'disable',
    } }), 201);
    owned.workspace = (await json(await request.post(`${WORKSPACES}/postgres`, {
      data: { connectionId: owned.connection.id, namespace: 'public' },
    }), 201)).workspace;
    const settings = await json(await request.get('/api/v1/schemii/console/settings'), 200);
    const base = `${WORKSPACES}/${owned.workspace.id}/console`;
    const revision = { expectedWorkspaceRevision: owned.workspace.revision, expectedSettingsRevision: settings.revision };
    async function start(sql) {
      const receipt = await json(await request.post(`${base}/executions`, { data: {
        ...revision, consoleId: consoleId(), mode: 'managed_read', statements: [sql],
      } }), 201);
      owned.executions.add(receipt.id);
      return receipt.id;
    }

    const first = await start("WITH amounts(id, label, amount) AS (VALUES (1, 'Ada'::text, 100000000000000000001::numeric), (2, 'Bea', NULL::numeric)) SELECT * FROM amounts ORDER BY id");
    const completed = await complete(request, first, 'succeeded');
    expect(completed.results).toHaveLength(1);
    const resultPath = `${executionPath(first)}/results/${completed.results[0].id}`;
    owned.results.add(resultPath);
    const page = await json(await request.get(resultPath), 200);
    expect(page.rows).toEqual([[1, 'Ada', '100000000000000000001'], [2, 'Bea', null]]);
    expect(page.nextCursor).toBeNull();
    expect((await request.delete(resultPath)).status()).toBe(204);
    expect((await request.get(resultPath)).status()).toBe(410);

    const sleeping = await start('SELECT pg_sleep(20)');
    const prepared = await complete(request, sleeping, 'succeeded');
    expect(prepared.results).toHaveLength(1);
    const sleepingResult = `${executionPath(sleeping)}/results/${prepared.results[0].id}`;
    owned.results.add(sleepingResult);
    // Named cursors prepare successfully before SELECT executes. The page GET
    // performs the real fetch; do not replay this forward-only request.
    const pendingPage = request.get(sleepingResult).then(response => ({ response }), error => ({ error }));
    owned.pending.add(pendingPage);
    await expect.poll(async () => (await json(await request.get(`${executionPath(sleeping)}/activity`), 200)).waitEvent).toBe('PgSleep');
    const fetching = await json(await request.get(`${executionPath(sleeping)}/activity`), 200);
    expect(fetching.phase).toBe('fetching');
    expect(fetching.cancellable).toBe(true);
    expect(fetching.completedStatementIndexes).toEqual([]);
    const cancelled = await json(await request.delete(executionPath(sleeping)), 200);
    // Cancellation belongs to the fetch operation, not the immutable successful
    // preparation receipt. Its retained descriptor must not imply usable rows.
    expect(cancelled.status).toBe('succeeded');
    const outcome = await pendingPage;
    owned.pending.delete(pendingPage);
    if (outcome.error) throw outcome.error;
    const problem = await json(outcome.response, 502);
    expect(problem.error.code).toBe('postgres_console_cancelled');
    let stopped;
    await expect.poll(async () => {
      stopped = await json(await request.get(`${executionPath(sleeping)}/activity`), 200);
      return stopped.phase;
    }).toBe('cancelled');
    expect(stopped.cancellable).toBe(false);
    expect((await request.get(sleepingResult)).status()).toBe(410);
    expect((await request.delete(sleepingResult)).status()).toBe(204);
    expect(owned.pending.size).toBe(0);
    const recovered = await complete(request, await start('SELECT 42::integer AS recovered'), 'succeeded');
    expect(recovered.results).toHaveLength(1);
    const recoveryResult = `${executionPath(recovered.id)}/results/${recovered.results[0].id}`;
    owned.results.add(recoveryResult);
    const recoveryPage = await json(await request.get(recoveryResult), 200);
    expect(recoveryPage.rows).toEqual([[42]]);
    expect((await request.delete(recoveryResult)).status()).toBe(204);
    expect((await request.get(recoveryResult)).status()).toBe(410);

    const session = await json(await request.post(`${base}/sessions`, { data: { ...revision, consoleId: consoleId() } }), 201);
    owned.session = `${base}/sessions/${session.id}`;
    const begun = await json(await request.post(`${owned.session}/executions`, {
      data: { sql: 'BEGIN', commitMode: 'manual', expectedRevision: session.revision },
    }), 201);
    await expect.poll(async () => (await json(await request.get(`${owned.session}/executions/${begun.id}`), 200)).status).toBe('succeeded');
    expect((await json(await request.get(owned.session), 200)).transactionStatus).toBe('intrans');
    expect((await request.delete(owned.session)).status()).toBe(204);
    expect((await request.get(owned.session)).status()).toBe(404);
  } catch (error) {
    failed = true;
    primaryFailure = error;
    throw error;
  } finally {
    try {
      await cleanup(request, owned);
    } catch (error) {
      if (failed) {
        throw new AggregateError([primaryFailure, error],
          'Semantic API assertion and owned fixture cleanup both failed',
          { cause: primaryFailure });
      }
      throw error;
    }
  }
});
