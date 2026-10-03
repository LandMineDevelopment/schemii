import assert from 'node:assert/strict';
import test from 'node:test';
import { chmod, copyFile, lstat, mkdir, mkdtemp, readFile, rename, rm, symlink, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import globalSetup from '../e2e/global-setup.js';
import { accountCredentials, accountStorageState, authenticateBrowserAccount } from '../e2e/helpers/account-auth.js';
import { cleanupParallelAccounts, PARALLEL_CAPABILITIES, prepareParallelAccounts, readParallelAccounts } from '../e2e/helpers/parallel-account.js';

function api({ enabled = true, lostResponse, failCatalog = false } = {}) {
  const users = new Map(), connections = new Map(), workspaces = new Map();
  const calls = [], contexts = [];
  let sequence = 0, lost = false;
  const nextId = prefix => `${prefix}_${(++sequence).toString(16).padStart(32, '0')}`;
  const response = (value, status = 200) => ({ status: () => status, ok: () => status < 400,
    json: async () => structuredClone(value), text: async () => 'Private target failure' });
  const identity = user => ({ user: { id: user.id, username: user.username, disabled: false },
    is_admin: user.is_admin, capabilities: user.capabilities });
  function mutate(kind, result) {
    if (lostResponse === kind && !lost) { lost = true; throw new Error(`Uncertain ${kind} transport failure`); }
    return result;
  }
  const requestFactory = { newContext: async options => {
    let current = options.storageState?.cookies?.[0]?.value || null;
    const context = { options, disposed: false,
      storageState: async () => ({ cookies: current ? [{ name: 'schemii_session', value: current, domain: 'localhost', path: '/', secure: true, httpOnly: true, expires: -1, sameSite: 'Lax' }] : [], origins: [] }),
      dispose: async () => { context.disposed = true; },
      get: async path => {
        calls.push({ method: 'GET', path, owner: current });
        if (path === '/api/v1/auth/status') return response({ enabled, setup_required: !users.size });
        if (path === '/api/v1/auth/me') return response(identity(users.get(current)));
        if (path === '/api/v1/admin/accounts') return response([...users.values()].map(user => ({ ...user,
          display_name: user.display_name, disabled: false, role_ids: [], effective_capabilities: user.capabilities,
          direct_access: { capabilities: user.capabilities.filter(value => value !== 'accounts:provision'), connections: [], dashboards: [] },
        })));
        if (path === '/api/v1/connections') return response({ connections: [...connections.values()].filter(value => value.ownerId === current) });
        if (path === '/api/v1/schemii/workspaces') return response({ workspaces: [...workspaces.values()].filter(value => value.owner === current).map(({ owner, ...value }) => value) });
        if (path === '/api/v1/schemoo/models') return response({ models: context.remainingModels || [] });
        if (path === '/api/v1/schemer/dashboards') return response({ dashboards: [] });
        if (path.startsWith('/api/v1/schemoo/catalog?')) return response({ tables: [] }, failCatalog && current !== 'user_local_prototype' ? 503 : 200);
        const collection = path.startsWith('/api/v1/connections/') ? connections : workspaces;
        const resource = collection.get(path.split('/').at(-1));
        return resource && (resource.ownerId || resource.owner) === current ? response(resource) : response({}, 404);
      },
      post: async (path, { data }) => {
        calls.push({ method: 'POST', path, owner: current, data });
        if (path === '/api/v1/auth/setup' || path === '/api/v1/auth/login') {
          let user;
          if (path.endsWith('setup')) {
            assert.equal(users.size, 0, 'only first account consumes setup');
            assert.equal(data.setup_token, 'owned-setup-token');
            user = { ...data, id: 'user_local_prototype', is_admin: true, capabilities: [...PARALLEL_CAPABILITIES] };
            users.set(user.id, user);
          } else user = [...users.values()].find(value => value.username === data.username && value.password === data.password);
          if (!user) return response({}, 401);
          current = user.id;
          return response(identity(user));
        }
        if (path === '/api/v1/admin/accounts') {
          assert.equal(users.get(current).is_admin, true);
          assert.deepEqual(data.direct_access.capabilities.sort(), PARALLEL_CAPABILITIES.filter(value => value !== 'accounts:provision'));
          const user = { ...data, id: nextId('user'), capabilities: [...data.direct_access.capabilities, 'accounts:provision'].sort() };
          users.set(user.id, user);
          return mutate('account', response(user, 201));
        }
        if (path === '/api/v1/connections') {
          const connection = { ...data, id: nextId('pg'), ownerId: current, ownership: 'user', revision: 1 };
          delete connection.password;
          connections.set(connection.id, connection);
          return current === 'user_local_prototype' ? response(connection, 201) : mutate('connection', response(connection, 201));
        }
        assert.equal(path, '/api/v1/schemii/workspaces/postgres');
        const workspace = { ...data, database: connections.get(data.connectionId).database, id: nextId('ws'), owner: current, revision: 1 };
        workspaces.set(workspace.id, workspace);
        return current === 'user_local_prototype' ? response({ workspace }, 201) : mutate('workspace', response({ workspace }, 201));
      },
      delete: async path => {
        calls.push({ method: 'DELETE', path, owner: current });
        if (path.startsWith('/api/v1/admin/accounts/')) { users.delete(path.split('/').at(-1)); return response({}, 204); }
        const [target, query] = path.split('?');
        const collection = target.startsWith('/api/v1/connections/') ? connections : workspaces;
        const resourceId = target.split('/').at(-1), resource = collection.get(resourceId);
        if (!resource) return response({}, 404);
        assert.equal(new URLSearchParams(query).get('expectedRevision'), String(resource.revision));
        if (collection === connections && [...workspaces.values()].some(value => value.connectionId === resourceId)) return response({}, 409);
        collection.delete(resourceId);
        return response({}, 204);
      },
    };
    contexts.push(context);
    return context;
  } };
  return { requestFactory, users, connections, workspaces, calls, contexts };
}

async function fixture(t, options) {
  const parent = await mkdtemp(join(tmpdir(), 'schemii-parallel-account-'));
  t.after(async () => { await rm(parent, { recursive: true, force: true }); });
  await writeFile(join(parent, 'account_setup_token'), 'owned-setup-token', { mode: 0o600 });
  const server = api(options);
  const environment = { SCHEMII_E2E_BOOTSTRAP: '1', SCHEMII_SECRET_DIRECTORY: parent, SCHEMII_TEST_POSTGRES_PASSWORD: 'private-target-password' };
  const directory = join(parent, 'owned');
  return { ...server, directory, parent, environment, prepare: () => prepareParallelAccounts({ directory, environment, requestFactory: server.requestFactory }) };
}

function selected(manifest, index) {
  return { SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE: join(manifest.accounts[0].root, '..', 'accounts.json'), SCHEMII_E2E_PROCESS_INDEX: String(index), SCHEMII_E2E_SOURCE_ROOT: manifest.sourceRoot };
}

test('serial prepare creates two full-rights owners and all fixtures before ready publication; exact cleanup preserves primary', async t => {
  const f = await fixture(t);
  const createContext = f.requestFactory.newContext;
  f.requestFactory.newContext = async options => {
    const context = await createContext(options), post = context.post;
    context.post = async (path, options) => {
      const ledger = JSON.parse(await readFile(join(f.directory, 'ownership.json')));
      const kind = path === '/api/v1/admin/accounts' ? 'account' : path === '/api/v1/connections' ? 'connection' : path === '/api/v1/schemii/workspaces/postgres' ? 'workspace' : null;
      if (kind && (kind === 'account' || ledger.accounts[1].accountId)) {
        assert.equal(ledger.pending.kind, kind, 'intent is durable before external mutation');
        await assert.rejects(readFile(join(f.directory, 'accounts.json')), { code: 'ENOENT' });
      }
      return post(path, options);
    };
    return context;
  };
  const { manifestPath } = await f.prepare();
  const manifest = await readParallelAccounts(manifestPath);
  assert.equal(manifest.physicalTargets, 'shared');
  assert.equal(f.calls.filter(call => call.path === '/api/v1/auth/setup').length, 1);
  assert.equal(f.calls.filter(call => call.path === '/api/v1/admin/accounts' && call.method === 'POST').length, 1);
  assert.equal(f.connections.size, 6); assert.equal(f.workspaces.size, 4);
  for (const account of manifest.accounts) {
    assert.equal((await lstat(account.root)).mode & 0o077, 0);
    assert.equal((await lstat(account.storageState)).mode & 0o077, 0);
    assert.equal(f.connections.get(account.connections[0]).database, 'schemii_test');
    assert.equal(f.workspaces.get(account.workspaces[0]).namespace, 'bookstore');
    assert.deepEqual(await accountCredentials(selected(manifest, account.index)), { username: account.username, password: account.password });
  }
  const beforeWrites = f.calls.filter(call => call.method === 'POST').length;
  const environment = selected(manifest, 2);
  await globalSetup({ projects: [{ use: { baseURL: manifest.baseURL, storageState: accountStorageState(environment) } }] }, { environment, requestFactory: f.requestFactory });
  assert.equal(f.calls.filter(call => call.method === 'POST').length, beforeWrites + 1, 'child setup only logs in');
  await readParallelAccounts(manifestPath); // Atomic state refresh never makes the sibling manifest unusable.
  await cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory });
  assert.equal(f.users.size, 1); assert.ok(f.users.has(manifest.accounts[0].accountId));
  assert.equal(f.connections.size, 3); assert.equal(f.workspaces.size, 2);
  assert.equal(JSON.parse(await readFile(join(f.directory, 'ownership.json'))).stage, 'cleaned');
  await assert.rejects(readFile(manifestPath), { code: 'ENOENT' });
  for (const account of manifest.accounts) await assert.rejects(lstat(account.root), { code: 'ENOENT' });
  assert.ok(f.contexts.every(context => context.disposed));
  assert.ok(f.contexts.every(context => context.options.extraHTTPHeaders.Origin === 'https://localhost:8001'));
});

