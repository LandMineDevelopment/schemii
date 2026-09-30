import { randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { HTTPClient } from './http.mjs';
import { oracleFor, ProtocolFailure } from './protocol.mjs';
import { privateJSON, writeJSON, root } from '../harness/store.mjs';

const bad = code => { throw new ProtocolFailure(code); };
const fields = [{ table: 'orders', column: 'id' }];
export const explore = { root: 'orders', fields, limit: 100, selections: {}, reportFilters: [] };
export function rowOracles(columns) {
  return { rows: oracleFor(columns, Array.from({ length: 513 }, (_, i) => [i + 1])),
    csv: oracleFor(columns, Array.from({ length: 513 }, (_, i) => [String(i + 1)])) };
}
export async function compiledColumns(sql) {
  const python = existsSync(join(root, '.venv/bin/python')) ? join(root, '.venv/bin/python') : 'python';
  return new Promise((resolve, reject) => {
    const child = spawn(python, [join(root, 'testing/load/compiled_columns.py')], { stdio: ['pipe', 'pipe', 'ignore'] });
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; if (output.length > 16384) child.kill('SIGTERM'); });
    child.once('error', () => reject(new ProtocolFailure('compiled_plan_parser_unavailable')));
    child.once('close', code => { try { if (code !== 0) bad('invalid_compiled_plan'); resolve(JSON.parse(output)); } catch (error) { reject(error); } });
    child.stdin.on('error', () => {}); child.stdin.end(JSON.stringify(sql));
  });
}

export function ordinaryRequest(workload, account) {
  if (workload === 'cheap-read') return { method: 'GET', path: '/api/v1/auth/me', status: 200,
    equals: { 'user.id': account.userId, 'user.username': account.username } };
  if (workload === 'catalog') return { method: 'GET',
    path: `/api/v1/schemoo/catalog?connection_id=${account.source.connectionId}&namespace=${account.source.schema}`,
    status: 200, nonEmpty: ['tables'] };
  if (workload === 'compile') return { method: 'POST', path: `/api/v1/schemoo/models/${account.model.id}/plan`,
    body: { expectedRevision: account.model.revision, explore }, status: 200, requireSQL: true };
  bad('not_ordinary_workload');
}
export function streamRequest(workload, account) {
  const body = { modelId: account.model.id, expectedRevision: account.model.revision, explore };
  if (workload.startsWith('report-')) {
    const count = Number(workload.slice(7)), dashboard = account.dashboards[count];
    return { method: 'POST', path: `/api/v1/schemer/dashboards/${dashboard.id}/executions/stream`,
      body: { expectedRevision: dashboard.revision },
      tiles: Object.fromEntries(Array.from({ length: count }, (_, i) => [`tile-${i + 1}`, account.oracles.rows])) };
  }
  return { method: 'POST', path: `/api/v1/schemer/query/${workload === 'csv' ? 'export' : 'stream'}`, body,
    tiles: { report: account.oracles.rows } };
}

