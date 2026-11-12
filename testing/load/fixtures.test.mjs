import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import os from 'node:os';
import https from 'node:https';
import tls from 'node:tls';
import { EventEmitter } from 'node:events';
import { prepareFixtures, cleanupFixtures, compiledColumns, rowOracles, streamRequest, prepareOwnedFixtures, cleanupOwnedFixtures, createOwnedResource, heldFixture, disposedFixtureConsumer, fixtureCommand, LOAD_FIXTURE_PROFILE } from './fixtures.mjs';
import { plan } from './plan.mjs';
import { readSnapshot, writeSnapshot } from './journal.mjs';
import { NDJSONOracle, CSVOracle } from './protocol.mjs';
import { privateJSON, writeJSON } from '../harness/store.mjs';
import { ProtocolFailure } from './protocol.mjs';
import { HTTPClient, LOCAL, PREVIEW } from './http.mjs';

test('held consumer binds the fixed representative profile, source mapping and fresh owner; restored cleanup requires joined exact consumer', async t => {
  const dir = await mkdtemp(join(os.tmpdir(), 'schemii-held-fixture-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const run = 'pressure_' + 'a'.repeat(32), nonce = 'b'.repeat(64), owner = { pid: 123, start: '456' };
  const principal = { run, username: run, userId: 'fixture-admin', passwordFile: join(dir, 'admin.json') };
  // JSON strings are suitable private test secret bytes; no live credentials.
  await writeSnapshot(principal.passwordFile, 'fixture-admin-password');
  await writeSnapshot(join(dir, 'source.json'), 'fixture-source-password');
  const value = { version: 1, kind: 'launcher_owned_disposable_pressure', status: 'held', run, nonce, owner, directory: dir,
    profile: structuredClone(LOAD_FIXTURE_PROFILE), canonicalOrigin: LOCAL, stockCapacityEligible: false,
    principal, launcherPath: '/owned/start.sh', credentialsFile: join(dir, 'credentials.json'), sourceWitnessConfig: join(dir, 'source-config.json'),
    heartbeatAt: new Date().toISOString(), expiresAt: new Date(Date.now() + 60000).toISOString() };
  value.runtimeReceipt = { version: 1, run, scope: 'startup-effective-configuration', profile: value.profile, apiProcesses: 1,
    capacityAcceptance: false, instrumentation: value.profile.instrumentation, instrumentationOverheadMeasured: false, stockCapacityEligible: false,
    metadata: { normalMaximum: 16, readinessMaximum: 2 }, sourceOrdinaryMaximum: 20, sourcePerIdentityMaximum: 4,
    databaseSettings: { metadata: value.profile.database, source: value.profile.database },
    effectiveAllocations: Object.fromEntries(Object.entries(value.profile.allocations).map(([name, allocation]) => [name, { ...allocation, containerId: name, created: 'unit-created', image: 'unit-image' }])) };
  value.application = { id: 'schemii', created: 'unit-created', image: 'unit-image' }; value.source = { id: 'source', created: 'unit-created', image: 'unit-image' };
  value.osRuntimeReceiptFile = join(dir, 'os-runtime.json');
  await writeSnapshot(value.osRuntimeReceiptFile, { version: 1, origin: LOCAL,
    fixture: { version: 1, run, nonce, project: `schemii-pressure-${run.slice(9)}`, profile: value.profile, owner, sourceAssetsSha256: 'e'.repeat(64), stockCapacityEligible: false },
    containers: ['schemii', 'source', 'metadata', 'ingress'].map(service => ({ service, id: service, image: 'unit-image' })) });
  value.osRuntimeReceiptSha256 = (await readSnapshot(value.osRuntimeReceiptFile, 1024 * 1024, true)).sha256;
  const credentials = { version: 1, run, admin: principal, sourceAdminRole: 'pressure_admin',
    sourceAdminConnection: { host: '127.0.0.19', port: 6543, dbname: 'pressure_source', user: 'pressure_admin', sslmode: 'require', passwordFile: join(dir, 'source.json') },
    appSourceConnection: { host: 'source', port: 5432, database: 'pressure_source', username: 'pressure_source', passwordFile: join(dir, 'source.json') } };
  const witness = { version: 2, runId: 'load_' + run.slice(-16), authority: { database: 'pressure_source', role: 'pressure_source', endpoint: { host: '127.0.0.19', port: 6543, sslmode: 'require' } } };
  const path = join(dir, 'authority.json'), options = { readIdentity: async () => ({ birthTick: '456' }) };
  await writeSnapshot(credentials.sourceAdminConnection.passwordFile, 'fixture-source-password');
  await writeSnapshot(value.credentialsFile, credentials); await writeSnapshot(value.sourceWitnessConfig, witness); await writeSnapshot(path, value);
  assert.equal((await heldFixture(path, options)).profile.name, 'representative-load-v1');
  await writeSnapshot(path, { ...value, osRuntimeReceiptSha256: '0'.repeat(64) });
  await assert.rejects(heldFixture(path, options), /fixture_os_receipt_unqualified/); await writeSnapshot(path, value);
  for (const changed of [{ ...value, profile: { ...value.profile, name: 'pressure-v1' } },
    { ...value, profile: { ...value.profile, metadata: { ...value.profile.metadata, normalMaximum: 2 } } },
    { ...value, heartbeatAt: new Date(Date.now() + 60000).toISOString() }]) {
    await writeSnapshot(path, changed); await assert.rejects(heldFixture(path, options), /held_fixture_unqualified/);
  }
  await writeSnapshot(path, value); await assert.rejects(heldFixture(path, { readIdentity: async () => ({ birthTick: '999' }) }), /owner_changed/);
  for (const changed of [{ ...value, application: { ...value.application, id: 'replacement' } },
    { ...value, runtimeReceipt: { ...value.runtimeReceipt, sourceOrdinaryMaximum: 200 } }]) {
    await writeSnapshot(path, changed); await assert.rejects(heldFixture(path, options), /fixture_runtime_/);
  }
  await writeSnapshot(path, value);
  await writeSnapshot(value.credentialsFile, { ...credentials, sourceAdminConnection: { ...credentials.sourceAdminConnection, host: '192.0.2.99' } });
  await assert.rejects(heldFixture(path, options), /source_mapping_changed/);
  const consumer = { pid: 321, start: '654', uid: process.getuid(), argvSha256: 'c'.repeat(64), sourceFingerprint: 'd'.repeat(64) };
  const attachment = { version: 1, status: 'joined', run, nonce, consumer }, journal = { run, profile: value.profile, hold: { nonce, attachment: null }, cleanup: true, restored: true, stage: 'restored' };
  const binding = { authorityFile: path, attachmentFile: join(dir, 'attachment.json'), run, nonce, consumer };
  await writeSnapshot(join(dir, 'journal.json'), journal); await writeSnapshot(binding.attachmentFile, attachment);
  assert.equal((await disposedFixtureConsumer(binding, { pid: 321, birthTick: '654' })).fixtureDisposed, true);
  for (const changed of [{ ...attachment, status: 'detaching' }, { ...attachment, consumer: { ...consumer, start: 'different' } }]) {
    await writeSnapshot(binding.attachmentFile, changed); await assert.rejects(disposedFixtureConsumer(binding, { pid: 321, birthTick: '654' }), /disposal_not_proven/);
  }
});
test('fixture callbacks use only the owning launcher and exact attachment path', async () => {
  const authority = { directory: '/private/fixture', launcherPath: '/owned/start.sh' }, calls = [];
  const launch = (command, args, options) => {
    calls.push({ command, args, options }); const child = new EventEmitter(); child.stdout = new EventEmitter(); child.kill = () => {};
    queueMicrotask(() => { child.stdout.emit('data', JSON.stringify({ status: args[0].endsWith('detach') ? 'detaching' : 'attached', attachmentFile: '/private/fixture/attachment.json' })); child.emit('close', 0); }); return child;
  };
  const attached = await fixtureCommand(authority, 'attach', null, { launch });
  await fixtureCommand(authority, 'heartbeat', attached.attachmentFile, { launch }); await fixtureCommand(authority, 'detach', attached.attachmentFile, { launch });
  assert(calls.every(value => value.command === '/owned/start.sh' && !value.options.detached));
  assert.deepEqual(calls[0].args, ['--metadata-pressure-attach', '/private/fixture', String(process.pid)]);
  await assert.rejects(fixtureCommand(authority, 'detach', '/peer/attachment.json', { launch }), /fixture_control_invalid/);
  await assert.rejects(fixtureCommand(authority, 'rebuild', null, { launch }), /fixture_control_invalid/); assert.equal(calls.length, 3);
});

async function disposable(t, options = {}) {
  const dir = await mkdtemp(join(os.tmpdir(), 'schemii-owned-population-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const tag = 'load_0123456789abcdef', pool = 'user_schemii_connection_pool', adminName = 'pressure_' + 'f'.repeat(32), adminId = 'user_fixture_admin';
  const users = new Map([[adminName, { id: adminId, username: adminName, password: 'fixture-admin-password', display_name: 'fixture', is_admin: true, direct_access: { capabilities: [] } }],
    ['retained-peer', { id: 'untouched-peer', username: 'retained-peer', is_admin: false, display_name: 'Retained peer' }]]);
  const stores = new Map(), state = { serial: 0, source: false, failOnce: null, writes: [], revoked: [], drift: false };
  const store = name => { if (!stores.has(name)) stores.set(name, []); return stores.get(name); };
  const authority = { version: 1, kind: 'schemii-pressure-load-hold', status: 'held', profile: LOAD_FIXTURE_PROFILE, tag, path: join(dir, 'authority.json'), origin: LOCAL,
    admin: { username: adminName, userId: adminId, password: 'fixture-admin-password' }, sourceWitnessConfig: join(dir, 'source-monitor-config.json'),
    sourceAdminRole: 'fixture_source_admin', sourceAdminConnection: { password: 'source-admin-private' },
    appSourceConnection: { host: 'source', port: 5432, database: 'pressure_source', username: 'pressure_source', password: 'source-private', sslMode: 'require', connectTimeout: 3 } };
  const userPublic = user => { const value = { ...user }; delete value.password; return value; };
  class FakeClient {
    constructor({ cookie } = {}) { this.cookie = cookie || ''; this.username = cookie?.replace('cookie:', ''); }
    async login(credential, expected) {
      const user = users.get(credential.username); assert(user); assert.equal(user.password, credential.password); if (expected) assert.equal(user.id, expected);
      this.username = user.username; this.cookie = 'cookie:' + user.username;
      return { user: userPublic(user), is_admin: user.is_admin === true, capabilities: user.direct_access?.capabilities || [] };
    }
    async logout() { if (this.cookie) state.revoked.push(this.username); this.cookie = ''; }
    close() {}
    async request(request) {
      if (request.path === '/api/v1/auth/me') return users.has(this.username) ? { status: 200, data: await this.json('GET', request.path) } : { status: 401, data: {} };
      try { return { status: request.method === 'DELETE' ? 204 : 200, data: await this.json(request.method || 'GET', request.path, request.body) }; }
      catch (error) { if (error.message === 'fake_not_found') return { status: 404, data: {} }; throw error; }
    }
    async json(method, path, body) {
      if (body !== undefined) body = structuredClone(body); // Real HTTP serializes request values.
      const user = users.get(this.username), roleStore = store('/api/v1/admin/roles');
      const capabilities = [...new Set([...(user?.direct_access?.capabilities || []), ...roleStore.filter(role => role.user_ids.includes(user?.id)).flatMap(role => role.capabilities)])];
      if (path === '/api/v1/auth/me') return { user: userPublic(user), capabilities };
      if (path === '/api/v1/schemii/console/settings') return { revision: 1, rowPageSize: 100 };
      if (path.endsWith('/plan')) return { sql: 'fixture-plan' };
      if (path === '/api/v1/admin/accounts' && method === 'GET') return [...users.values()].map(userPublic);
      if (path === '/api/v1/schemii/workspaces/postgres') {
        const journal = await readSnapshot(join(dir, 'fixtures-private.json'));
        assert(journal.resources.some(item => item.kind === 'workspace' && item.phase === 'sent' && item.ownerId === user.id));
        const workspace = { id: `ws_${(++state.serial).toString(16).padStart(32, '0')}`, ownerId: user.id, name: body.namespace, revision: 1, ...body };
        store('/api/v1/schemii/workspaces').push(workspace); return { created: true, workspace };
      }
      const collections = ['/api/v1/admin/accounts', '/api/v1/admin/roles', '/api/v1/admin/schemii-connections', '/api/v1/connections', '/api/v1/schemii/workspaces', '/api/v1/schemoo/models', '/api/v1/schemer/dashboards'];
      const collection = collections.find(value => path === value || path.startsWith(value + '/'));
      assert(collection, `unexpected API path ${path}`);
      const isAdmin = collection.startsWith('/api/v1/admin/'), entries = collection === '/api/v1/admin/accounts' ? [...users.values()] : store(collection);
      const key = collection.endsWith('connections') ? 'connections' : collection.endsWith('workspaces') ? 'workspaces' : collection.endsWith('models') ? 'models' : 'dashboards';
      const visible = item => isAdmin || item.ownerId === user?.id || collection.endsWith('dashboards') && roleStore.some(role => role.user_ids.includes(user?.id) && role.dashboards.some(grant => grant.dashboard_id === item.id));
      if (method === 'GET' && path === collection) return isAdmin && !collection.endsWith('schemii-connections') ? entries.map(value => collection.endsWith('accounts') ? userPublic(value) : value) : { [key]: entries.filter(visible) };
      if (method === 'POST') {
        const journal = await readSnapshot(join(dir, 'fixtures-private.json'));
        assert(journal.resources.some(item => item.collection === collection && item.phase === 'sent' && item.label === (body.name || body.username)));
        state.writes.push({ method, collection });
        const prefix = collection.endsWith('accounts') ? 'user' : collection.endsWith('roles') ? 'role' : collection.endsWith('connections') ? 'pg' : collection.endsWith('models') ? 'model' : 'dashboard';
        const item = { ...body, id: `${prefix}_${(++state.serial).toString(16).padStart(32, '0')}`, revision: 1,
          ...(!['user', 'role'].includes(prefix) ? { ownerId: collection.endsWith('schemii-connections') ? pool : user.id } : {}) };
        if (collection.endsWith('accounts')) users.set(item.username, item); else entries.push(item);
        if (state.failOnce === collection) { state.failOnce = null; throw new ProtocolFailure('http_transport_failure'); }
        return collection.endsWith('accounts') ? userPublic(item) : (() => { const value = { ...item }; delete value.password; return value; })();
      }
      const id = path.slice(collection.length + 1).split('?')[0], item = entries.find(value => value.id === id && visible(value));
      if (!item) throw new Error('fake_not_found');
      if (method === 'GET') return state.drift ? { ...item, ownerId: 'foreign' } : item;
      if (method === 'DELETE') {
        assert(item.id !== adminId && item.id !== 'untouched-peer');
        if (collection.endsWith('accounts')) users.delete(item.username); else entries.splice(entries.indexOf(item), 1);
        return null;
      }
      throw new Error('unsupported fake operation');
    }
  }
  const provisionSource = async (action, configPath, receiptPath) => {
    const config = await readSnapshot(configPath);
    const result = { schemas: Array.from({ length: config.active }, (_, index) => ({ index: index + 1, name: `${tag}_u${String(index + 1).padStart(4, '0')}`,
      nonce: String(index + 1).padStart(32, '0'), phase: action === 'prepare' ? 'ready' : 'deleted' })) };
    state.source = action === 'prepare'; await writeSnapshot(receiptPath, result); return result;
  };
  const spec = plan({ fixtureMode: 'disposable', workload: options.workload || 'sql-page', active: options.active || 2, registered: options.registered || 10,
    profileMode: options.profileMode || 'private-single' });
  return { dir, spec, users, state, stores, authority, FakeClient,
    prepare: () => prepareOwnedFixtures({ dir, spec, authority, Client: FakeClient, provisionSource, readColumns: async () => ['Orders.id'] }),
    cleanup: receipt => cleanupOwnedFixtures({ dir, receipt, authority, Client: FakeClient, provisionSource }) };
}
test('disposable population separates registered and active identities, rotates private profiles and cleans exact resources', async t => {
  const f = await disposable(t, { profileMode: 'private-multiple' }), receipt = await f.prepare();
  assert.equal(receipt.population.registered, 10); assert.equal(receipt.accounts.length, 2);
  assert(receipt.accounts.every(account => account.variants.length === 2 && account.profiles.length === 2));
  assert(receipt.accounts.every(account => account.oracles.sql.rows === 513));
  const result = await f.cleanup(receipt);
  assert.equal(result.registeredAccountsDeleted, 10); assert.equal(result.objectsRemaining, 0);
  assert(f.users.has('retained-peer')); assert(f.users.has(f.authority.admin.username));
  assert.equal(f.state.source, false);
  const second = await f.cleanup(await readSnapshot(join(f.dir, 'fixtures-private.json')));
  assert.equal(second.objectsRemaining, 0); assert(f.users.has('retained-peer'));
});
test('pure viewers keep only Schemer access and receive an explicit shared grant; recovery is separate', async t => {
  const f = await disposable(t, { workload: 'shared-report', profileMode: 'managed-shared' }), receipt = await f.prepare();
  assert(receipt.accounts.every(account => !account.workspace && account.sharedDashboard));
  for (const account of receipt.accounts) assert.deepEqual(f.users.get(account.username).direct_access.capabilities, ['schemer:access']);
  assert.equal(receipt.recoveryAccount.instrumentationOnly, true);
  assert.notEqual(receipt.recoveryAccount.userId, receipt.accounts[0].userId);
  const roles = f.stores.get('/api/v1/admin/roles');
  assert(roles.some(role => role.name.endsWith('_viewers') && role.dashboards.length === 1 && role.connections[0].allow_authoring === false));
  await f.cleanup(receipt); assert(f.users.has(f.authority.admin.username));
});
test('cleanup preserves a role whose membership was extended to a peer after preparation', async t => {
  const f = await disposable(t, { workload: 'shared-report', profileMode: 'managed-shared' }), receipt = await f.prepare();
  const role = f.stores.get('/api/v1/admin/roles').find(value => value.name.endsWith('_viewers'));
  role.user_ids.push('untouched-peer');
  await assert.rejects(f.cleanup(receipt), /cleanup_role_changed/);
  assert(f.stores.get('/api/v1/admin/roles').some(value => value.id === role.id)); assert(f.users.has('retained-peer'));
});
test('1000 registered identities do not create1000 active profiles or alter retained users', async t => {
  const f = await disposable(t, { registered: 1000, active: 1 }), receipt = await f.prepare();
  assert.equal(receipt.population.registered, 1000); assert.equal(receipt.accounts.length, 1);
  assert.equal((await readSnapshot(join(f.dir, 'population-private.json'))).accounts.length, 1000);
  assert.equal(receipt.resources.filter(item => item.kind === 'private-profile').length, 1);
  assert.equal(f.users.get('retained-peer').id, 'untouched-peer');
});
test('unknown send is never replayed from absence, name collisions/ambiguous adoption preserve peers', async t => {
  const dir = await mkdtemp(join(os.tmpdir(), 'schemii-resource-intent-')); t.after(() => rm(dir, { recursive: true, force: true }));
  const receipt = { tag: 'load_0123456789abcdef', resources: [] }, file = join(dir, 'private.json'), rows = [];
  let sends = 0;
  const client = { json: async method => { if (method === 'GET') return { models: rows }; sends++; throw new ProtocolFailure('http_transport_failure'); } };
  const request = { receipt, save: () => writeSnapshot(file, receipt), client, key: 'model', kind: 'model', collection: '/api/v1/schemoo/models', listKey: 'models',
    body: { name: receipt.tag + '_nonce_model' }, ownerId: 'owner' };
  await assert.rejects(createOwnedResource(request));
  await assert.rejects(createOwnedResource(request), /uncertain_create/); assert.equal(sends, 1);
  rows.push({ name: request.body.name, id: 'candidate-one', ownerId: 'owner' }, { name: request.body.name, id: 'candidate-two', ownerId: 'owner' });
  await assert.rejects(createOwnedResource(request), /ambiguous/); assert.equal(rows.length, 2);
});

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

// Publicly embedded, self-signed fixture material; never used by the application.
const fixtureKey = `-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgQGNzhCo56l+imQYj
pbnaw8H59xr2TrYjjwfjDC4kv0KhRANCAAQMVFBMgXx7O+zwGtbgn8TEb8FliFkL
1Ss/wqjzJ69nH5HCrmId37bQEozYAjR0DHJaJnXcgLIEGaePelXy8/jc
-----END PRIVATE KEY-----`;
const fixtureCert = `-----BEGIN CERTIFICATE-----
MIIBfTCCASOgAwIBAgIUEP3pQB0lN4rzPppRCfpPRbOvlRUwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MDkzMDAxNDczM1oXDTQ2MDkyNTAx
NDczM1owFDESMBAGA1UEAwwJbG9jYWxob3N0MFkwEwYHKoZIzj0CAQYIKoZIzj0D
AQcDQgAEDFRQTIF8ezvs8BrW4J/ExG/BZYhZC9UrP8Ko8yevZx+Rwq5iHd+20BKM
2AI0dAxyWiZ13ICyBBmnj3pV8vP43KNTMFEwHQYDVR0OBBYEFLOl0lpKyKg82pPv
a+0MWlxwBlXZMB8GA1UdIwQYMBaAFLOl0lpKyKg82pPva+0MWlxwBlXZMA8GA1Ud
EwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDSAAwRQIhAOAexcfnig9YkS2RBvtlOWUz
ezmij/dW4Dlz7CxbKpEDAiBVvcLI6MRgWAzHyspoL7Ea4tOMMholFsgtlS/QPsgI
QA==
-----END CERTIFICATE-----`;
test('real HTTPS mutations carry the validated local or preview Origin and owner cookie', async t => {
  const received = [];
  const server = https.createServer({ key: fixtureKey, cert: fixtureCert }, async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk;
    received.push({ method: request.method, path: request.url, origin: request.headers.origin, cookie: request.headers.cookie });
    // This is a bounded transport fixture mirroring AuthenticationMiddleware's
    // origin boundary, not an application server or application acceptance.
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method) && request.headers.origin !== `https://${request.headers.host}`) {
      response.writeHead(403, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ detail: 'Same-origin request required' })); return;
    }
    const input = body ? JSON.parse(body) : null;
    if (request.url === '/api/v1/auth/login') {
      response.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': `fixture=${input.username}; Secure; HttpOnly` });
      response.end(JSON.stringify({ user: { id: input.username, username: input.username } })); return;
    }
    response.writeHead(request.method === 'DELETE' ? 204 : 200, { 'Content-Type': 'application/json' });
    response.end(request.method === 'DELETE' ? undefined : '{}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port, clients = [];
  t.after(async () => { for (const client of clients) client.close(); await new Promise(resolve => server.close(resolve)); });
  for (const [index, origin] of [LOCAL, PREVIEW].entries()) {
    const client = new HTTPClient({ origin }); clients.push(client);
    assert.equal(client.agent.options.rejectUnauthorized, origin !== LOCAL);
    // Redirect only this owned test agent's socket, retaining the real request
    // target/Host/headers. Production connection and TLS settings stay intact.
    client.agent.createConnection = options => tls.connect({ ...options, host: '127.0.0.1', port,
      servername: 'localhost', rejectUnauthorized: false });
    const username = `owner-${index}`;
    await client.login({ username, password: 'fixture-only' }, username);
    await client.json('POST', '/api/v1/schemoo/models', { name: 'fixture-only' });
    await client.json('PUT', '/api/v1/schemer/dashboards/fixture', { revision: 1 });
    await client.json('DELETE', '/api/v1/schemoo/models/fixture', undefined, 204);
    await client.logout();
    const calls = received.slice(index * 5, index * 5 + 5);
    assert.equal(calls.length, 5);
    assert.ok(calls.every(call => call.origin === origin));
    assert.equal(calls[0].cookie, undefined);
    assert.ok(calls.slice(1).every(call => call.cookie === `fixture=${username}`));
    assert.equal(client.cookie, '');
  }
  const before = received.length;
  assert.throws(() => new HTTPClient({ origin: 'https://foreign.invalid' }), { code: 'invalid_origin' });
  await assert.rejects(clients[0].json('POST', '//foreign.invalid/api/v1/auth/login', {}), { code: 'invalid_api_path' });
  assert.equal(received.length, before);
  // Prove the fixture itself rejects absent and foreign Origin. Removing the
  // client's production header makes the positive authenticated workflow fail.
  for (const origin of [undefined, 'https://foreign.invalid']) {
    const status = await new Promise((resolve, reject) => {
      const request = https.request({ hostname: '127.0.0.1', port, path: '/api/v1/auth/login', method: 'POST',
        rejectUnauthorized: false, headers: { Host: new URL(LOCAL).host, ...(origin ? { Origin: origin } : {}) } },
      response => { response.resume(); response.once('end', () => resolve(response.statusCode)); });
      request.once('error', reject); request.end('{}');
    });
    assert.equal(status, 403);
  }
});