test('automatic cleanup removes both execution trees while exported evidence and live peer/cache link targets survive', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare(), manifest = await readParallelAccounts(manifestPath);
  const peer = join(f.parent, 'active-peer'), cache = join(f.parent, 'dependency-cache');
  await mkdir(peer); await mkdir(cache);
  await writeFile(join(peer, 'live-session'), 'peer is still active');
  await writeFile(join(cache, 'immutable-package'), 'cached package bytes');
  for (const account of manifest.accounts) {
    const execution = join(account.root, 'parallel');
    await mkdir(join(execution, 'artifacts', 'traces'), { recursive: true });
    await writeFile(join(execution, 'package.json'), '{"type":"module"}');
    await writeFile(join(execution, 'playwright.config.js'), 'export default {};');
    await writeFile(join(execution, 'artifacts', 'traces', 'owned.trace'), 'owned diagnostic bytes');
    await writeFile(join(execution, 'telemetry.jsonl'), '{"owned":true}\n');
    await symlink(peer, join(execution, 'scripts'));
    await symlink(cache, join(execution, 'node_modules'));
  }
  const selectedEvidence = join(f.parent, 'exported-owned.trace');
  await copyFile(join(manifest.accounts[1].root, 'parallel', 'artifacts', 'traces', 'owned.trace'), selectedEvidence);
  await cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory });
  for (const account of manifest.accounts) await assert.rejects(lstat(account.root), { code: 'ENOENT' });
  assert.equal(await readFile(join(peer, 'live-session'), 'utf8'), 'peer is still active');
  assert.equal(await readFile(join(cache, 'immutable-package'), 'utf8'), 'cached package bytes');
  assert.equal(await readFile(selectedEvidence, 'utf8'), 'owned diagnostic bytes');
  assert.equal(JSON.parse(await readFile(join(f.directory, 'ownership.json'))).stage, 'cleaned');
});