// Persist the creation intent before sending a write. On uncertain outcomes,
// reconcile only the exact run name absent from the pre-create owner listing.
// Retained accounts, source profiles, rows, and starter resources stay borrowed.
export async function prepareFixtures({ dir, accounts, credentialMap, stateDir, kind = 'cheap', origin, Client = HTTPClient }) {
  const file = join(dir, 'fixtures-private.json');
  let receipt;
  try { receipt = await privateJSON(file); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!receipt) receipt = { version: 1, tag: `load_${randomBytes(8).toString('hex')}`, kind, accounts: [] };
  if (receipt.kind !== kind || receipt.version !== 1) bad('fixture_receipt_changed');
  const registry = await privateJSON(join(stateDir, 'registry.json'));
  const slots = new Map(registry.slots.map(slot => [slot.username, slot]));
  const clients = new Map();
  const save = () => writeJSON(file, receipt);
  try {
    for (const username of accounts) {
      const slot = slots.get(username), credential = credentialMap.get(username);
      if (!slot?.provisioned || !slot.accountId || !credential) bad('unregistered_account');
      let account = receipt.accounts.find(item => item.username === username);
      if (!account) { account = { username, userId: slot.accountId, owned: [], pending: null }; receipt.accounts.push(account); await save(); }
      const client = new Client({ origin }); clients.set(username, client);
      const identity = await client.login(credential, account.userId);
      account.cookie = client.cookie; await save();
      if (kind === 'cheap') continue;
      if (slot.persona !== 'report_author' || !['schemoo:access', 'schemer:access', 'schemer:author'].every(cap => identity.capabilities.includes(cap)) ||
          !slot.connectionId || slot.schema !== username) bad('report_author_prerequisite');
      account.source = { connectionId: slot.connectionId, schema: slot.schema, database: 'schemii_qa', borrowed: true };
      const modelName = `${receipt.tag}_model`;
      if (!account.model) {
        const models = (await client.json('GET', '/api/v1/schemoo/models')).models;
        const existing = models.find(model => model.name === modelName);
        if (existing && account.pending?.name !== modelName) bad('unowned_name_collision');
        if (!existing) { account.pending = { kind: 'model', name: modelName }; await save(); }
        const model = existing || await client.json('POST', '/api/v1/schemoo/models', {
          name: modelName, connectionId: slot.connectionId, database: 'schemii_qa', namespace: slot.schema,
          definition: { root: 'orders', nodes: [{ id: 'orders', table: 'orders', label: 'Orders' }], edges: [], scopes: [] } }, 201);
        if (model.ownerId !== account.userId || model.connectionId !== slot.connectionId || model.namespace !== slot.schema) bad('model_ownership_changed');
        account.model = { id: model.id, revision: model.revision, name: modelName };
        account.owned.push({ kind: 'model', ...account.model }); account.pending = null; await save();
      }
      if (!account.oracles) {
        const compiled = await client.json('POST', `/api/v1/schemoo/models/${account.model.id}/plan`, {
          expectedRevision: account.model.revision, explore });
        const columns = await compiledColumns(compiled.sql);
        if (columns.length !== fields.length) bad('compiled_column_count');
        account.oracles = rowOracles(columns); await save();
      }
      account.dashboards ||= {};
      for (const count of [1, 5, 20]) {
        if (account.dashboards[count]) continue;
        const name = `${receipt.tag}_dashboard_${count}`;
        const dashboards = (await client.json('GET', '/api/v1/schemer/dashboards')).dashboards;
        const existing = dashboards.find(dashboard => dashboard.name === name);
        if (existing && account.pending?.name !== name) bad('unowned_name_collision');
        if (!existing) { account.pending = { kind: 'dashboard', name }; await save(); }
        const dashboard = existing || await client.json('POST', '/api/v1/schemer/dashboards', {
          name, modelId: account.model.id, modelRevision: account.model.revision,
          tiles: Array.from({ length: count }, (_, i) => ({ id: `tile-${i + 1}`, title: `Rows ${i + 1}`, kind: 'detail', detailFields: fields })) }, 201);
        if (dashboard.ownerId !== account.userId || dashboard.modelId !== account.model.id) bad('dashboard_ownership_changed');
        account.dashboards[count] = { id: dashboard.id, revision: dashboard.revision, name };
        account.owned.push({ kind: 'dashboard', ...account.dashboards[count] }); account.pending = null; await save();
      }
    }
    // Cross-owner object probes establish application isolation for these
    // private fixtures; they are distinct from browser cookie isolation.
    if (kind !== 'cheap' && receipt.accounts.length > 1) {
      for (let i = 0; i < receipt.accounts.length; i++) {
        const peer = receipt.accounts[(i + 1) % receipt.accounts.length];
        const response = await clients.get(receipt.accounts[i].username).request({ path: `/api/v1/schemoo/models/${peer.model.id}` });
        if (![403, 404].includes(response.status)) bad('cross_owner_access');
      }
      receipt.crossOwnerVerified = true;
    }
    await save();
    return receipt;
  } catch (error) {
    // Preserve cookies and ownership journal for guarded cleanup after failure.
    throw error;
  } finally { for (const client of clients.values()) client.close(); }
}

