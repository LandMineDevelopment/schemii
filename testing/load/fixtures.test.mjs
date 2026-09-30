import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import os from 'node:os';
import { prepareFixtures, cleanupFixtures, compiledColumns, rowOracles, streamRequest } from './fixtures.mjs';
import { NDJSONOracle, CSVOracle } from './protocol.mjs';
import { privateJSON, writeJSON } from '../harness/store.mjs';
import { ProtocolFailure } from './protocol.mjs';

async function fixture(t) {
  const dir = await mkdtemp(join(os.tmpdir(), 'schemii-load-fixture-test-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const names = ['qa_report_author_001', 'qa_report_author_002'];
  const slots = names.map((username, index) => ({ username, schema: username, persona: 'report_author',
    provisioned: true, accountId: `user-${index}`, connectionId: `pg_${String(index).repeat(32)}` }));
  await writeJSON(join(dir, 'registry.json'), { slots });
  const modelStore = new Map(names.map(name => [name, [{ id: `retained-${name}`, name: 'starter' }]]));
  const dashStore = new Map(names.map(name => [name, [{ id: `retained-${name}`, name: 'starter' }]]));
  // Fixture lifecycle is an early Node-only unit boundary. The real planner
  // and Python parser contract run in tests/test_load_planner.py after setup.
  const compiled = { sql: 'owned-fixture-plan' };
  const state = { crossOwner: false, failAfterCreate: false, drift: false, logouts: 0, logins: 0, intents: 0, serial: 0,
    executionResponse: null, failLogout: false, executionDeletes: 0 };
  class FakeClient {
    constructor({ cookie } = {}) { this.cookie = cookie || ''; }
    async login(credential, expected) {
      state.logins++;
      this.username = credential.username; this.userId = slots.find(slot => slot.username === this.username).accountId;
      assert.equal(this.userId, expected); this.cookie = `private=${this.username}`;
      return { capabilities: ['schemoo:access', 'schemer:access', 'schemer:author'] };
    }
    async json(method, path, body, expected = 200) {
      if (path === '/api/v1/auth/me') return { user: { id: this.userId, username: this.username } };
      if (path.endsWith('/plan') && method === 'POST') return compiled;
      if (path.startsWith('/api/v1/common/query-executions/') && method === 'DELETE') { state.executionDeletes++; return {}; }
      const kind = path.includes('/models') ? 'model' : 'dashboard';
      const store = kind === 'model' ? modelStore : dashStore, entries = store.get(this.username);
      const base = kind === 'model' ? '/api/v1/schemoo/models' : '/api/v1/schemer/dashboards';
      if (method === 'GET' && path === base) return { [kind === 'model' ? 'models' : 'dashboards']: entries };
      if (method === 'POST') {
        const journal = await privateJSON(join(dir, 'fixtures-private.json'));
        assert.equal(journal.accounts.find(account => account.username === this.username).pending.name, body.name);
        state.intents++;
        const item = { ...body, id: `${kind}_${(++state.serial).toString(16).padStart(32, '0')}`, ownerId: this.userId, revision: 1 };
        entries.push(item);
        if (state.failAfterCreate) { state.failAfterCreate = false; throw new ProtocolFailure('http_transport_failure'); }
        return item;
      }
      if (method === 'DELETE') {
        assert.equal(expected, 204);
        const id = path.slice(base.length + 1).split('?')[0];
        const item = entries.find(value => value.id === id);
        assert.ok(item.name.startsWith('load_'));
        entries.splice(entries.indexOf(item), 1); return null;
      }
      throw new Error('Unexpected test API call');
    }
    async request({ path }) {
      if (path.startsWith('/api/v1/common/query-executions/')) return state.executionResponse;
      const store = path.includes('/models') ? modelStore : dashStore;
      const id = path.split('/').at(-1), owned = store.get(this.username).find(value => value.id === id);
      if (!owned) return { status: state.crossOwner ? 200 : 404, data: {} };
      return { status: 200, data: state.drift ? { ...owned, ownerId: 'peer' } : owned };
    }
    async logout() { if (!this.cookie) return; state.logouts++; if (state.failLogout) throw new ProtocolFailure('logout_transport_failed'); this.cookie = ''; }
    close() {}
  }
  const credentialMap = new Map(names.map(username => [username, { username, password: 'private-secret' }]));
  return { dir, names, state, modelStore, dashStore, prepare: options => prepareFixtures({ dir, accounts: names,
    credentialMap, stateDir: dir, kind: 'reports', Client: FakeClient,
    readColumns: async sql => { assert.equal(sql, compiled.sql); return ['fixture-output.id']; }, ...options }),
    cleanup: () => cleanupFixtures({ dir, credentialMap, Client: FakeClient }) };
}
test('fixture row oracles preserve supplied compiled headers in NDJSON and CSV', () => {
  const columns = ['fixture-output.id'];
  const oracles = rowOracles(columns), account = { model: { id: `model_${'a'.repeat(32)}`, revision: 1 }, oracles,
    dashboards: { 1: { id: `dashboard_${'b'.repeat(32)}`, revision: 1 } } };
  const parser = new NDJSONOracle(streamRequest('report-1', account).tiles);
  const rows = Array.from({ length: 513 }, (_, i) => [i + 1]);
  for (const event of [{ type: 'start', tiles: [{ tileId: 'tile-1' }] },
    { type: 'execution', executionId: `cex_${'c'.repeat(32)}` },
    { type: 'rows', tileId: 'tile-1', columns: columns.map(name => ({ name })), rows },
    { type: 'complete', tileId: 'tile-1', rowCount: 513, limitReached: false, reason: null }, { type: 'end' }])
    parser.push(Buffer.from(JSON.stringify(event) + '\n'));
  assert.equal(parser.finish().rows, 513);
  const csv = new CSVOracle(oracles.csv);
  csv.push(Buffer.from(columns.join(',') + '\r\n' + rows.map(row => row.join(',') + '\r\n').join('')));
  assert.equal(csv.finish().rows, 513);
});
test('production compiled-column reader transports serialized SQL and parser output without invoking project Python', async () => {
  const columns = await compiledColumns('transport-only SQL', { launch: (_python, args, options) => {
    assert.match(args[0], /testing\/load\/compiled_columns\.py$/);
    assert.deepEqual(options.stdio, ['pipe', 'pipe', 'ignore']);
    return spawn(process.execPath, ['-e', `
      let input='';process.stdin.on('data',chunk=>input+=chunk);
      process.stdin.on('end',()=>{
        if(JSON.parse(input)!=='transport-only SQL')process.exit(2);
        console.log(JSON.stringify(['transport.column']));
      });
    `], options);
  } });
  assert.deepEqual(columns, ['transport.column']);
  await assert.rejects(compiledColumns('transport-only SQL', { launch: (_python, _args, options) =>
    spawn(process.execPath, ['-e', 'process.stdin.resume();process.stdin.on("end",()=>process.exit(1));'], options) }),
  { code: 'invalid_compiled_plan' });
});
test('owned fixture lifecycle journals each write, checks cross-owner denial and preserves retained objects', async t => {
  const f = await fixture(t), receipt = await f.prepare();
  assert.equal(receipt.crossOwnerVerified, true);
  for (const account of receipt.accounts) {
    assert.deepEqual(account.oracles.rows.columns, ['fixture-output.id']);
    assert.deepEqual(account.oracles.csv.columns, ['fixture-output.id']);
  }
  assert.equal(f.state.intents, 8);
  assert.equal((await stat(join(f.dir, 'fixtures-private.json'))).mode & 0o077, 0);
  assert.ok((await privateJSON(join(f.dir, 'fixtures-private.json'))).accounts.every(account => account.cookie));
  const result = await f.cleanup();
  assert.deepEqual(result, { deleted: 8, objectsRemaining: 0, sessionsRevoked: true });
  assert.ok((await privateJSON(join(f.dir, 'fixtures-private.json'))).accounts.every(account => !account.cookie));
  for (const name of f.names) {
    assert.equal(f.modelStore.get(name).length, 1); assert.equal(f.modelStore.get(name)[0].name, 'starter');
    assert.equal(f.dashStore.get(name).length, 1); assert.equal(f.dashStore.get(name)[0].name, 'starter');
  }
  assert.equal((await f.cleanup()).deleted, 0);
});
test('a disconnected create is reconciled from its persisted intent and cleaned without adopting unrelated names', async t => {
  const f = await fixture(t); f.state.failAfterCreate = true;
  await assert.rejects(f.prepare(), { code: 'http_transport_failure' });
  const journal = await privateJSON(join(f.dir, 'fixtures-private.json'));
  assert.equal(journal.accounts[0].pending.kind, 'model');
  const result = await f.cleanup();
  assert.equal(result.deleted, 1); assert.equal(f.modelStore.get(f.names[0]).length, 1);
});
test('cross-owner disclosure fails preparation and ownership drift blocks cleanup', async t => {
  const f = await fixture(t); f.state.crossOwner = true;
  await assert.rejects(f.prepare(), { code: 'cross_owner_access' });
  f.state.crossOwner = false; f.state.drift = true;
  const before = f.state.logouts;
  await assert.rejects(f.cleanup(), { code: 'cleanup_ownership_changed' });
  assert.equal(f.state.logouts, before + 1);
  assert.equal((await privateJSON(join(f.dir, 'fixtures-private.json'))).accounts[0].cleanupCookie, undefined);
  assert.equal(f.dashStore.get(f.names[0]).length, 4);
});
test('already-absent owned execution converges only for its exact code and verified authenticated owner', async t => {
  const f = await fixture(t); await f.prepare();
  const file = join(f.dir, 'fixtures-private.json'), receipt = await privateJSON(file);
  receipt.executions = [{ username: f.names[0], id: `cex_${'d'.repeat(32)}` }]; await writeJSON(file, receipt);
  f.state.executionResponse = { status: 404, data: { error: { code: 'console_execution_not_found' } } };
  assert.equal((await f.cleanup()).objectsRemaining, 0);
  assert.equal(f.state.executionDeletes, 0);
  assert.equal((await privateJSON(file)).executions[0].closed, true);
  assert.equal((await f.cleanup()).deleted, 0);
});
test('unknown execution404 and access failures retain ownership and revoke newly obtained cleanup authentication', async t => {
  const f = await fixture(t); await f.prepare();
  const file = join(f.dir, 'fixtures-private.json'), receipt = await privateJSON(file);
  receipt.executions = [{ username: f.names[0], id: `cex_${'e'.repeat(32)}` }]; await writeJSON(file, receipt);
  for (const response of [{ status: 404, data: { error: { code: 'unknown_resource' } } }, { status: 403, data: {} }]) {
    f.state.executionResponse = response; const before = f.state.logouts;
    await assert.rejects(f.cleanup(), { code: 'cleanup_execution_read_failed' });
    assert.equal(f.state.logouts, before + 1);
    const retained = await privateJSON(file);
    assert.ok(!retained.executions[0].closed); assert.equal(retained.accounts[0].owned.length, 4);
  }
});
test('failed cleanup revocation preserves its exact private cookie for recovery without silently creating another session', async t => {
  const f = await fixture(t); await f.prepare();
  f.state.drift = true; f.state.failLogout = true;
  await assert.rejects(f.cleanup(), { code: 'logout_transport_failed' });
  const receipt = await privateJSON(join(f.dir, 'fixtures-private.json'));
  assert.ok(receipt.accounts[0].cleanupCookie);
  const logins = f.state.logins;
  await assert.rejects(f.cleanup(), { code: 'logout_transport_failed' });
  assert.equal(f.state.logins, logins);
  assert.ok((await privateJSON(join(f.dir, 'fixtures-private.json'))).accounts[0].cleanupCookie);
  f.state.failLogout = false; f.state.drift = false;
  assert.equal((await f.cleanup()).objectsRemaining, 0);
});