test('a process root replaced by a peer symlink is refused before application cleanup and leaves peer files intact', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare(), manifest = await readParallelAccounts(manifestPath);
  const peer = join(f.parent, 'active-peer'); await mkdir(peer);
  await writeFile(join(peer, 'auth.json'), 'peer private state');
  await rename(manifest.accounts[1].root, join(f.parent, 'preserved-process-2'));
  await symlink(peer, manifest.accounts[1].root);
  const before = f.calls.length;
  await assert.rejects(cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory }), /symlinks/);
  assert.equal(f.calls.length, before, 'root ownership preflight precedes all application mutations');
  assert.equal(f.users.size, 2);
  assert.equal(await readFile(join(peer, 'auth.json'), 'utf8'), 'peer private state');
});

test('original no-selector setup keeps anonymous state and explicit admin login behavior', async t => {
  const f = await fixture(t, { enabled: false });
  const state = join(f.parent, 'serial', 'admin.json');
  assert.equal(accountStorageState({}), './artifacts/playwright-auth/admin.json');
  await globalSetup({ projects: [{ use: { baseURL: 'https://localhost:8001', storageState: state } }] }, { environment: {}, requestFactory: f.requestFactory });
  assert.deepEqual(JSON.parse(await readFile(state)), { cookies: [], origins: [] });
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
  const context = await api().requestFactory.newContext({});
  await assert.rejects(authenticateBrowserAccount(context, {}), /explicit SCHEMII_E2E_BOOTSTRAP/);
  await context.dispose();
  const existing = api();
  existing.users.set('user_local_prototype', { id: 'user_local_prototype', username: 'existing', password: 'explicit-password', is_admin: true, capabilities: ['accounts:provision'] });
  await globalSetup({ projects: [{ use: { baseURL: 'https://localhost:8001', storageState: state } }] }, {
    environment: { SCHEMII_E2E_USERNAME: 'existing', SCHEMII_E2E_PASSWORD: 'explicit-password' }, requestFactory: existing.requestFactory,
  });
  assert.equal(existing.connections.size, 0, 'ordinary existing account login creates no fixtures without bootstrap consent');
  assert.equal(existing.calls.filter(call => call.method === 'POST').length, 1);
});