export async function cleanupFixtures({ dir, credentialMap, origin, Client = HTTPClient }) {
  const file = join(dir, 'fixtures-private.json');
  let receipt; try { receipt = await privateJSON(file); } catch (error) { if (error.code === 'ENOENT') return { objectsRemaining: 0 }; throw error; }
  if (receipt.version !== 1 || !/^load_[a-f0-9]{16}$/.test(receipt.tag)) bad('invalid_fixture_receipt');
  const save = () => writeJSON(file, receipt);
  let deleted = 0;
  for (const account of receipt.accounts) {
    const client = new Client({ origin });
    let cleanupAuthenticated = false;
    try {
      if (account.cleanupCookie) {
        const previous = new Client({ origin, cookie: account.cleanupCookie });
        try { await previous.logout(); } finally { previous.close(); }
        delete account.cleanupCookie; await save();
      }
      await client.login(credentialMap.get(account.username), account.userId);
      cleanupAuthenticated = true;
      account.cleanupCookie = client.cookie; await save();
      for (const execution of receipt.executions || []) {
        if (execution.username !== account.username || execution.closed) continue;
        if (!/^cex_[a-f0-9]{32}$/.test(execution.id)) bad('invalid_owned_execution');
        const current = await client.request({ path: `/api/v1/common/query-executions/${execution.id}` });
        if (current.status === 404 && current.data?.error?.code === 'console_execution_not_found') {
          // This exact receipt was captured in this owner's authenticated
          // response. Recheck owner identity before accepting process-local
          // receipt disappearance after a launcher rebuild.
          const identity = await client.json('GET', '/api/v1/auth/me');
          if (identity.user?.id !== account.userId || identity.user?.username !== account.username) bad('cleanup_identity_changed');
          execution.closed = true; await save(); continue;
        }
        if (current.status !== 200 || current.data?.id !== execution.id || !Array.isArray(current.data.results)) bad('cleanup_execution_read_failed');
        await client.json('DELETE', `/api/v1/common/query-executions/${execution.id}`);
        for (const result of current.data.results) await client.json('DELETE',
          `/api/v1/common/query-executions/${execution.id}/results/${result.id}`, undefined, 204);
        execution.closed = true; await save();
      }
      // A pending create may have reached the app before transport failure.
      if (account.pending) {
        const { kind, name } = account.pending;
        if (!['model', 'dashboard'].includes(kind) || !name.startsWith(`${receipt.tag}_`)) bad('invalid_pending_ownership');
        const collection = kind === 'model' ? '/api/v1/schemoo/models' : '/api/v1/schemer/dashboards';
        const list = await client.json('GET', collection);
        const found = list[kind === 'model' ? 'models' : 'dashboards'].filter(item => item.name === name);
        if (found.length > 1) bad('ambiguous_pending_create');
        if (found.length) account.owned.push({ kind, id: found[0].id, name, revision: found[0].revision });
        account.pending = null; await save();
      }
      for (const item of [...account.owned].reverse()) {
        if (!item.name.startsWith(`${receipt.tag}_`) || !['model', 'dashboard'].includes(item.kind)) bad('invalid_owned_resource');
        const collection = item.kind === 'model' ? '/api/v1/schemoo/models' : '/api/v1/schemer/dashboards';
        const current = await client.request({ path: `${collection}/${item.id}` });
        if (current.status === 200) {
          if (current.data.ownerId !== account.userId || current.data.name !== item.name ||
              (item.kind === 'model' && (current.data.connectionId !== account.source.connectionId || current.data.namespace !== account.source.schema)) ||
              (item.kind === 'dashboard' && current.data.modelId !== account.model.id)) bad('cleanup_ownership_changed');
          const parameter = item.kind === 'model' ? 'expected_revision' : 'expectedRevision';
          await client.json('DELETE', `${collection}/${item.id}?${parameter}=${current.data.revision}`, undefined, 204);
          deleted++;
        } else if (current.status !== 404) bad('cleanup_read_failed');
        const listing = await client.json('GET', collection);
        if (listing[item.kind === 'model' ? 'models' : 'dashboards'].some(value => value.id === item.id)) bad('cleanup_object_remaining');
        account.owned = account.owned.filter(value => value.id !== item.id); await save();
      }
      // Revoke the recorded load session as well as this cleanup login.
      const loadSession = new Client({ origin, cookie: account.cookie });
      try { await loadSession.logout(); } finally { loadSession.close(); }
      delete account.cookie; await save();
    } finally {
      // Destroying an HTTP agent does not revoke authentication. Always
      // attempt logout; a failed revocation remains a cleanup failure.
      try { if (cleanupAuthenticated) { await client.logout(); delete account.cleanupCookie; await save(); } }
      finally { client.close(); }
    }
  }
  return { deleted, objectsRemaining: receipt.accounts.reduce((n, account) => n + account.owned.length + Number(!!account.pending), 0), sessionsRevoked: true };
}