test('prepare requires consent, enabled auth and a fresh owned root without changing existing state', async t => {
  const f = await fixture(t, { enabled: false });
  await assert.rejects(prepareParallelAccounts({ directory: f.directory, requestFactory: f.requestFactory, environment: {} }), /explicit/);
  assert.equal(f.calls.length, 0);
  await assert.rejects(f.prepare(), /preparation failed/);
  assert.equal(f.users.size, 0);
  assert.equal(JSON.parse(await readFile(join(f.directory, 'ownership.json'))).stage, 'preparing');
  await assert.rejects(f.prepare(), { code: 'EEXIST' });
});

test('closed manifest rejects wrong source, extra fields, shared identities, unsafe files and missing state', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare();
  const original = JSON.parse(await readFile(manifestPath));
  for (const change of [value => { value.unapproved = true; }, value => { value.sourceRoot = f.parent; },
    value => { value.accounts[1].accountId = value.accounts[0].accountId; }, value => { value.accounts[1].connections[0] = value.accounts[0].connections[0]; },
    value => { value.accounts[1].root = value.accounts[0].root; }]) {
    const value = structuredClone(original); change(value);
    await writeFile(manifestPath, JSON.stringify(value));
    await assert.rejects(readParallelAccounts(manifestPath), /Invalid|distinct/);
  }
  await writeFile(manifestPath, JSON.stringify(original));
  await chmod(manifestPath, 0o644); await assert.rejects(readParallelAccounts(manifestPath), /private/); await chmod(manifestPath, 0o600);
  const saved = join(f.directory, 'saved.json'); await writeFile(saved, JSON.stringify(original), { mode: 0o600 });
  await unlink(manifestPath); await symlink(saved, manifestPath); await assert.rejects(readParallelAccounts(manifestPath), /symlinks/);
  await unlink(manifestPath); await writeFile(manifestPath, JSON.stringify(original), { mode: 0o600 });
  await unlink(original.accounts[1].storageState); await assert.rejects(readParallelAccounts(manifestPath), { code: 'ENOENT' });
  assert.throws(() => accountStorageState({ SCHEMII_E2E_PROCESS_INDEX: '2' }), /absolute/);
  assert.throws(() => accountStorageState({ SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE: manifestPath, SCHEMII_E2E_PROCESS_INDEX: '3' }), /index/);
});

test('two state paths containing the same session are rejected', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare(), manifest = await readParallelAccounts(manifestPath);
  await writeFile(manifest.accounts[1].storageState, await readFile(manifest.accounts[0].storageState));
  await assert.rejects(readParallelAccounts(manifestPath), /distinct authenticated/);
});

for (const kind of ['account', 'connection', 'workspace']) test(`uncertain ${kind} creation is read back by exact intent and cleaned without replay or hiding failure`, async t => {
  const f = await fixture(t, { lostResponse: kind });
  await assert.rejects(f.prepare(), new RegExp(`Uncertain ${kind} transport failure`));
  const ledger = JSON.parse(await readFile(join(f.directory, 'ownership.json')));
  assert.equal(ledger.stage, 'cleaned'); assert.equal(ledger.pending, null);
  assert.equal(f.users.size, 1); assert.equal(f.connections.size, 3); assert.equal(f.workspaces.size, 2);
  assert.equal(f.calls.filter(call => call.method === 'POST' && call.path === '/api/v1/admin/accounts').length, 1);
  assert.ok(f.contexts.every(context => context.disposed));
});

test('failed fixture setup cleans only recorded second-account resources and retains original failure', async t => {
  const f = await fixture(t, { failCatalog: true });
  await assert.rejects(f.prepare(), /verify schemii_test catalog.*503/);
  assert.equal(f.users.size, 1); assert.equal(f.connections.size, 3); assert.equal(f.workspaces.size, 2);
});

test('rights and ownership drift reject child setup and preserve cleanup ledger/account', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare(), manifest = await readParallelAccounts(manifestPath);
  const environment = selected(manifest, 2);
  f.users.get(manifest.accounts[1].accountId).capabilities = ['accounts:provision'];
  await assert.rejects(globalSetup({ projects: [{ use: { baseURL: manifest.baseURL, storageState: accountStorageState(environment) } }] }, { environment, requestFactory: f.requestFactory }), /rights/);
  await assert.rejects(cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory }), /rights/);
  assert.equal(f.users.size, 2); assert.equal(f.connections.size, 6);
  f.users.get(manifest.accounts[1].accountId).capabilities = [...PARALLEL_CAPABILITIES];
  f.connections.get(manifest.accounts[1].connections[0]).host = 'unknown-shared-host';
  await assert.rejects(cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory }), /ownership drift/);
  assert.equal(f.users.size, 2);
  assert.equal(JSON.parse(await readFile(join(f.directory, 'ownership.json'))).stage, 'cleaning');
});

test('unknown owned leftovers block account removal; cleanup never sweeps them or primary resources', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare(), manifest = await readParallelAccounts(manifestPath);
  const create = f.requestFactory.newContext;
  f.requestFactory.newContext = async options => { const context = await create(options); context.remainingModels = [{ id: 'unrecorded-model' }]; return context; };
  await assert.rejects(cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory }), /Unrecorded/);
  assert.equal(f.users.size, 2); assert.equal(f.connections.size, 3); assert.equal(f.workspaces.size, 2);
  assert.ok(f.connections.has(manifest.accounts[0].connections[0]));
  assert.equal(f.calls.filter(call => call.method === 'DELETE' && call.path.includes('unrecorded-model')).length, 0);
});

test('uncertain account deletion retains failure then resumes from recorded deletion intent without another deletion', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare();
  const create = f.requestFactory.newContext;
  let lost = false;
  f.requestFactory.newContext = async options => {
    const context = await create(options), remove = context.delete;
    context.delete = async path => {
      const response = await remove(path);
      if (path.startsWith('/api/v1/admin/accounts/') && !lost) { lost = true; throw new Error('Uncertain account deletion transport failure'); }
      return response;
    };
    return context;
  };
  await assert.rejects(cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory }), /Uncertain account deletion/);
  assert.equal(JSON.parse(await readFile(join(f.directory, 'ownership.json'))).stage, 'deleting-account');
  assert.equal(f.users.size, 1);
  await cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory });
  assert.equal(JSON.parse(await readFile(join(f.directory, 'ownership.json'))).stage, 'cleaned');
  assert.equal(f.calls.filter(call => call.method === 'DELETE' && call.path.startsWith('/api/v1/admin/accounts/')).length, 1);
});

for (const kind of ['workspace', 'connection']) test(`uncertain ${kind} deletion resumes by recorded ID and never deletes the resource twice`, async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare();
  const create = f.requestFactory.newContext;
  let lostPath;
  f.requestFactory.newContext = async options => {
    const context = await create(options), remove = context.delete;
    context.delete = async path => {
      const response = await remove(path);
      if (!lostPath && path.startsWith(kind === 'workspace' ? '/api/v1/schemii/workspaces/' : '/api/v1/connections/')) {
        lostPath = path; throw new Error(`Uncertain ${kind} deletion transport failure`);
      }
      return response;
    };
    return context;
  };
  await assert.rejects(cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory }), /Uncertain/);
  assert.equal(f.users.size, 2);
  await cleanupParallelAccounts({ manifestPath, requestFactory: f.requestFactory });
  assert.equal(f.users.size, 1); assert.equal(f.connections.size, 3); assert.equal(f.workspaces.size, 2);
  assert.equal(f.calls.filter(call => call.method === 'DELETE' && call.path === lostPath).length, 1);
});

test('existing primary workspace order is preserved and unsafe bookstore selection blocks ready publication', async t => {
  const f = await fixture(t);
  const originalContext = f.requestFactory.newContext;
  f.requestFactory.newContext = async options => {
    const context = await originalContext(options), get = context.get;
    context.get = async path => {
      const response = await get(path);
      if (path === '/api/v1/schemii/workspaces' && f.workspaces.size >= 2) {
        const body = await response.json();
        body.workspaces.reverse();
        return { ...response, json: async () => body };
      }
      return response;
    };
    return context;
  };
  await assert.rejects(f.prepare(), /bookstore fixture must be the first/);
  assert.equal(f.users.size, 1); assert.equal(f.workspaces.size, 2);
  await assert.rejects(readFile(join(f.directory, 'accounts.json')), { code: 'ENOENT' });
});

test('actual CLI retains sanitized success/failure receipts outside private roots with mock transport only', async t => {
  const f = await fixture(t);
  const mock = join(f.parent, 'mock-playwright.mjs'), loader = join(f.parent, 'mock-loader.mjs');
  await writeFile(mock, `import assert from 'node:assert/strict';\nimport {readFileSync} from 'node:fs';\nconst PARALLEL_CAPABILITIES=${JSON.stringify(PARALLEL_CAPABILITIES)};\n${api.toString()}\nconst server=api();\nif(process.env.MOCK_CLEANUP_LEDGER){const ledger=JSON.parse(readFileSync(process.env.MOCK_CLEANUP_LEDGER));for(const account of ledger.accounts){server.users.set(account.accountId,{id:account.accountId,username:account.username,password:account.password,is_admin:true,display_name:account.index===2?'Browser process '+account.username:'Browser test administrator',capabilities:[...PARALLEL_CAPABILITIES]});account.connections.forEach((id,index)=>server.connections.set(id,{id,ownerId:account.accountId,ownership:'user',revision:1,name:'Browser fixture: '+['schemii_test','schemii_migration_demo','organization'][index],database:['schemii_test','schemii_migration_demo','organization'][index],host:'postgres'}));account.workspaces.forEach((id,index)=>server.workspaces.set(id,{id,owner:account.accountId,connectionId:account.connections[index],database:['schemii_test','schemii_migration_demo'][index],namespace:index===0?'bookstore':'public',revision:1}));}}\nexport const request=server.requestFactory;\n`, { mode: 0o600 });
  await writeFile(loader, `export async function resolve(specifier,context,nextResolve){if(specifier==='@playwright/test')return {url:${JSON.stringify(pathToFileURL(mock).href)},shortCircuit:true};return nextResolve(specifier,context);}`, { mode: 0o600 });
  const command = fileURLToPath(new URL('../e2e/helpers/parallel-account.js', import.meta.url));
  const env = { ...process.env, ...f.environment };
  for (const key of ['SCHEMII_E2E_USERNAME', 'SCHEMII_E2E_PASSWORD', 'SCHEMII_E2E_CREDENTIALS_FILE', 'SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE', 'SCHEMII_E2E_PROCESS_INDEX']) delete env[key];
  const invoke = (operation, directory, receipt, overrides = {}) => spawnSync(process.execPath, ['--experimental-loader', loader, command, operation, directory, `--receipt=${receipt}`], { env: { ...env, ...overrides }, encoding: 'utf8' });
  const preparedReceipt = join(f.parent, 'prepare-receipt.json');
  const prepared = invoke('prepare', f.directory, preparedReceipt);
  assert.equal(prepared.status, 0, prepared.stderr);
  assert.deepEqual(JSON.parse(await readFile(preparedReceipt)), { schema: 1, operation: 'prepare', outcome: 'passed', physicalTargets: 'shared', cleanup: 'pending' });
  const cleanedReceipt = join(f.parent, 'cleanup-receipt.json');
  const cleaned = invoke('cleanup', f.directory, cleanedReceipt, { MOCK_CLEANUP_LEDGER: join(f.directory, 'ownership.json') });
  assert.equal(cleaned.status, 0, cleaned.stderr);
  assert.deepEqual(JSON.parse(await readFile(cleanedReceipt)), { schema: 1, operation: 'cleanup', outcome: 'passed', primaryPreserved: true, cleanup: 'completed' });
  const duplicate = invoke('prepare', join(f.parent, 'must-not-mutate'), preparedReceipt);
  assert.equal(duplicate.status, 1);
  assert.equal(JSON.parse(await readFile(preparedReceipt)).outcome, 'passed', 'original receipt is never overwritten');
  await assert.rejects(lstat(join(f.parent, 'must-not-mutate')), { code: 'ENOENT' });
  const failedReceipt = join(f.parent, 'failure-receipt.json');
  const failed = invoke('prepare', join(f.parent, 'failed'), failedReceipt, { SCHEMII_E2E_BOOTSTRAP: '0' });
  assert.equal(failed.status, 1);
  assert.equal(JSON.parse(await readFile(failedReceipt)).outcome, 'failed');
  for (const result of [prepared, cleaned, failed]) {
    assert.doesNotMatch(result.stdout + result.stderr, /private-target-password|owned-setup-token|password|schemii_session/);
  }
});

test('child rejects a mismatched config/source root and never consumes setup even when bootstrap is set', async t => {
  const f = await fixture(t), { manifestPath } = await f.prepare(), manifest = await readParallelAccounts(manifestPath);
  const environment = { ...selected(manifest, 2), SCHEMII_E2E_BOOTSTRAP: '1', SCHEMII_SECRET_DIRECTORY: '/unavailable-token' };
  const config = { projects: [{ use: { baseURL: manifest.baseURL, storageState: accountStorageState(environment) } }] };
  await assert.rejects(globalSetup({ projects: [{ use: { baseURL: manifest.baseURL, storageState: 'artifacts/shared.json' } }] }, { environment, requestFactory: f.requestFactory }), /process-owned/);
  await assert.rejects(globalSetup(config, { environment: { ...environment, SCHEMII_E2E_SOURCE_ROOT: f.parent }, requestFactory: f.requestFactory }), /source root/);
  await assert.rejects(accountCredentials({ ...environment, SCHEMII_E2E_USERNAME: 'other' }), /another credential selector/);
  await globalSetup(config, { environment, requestFactory: f.requestFactory });
  assert.equal(f.calls.filter(call => call.path === '/api/v1/auth/setup').length, 1);
});
