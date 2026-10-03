import { constants } from 'node:fs';
import { lstat, mkdir, open, readFile, rename, rm, unlink, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { accountCredentials, authenticateBrowserAccount } from './account-auth.js';
import { ensureDatabaseFixtures } from './database-fixtures.js';

export const PARALLEL_CAPABILITIES = Object.freeze(['accounts:provision', 'schemer:access', 'schemer:author', 'schemii:access', 'schemoo:access']);
const CONNECTIONS = '/api/v1/connections';
const WORKSPACES = '/api/v1/schemii/workspaces';
const databases = ['schemii_test', 'schemii_migration_demo', 'organization'];
const SOURCE_ROOT = fileURLToPath(new URL('../../../', import.meta.url)).replace(/\/$/, '');
const id = (value, prefix) => typeof value === 'string' && new RegExp(`^${prefix}_[0-9a-f]{32}$`).test(value);
const keys = (value, expected) => value && typeof value === 'object' && !Array.isArray(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort());

async function privatePath(path, directory = false) {
  if (!isAbsolute(path) || resolve(path) !== path) throw new Error('Parallel browser paths must be absolute and normalized.');
  let current = sep;
  for (const component of path.split(sep).filter(Boolean)) {
    current = join(current, component);
    if ((await lstat(current)).isSymbolicLink()) throw new Error('Parallel browser paths cannot contain symlinks.');
  }
  const info = await lstat(path);
  if (!(directory ? info.isDirectory() : info.isFile()) || (info.mode & 0o077) || info.uid !== process.getuid()) {
    throw new Error('Parallel browser files and roots must be private and owned by the current user.');
  }
}

async function readPrivate(path) {
  await privatePath(path);
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size > 32 * 1024 || (info.mode & 0o077) || info.uid !== process.getuid()) throw new Error('Invalid private parallel browser document.');
    const buffer = Buffer.alloc(32 * 1024 + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead > 32 * 1024) throw new Error('Invalid private parallel browser document size.');
    try { return JSON.parse(buffer.subarray(0, bytesRead).toString('utf8')); }
    catch { throw new Error('Invalid private parallel browser JSON document.'); }
  } finally { await handle.close(); }
}

async function save(path, value) {
  const temporary = `${path}.${randomBytes(8).toString('hex')}.tmp`;
  try {
    await writeFile(temporary, JSON.stringify(value), { flag: 'wx', mode: 0o600 });
    await rename(temporary, path);
  } finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}

function validateDocument(value, directory, ready) {
  const expected = ['schema', 'baseURL', 'sourceRoot', 'physicalTargets', 'accounts', ...(ready ? [] : ['stage', 'pending'])];
  if (!keys(value, expected) || value.schema !== 1 || value.baseURL !== 'https://localhost:8001' || value.sourceRoot !== SOURCE_ROOT || value.physicalTargets !== 'shared' ||
      !Array.isArray(value.accounts) || value.accounts.length !== 2) throw new Error('Invalid parallel browser accounts document.');
  if (!ready && (!['preparing', 'ready', 'cleaning', 'deleting-account', 'cleaned'].includes(value.stage) ||
      (value.pending !== null && (!keys(value.pending, ['kind', 'identity']) || !['account', 'connection', 'workspace'].includes(value.pending.kind))))) {
    throw new Error('Invalid parallel browser ownership ledger.');
  }
  for (const [position, account] of value.accounts.entries()) {
    if (!keys(account, ['index', 'accountId', 'username', 'password', 'root', 'storageState', 'connections', 'workspaces']) ||
        account.index !== position + 1 || account.root !== join(directory, `process-${position + 1}`) || account.storageState !== join(account.root, 'auth.json') ||
        typeof account.username !== 'string' || !/^[a-zA-Z0-9_.@-]{3,64}$/.test(account.username) ||
        typeof account.password !== 'string' || account.password.length < 12 || account.password.length > 256 ||
        !(id(account.accountId, 'user') || account.accountId === 'user_local_prototype' || (!ready && account.accountId === null)) ||
        !Array.isArray(account.connections) || account.connections.length > 3 || account.connections.some(value => !id(value, 'pg')) ||
        !Array.isArray(account.workspaces) || account.workspaces.length > 2 || account.workspaces.some(value => !id(value, 'ws')) ||
        (ready && (account.connections.length !== 3 || account.workspaces.length !== 2))) throw new Error('Invalid parallel browser account slot.');
  }
  const [primary, secondary] = value.accounts;
  if (!/^e2e_parallel_[0-9a-f]{32}$/.test(secondary.username) || secondary.accountId === 'user_local_prototype' ||
      primary.username.toLowerCase() === secondary.username.toLowerCase() || (primary.accountId && primary.accountId === secondary.accountId) ||
      new Set(value.accounts.flatMap(account => [...account.connections, ...account.workspaces])).size !==
        value.accounts.reduce((total, account) => total + account.connections.length + account.workspaces.length, 0)) throw new Error('Parallel browser account ownership must be distinct.');
  return value;
}

/** Ready-only input: no provisioning, credential fallback or shared storage state. */
export async function readParallelAccounts(path) {
  if (!isAbsolute(path) || path !== join(dirname(path), 'accounts.json')) throw new Error('Use the absolute prepared accounts.json path.');
  const document = validateDocument(await readPrivate(path), dirname(path), true);
  await privatePath(dirname(path), true);
  const sessions = new Set();
  for (const account of document.accounts) {
    await privatePath(account.root, true);
    const state = await readPrivate(account.storageState);
    if (!keys(state, ['cookies', 'origins']) || !Array.isArray(state.cookies) || state.cookies.length !== 1 || !Array.isArray(state.origins) || state.origins.length) {
      throw new Error('Prepared parallel browser authentication state is missing.');
    }
    const cookie = state.cookies[0];
    if (cookie.name !== 'schemii_session' || typeof cookie.value !== 'string' || !cookie.value || cookie.domain !== 'localhost' ||
        cookie.path !== '/' || cookie.secure !== true || cookie.httpOnly !== true || cookie.sameSite !== 'Lax' || sessions.has(cookie.value)) {
      throw new Error('Parallel browser sessions must be distinct authenticated private contexts.');
    }
    sessions.add(cookie.value);
  }
  return document;
}

export async function writeParallelStorageState(account, state) {
  await privatePath(account.root, true);
  await save(account.storageState, state);
}

async function json(response, operation, expected = 200) {
  if (response.status() !== expected) throw new Error(`Parallel browser ${operation} failed (HTTP ${response.status()}).`);
  return expected === 204 ? null : response.json();
}

function verifyIdentity(account, identity) {
  if (!identity?.is_admin || identity.user?.id !== account.accountId || identity.user?.username !== account.username ||
      identity.user?.disabled || JSON.stringify([...(identity.capabilities || [])].sort()) !== JSON.stringify(PARALLEL_CAPABILITIES)) {
    throw new Error('Parallel browser account identity or product/author rights differ from its ownership record.');
  }
}

async function fixtures(context, account) {
  const connections = (await json(await context.get(CONNECTIONS), 'list owned connections')).connections;
  const workspaces = (await json(await context.get(WORKSPACES), 'list owned workspaces')).workspaces;
  if (workspaces.find(value => value.connectionId && value.database)?.id !== account.workspaces[0]) {
    throw new Error('Parallel browser bookstore fixture must be the first database-backed workspace. Existing work is preserved.');
  }
  for (const [index, database] of databases.entries()) {
    const connection = connections.find(value => value.id === account.connections[index]);
    if (!connection || connection.ownerId !== account.accountId || connection.ownership !== 'user' || connection.host !== 'postgres' ||
        connection.database !== database || connection.name !== `Browser fixture: ${database}`) throw new Error('Parallel browser fixture connection ownership or target drifted.');
    if (index < 2) {
      const workspace = workspaces.find(value => value.id === account.workspaces[index]);
      if (!workspace || workspace.connectionId !== connection.id || workspace.database !== database || workspace.namespace !== (index === 0 ? 'bookstore' : 'public')) {
        throw new Error('Parallel browser fixture workspace ownership or target drifted.');
      }
    }
  }
}

export async function verifyParallelAccount(context, account, identity) {
  verifyIdentity(account, identity);
  verifyIdentity(account, await json(await context.get('/api/v1/auth/me'), 'verify current identity'));
  await fixtures(context, account);
}

function contextOptions(baseURL, storageState) {
  return { baseURL, ignoreHTTPSErrors: true, extraHTTPHeaders: { Origin: new URL(baseURL).origin }, ...(storageState ? { storageState } : {}) };
}

/** Explicit serial preparation. Private application metadata does not isolate physical targets. */
export async function prepareParallelAccounts({ directory, baseURL = 'https://localhost:8001', requestFactory, environment = process.env }) {
  if (environment.SCHEMII_E2E_BOOTSTRAP !== '1' || environment.SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE || environment.SCHEMII_E2E_PROCESS_INDEX) {
    throw new Error('Parallel account preparation requires explicit SCHEMII_E2E_BOOTSTRAP=1 and no active parallel account selector.');
  }
  if (!isAbsolute(directory) || resolve(directory) !== directory || baseURL !== 'https://localhost:8001') throw new Error('Parallel preparation requires a fresh absolute directory and the canonical application origin.');
  // Exclusive creation prevents adopting another run's credentials or resources.
  await mkdir(directory, { mode: 0o700 });
  await privatePath(directory, true);
  const primaryCredentials = await accountCredentials(environment) || { username: `e2e_admin_${randomBytes(6).toString('hex')}`, password: randomBytes(32).toString('base64url') };
  primaryCredentials.username = primaryCredentials.username.toLowerCase();
  const ledger = { schema: 1, baseURL, sourceRoot: SOURCE_ROOT, physicalTargets: 'shared', accounts: [primaryCredentials,
    { username: `e2e_parallel_${randomBytes(16).toString('hex')}`, password: randomBytes(32).toString('base64url') }].map((credentials, index) => ({
    index: index + 1, accountId: null, ...credentials, root: join(directory, `process-${index + 1}`),
    storageState: join(directory, `process-${index + 1}`, 'auth.json'), connections: [], workspaces: [],
  })), stage: 'preparing', pending: null };
  const ledgerPath = join(directory, 'ownership.json');
  await save(ledgerPath, ledger);
  for (const account of ledger.accounts) await mkdir(account.root, { mode: 0o700 });
  const primary = await requestFactory.newContext(contextOptions(baseURL));
  let secondary;
  try {
    const status = await json(await primary.get('/api/v1/auth/status'), 'check enabled authentication');
    if (!status.enabled) throw new Error('Parallel browser accounts require enabled authentication.');
    if (!status.setup_required && !await accountCredentials(environment)) throw new Error('An existing installation requires explicitly supplied primary credentials.');
    const authEnvironment = { ...environment, SCHEMII_E2E_CREDENTIALS_FILE: undefined,
      SCHEMII_E2E_USERNAME: primaryCredentials.username, SCHEMII_E2E_PASSWORD: primaryCredentials.password };
    const { identity } = await authenticateBrowserAccount(primary, authEnvironment);
    ledger.accounts[0].accountId = identity.user.id;
    verifyIdentity(ledger.accounts[0], identity);
    await save(ledgerPath, ledger);
    const targetCredentials = { username: environment.SCHEMII_TEST_POSTGRES_USER || 'schemii', password: async () => {
      if (environment.SCHEMII_TEST_POSTGRES_PASSWORD) return environment.SCHEMII_TEST_POSTGRES_PASSWORD;
      const password = (await readFile(resolve(environment.SCHEMII_SECRET_DIRECTORY || '.schemii/secrets', 'demo_target_password'), 'utf8')).split('\n', 1)[0];
      if (!password) throw new Error('Parallel browser fixtures require the launcher-generated target secret.');
      return password;
    } };
    const firstFixtures = await ensureDatabaseFixtures(primary, targetCredentials, { ownerId: identity.user.id });
    ledger.accounts[0].connections = firstFixtures.connections.map(value => value.id);
    ledger.accounts[0].workspaces = firstFixtures.workspaces.map(value => value.id);
    await save(ledgerPath, ledger);
    const second = ledger.accounts[1];
    ledger.pending = { kind: 'account', identity: { username: second.username } };
    await save(ledgerPath, ledger);
    const account = await json(await primary.post('/api/v1/admin/accounts', { data: {
      username: second.username, password: second.password, display_name: `Browser process ${second.username}`, is_admin: true,
      role_ids: [], direct_access: { capabilities: PARALLEL_CAPABILITIES.filter(value => value !== 'accounts:provision'), connections: [], dashboards: [] },
    } }), 'create second account', 201);
    if (!id(account.id, 'user')) throw new Error('Invalid created second-account identity.');
    second.accountId = account.id;
    ledger.pending = null;
    await save(ledgerPath, ledger);
    secondary = await requestFactory.newContext(contextOptions(baseURL));
    verifyIdentity(second, await json(await secondary.post('/api/v1/auth/login', { data: { username: second.username, password: second.password } }), 'login second account'));
    await ensureDatabaseFixtures(secondary, targetCredentials, { ownerId: second.accountId, fresh: true,
      beforeCreate: async (kind, identity) => { ledger.pending = { kind, identity }; await save(ledgerPath, ledger); },
      onCreate: async (kind, resource) => {
        if (!id(resource.id, kind === 'connection' ? 'pg' : 'ws')) throw new Error('Invalid created parallel fixture identity.');
        second[kind === 'connection' ? 'connections' : 'workspaces'].push(resource.id);
        ledger.pending = null;
        await save(ledgerPath, ledger);
      },
    });
    for (const [index, context] of [primary, secondary].entries()) {
      await verifyParallelAccount(context, ledger.accounts[index], await json(await context.get('/api/v1/auth/me'), 'verify prepared identity'));
      await save(ledger.accounts[index].storageState, await context.storageState());
    }
    const { stage, pending, ...manifest } = ledger;
    validateDocument(manifest, directory, true);
    ledger.stage = 'ready';
    await save(ledgerPath, ledger);
    await save(join(directory, 'accounts.json'), manifest);
    await readParallelAccounts(join(directory, 'accounts.json'));
    return { manifestPath: join(directory, 'accounts.json'), accounts: manifest.accounts.map(({ index, root, storageState }) => ({ index, root, storageState })) };
  } catch (error) {
    try { await cleanupParallelAccounts({ directory, requestFactory }); }
    catch (cleanupError) { throw new AggregateError([error, cleanupError], 'Parallel preparation failed; ownership cleanup also failed. Private ledger retained.'); }
    throw error;
  } finally { await secondary?.dispose(); await primary.dispose(); }
}

/** Reconcile an uncertain write once by its recorded exact identity; never replay it. */
async function reconcile(context, admin, ledger, ledgerPath) {
  if (!ledger.pending) return;
  const { kind, identity } = ledger.pending;
  const second = ledger.accounts[1];
  let candidates;
  if (kind === 'account') {
    if (!keys(identity, ['username']) || identity.username !== second.username) throw new Error('Unrecognized pending account ownership.');
    candidates = (await json(await admin.get('/api/v1/admin/accounts'), 'read back pending account')).filter(value => value.username === second.username);
    if (candidates.length === 1 && (!candidates[0].is_admin || candidates[0].disabled || candidates[0].display_name !== `Browser process ${second.username}`)) throw new Error('Pending account ownership drift.');
  } else if (kind === 'connection') {
    if (!keys(identity, ['database', 'name']) || !databases.includes(identity.database) || identity.name !== `Browser fixture: ${identity.database}`) throw new Error('Unrecognized pending connection ownership.');
    candidates = (await json(await context.get(CONNECTIONS), 'read back pending connection')).connections.filter(value => value.name === identity.name && value.database === identity.database);
    if (candidates.some(value => value.ownerId !== second.accountId || value.ownership !== 'user' || value.host !== 'postgres')) throw new Error('Pending connection ownership drift.');
  } else {
    const index = second.connections.indexOf(identity.connectionId);
    if (!keys(identity, ['connectionId', 'namespace', 'database']) || ![0, 1].includes(index) ||
        identity.database !== databases[index] || identity.namespace !== (index === 0 ? 'bookstore' : 'public')) throw new Error('Unrecognized pending workspace ownership.');
    candidates = (await json(await context.get(WORKSPACES), 'read back pending workspace')).workspaces.filter(value => value.connectionId === identity.connectionId && value.namespace === identity.namespace && value.database === identity.database);
  }
  if (candidates.length > 1) throw new Error('Pending parallel fixture identity is ambiguous.');
  if (candidates.length) {
    const resourceId = candidates[0].id;
    if (kind === 'account') second.accountId = resourceId;
    else if (!second[kind === 'connection' ? 'connections' : 'workspaces'].includes(resourceId)) second[kind === 'connection' ? 'connections' : 'workspaces'].push(resourceId);
  }
  ledger.pending = null;
  await save(ledgerPath, ledger);
}

/** Call only after both worker processes stop. Primary resources are preserved. */
export async function cleanupParallelAccounts({ directory, manifestPath, requestFactory }) {
  directory ||= dirname(manifestPath);
  await privatePath(directory, true);
  const ledgerPath = join(directory, 'ownership.json');
  const ledger = validateDocument(await readPrivate(ledgerPath), directory, false);
  async function ownedRoots() {
    const roots = [];
    for (const account of ledger.accounts) {
      try { await privatePath(account.root, true); roots.push(account.root); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    return roots;
  }
  const existingRoots = await ownedRoots();
  if (ledger.stage === 'cleaned') {
    if (existingRoots.length) throw new Error('Process roots reappeared after completed cleanup; preserve unrecognized work.');
    return;
  }
  const [first, second] = ledger.accounts;
  if (!first.accountId) throw new Error('Primary authentication did not complete; private preparation receipt retained.');
  const admin = await requestFactory.newContext(contextOptions(ledger.baseURL));
  let context;
  async function completed() {
    const roots = await ownedRoots();
    await unlink(join(directory, 'accounts.json')).catch(error => { if (error.code !== 'ENOENT') throw error; });
    // Workers have stopped and selected evidence has been exported. Recursive
    // removal unlinks nested source/cache links; it never follows their targets.
    for (const root of roots) {
      await rm(root, { recursive: true });
      await lstat(root).then(() => { throw new Error('Disposable parallel process root remains present.'); }, error => { if (error.code !== 'ENOENT') throw error; });
    }
    ledger.stage = 'cleaned';
    // Top-level ownership.json is retained private evidence, not disposed staging.
    await save(ledgerPath, ledger);
  }
  try {
    verifyIdentity(first, await json(await admin.post('/api/v1/auth/login', { data: { username: first.username, password: first.password } }), 'login cleanup administrator'));
    if (ledger.pending?.kind === 'account') await reconcile(null, admin, ledger, ledgerPath);
    if (!second.accountId) { await completed(); return; }
    if (ledger.stage === 'deleting-account') {
      const accounts = await json(await admin.get('/api/v1/admin/accounts'), 'read back account deletion');
      if (!accounts.some(value => value.id === second.accountId)) { await completed(); return; }
    }
    context = await requestFactory.newContext(contextOptions(ledger.baseURL));
    verifyIdentity(second, await json(await context.post('/api/v1/auth/login', { data: { username: second.username, password: second.password } }), 'login cleanup owner'));
    await reconcile(context, admin, ledger, ledgerPath);
    ledger.stage = 'cleaning';
    await save(ledgerPath, ledger);
    // Reverse dependency order, exact recorded IDs and current revision checks.
    for (const [base, resourceIds] of [[WORKSPACES, second.workspaces], [CONNECTIONS, second.connections]]) {
      for (const resourceId of [...resourceIds].reverse()) {
        const response = await context.get(`${base}/${resourceId}`);
        if (response.status() !== 404) {
          const resource = await json(response, 'inspect owned cleanup resource');
          if (!Number.isInteger(resource.revision) || resource.revision < 1 || (base === CONNECTIONS &&
              (resource.ownerId !== second.accountId || resource.ownership !== 'user' || !databases.includes(resource.database) || resource.host !== 'postgres' || resource.name !== `Browser fixture: ${resource.database}`)) ||
              (base === WORKSPACES && (resource.connectionId !== second.connections[second.workspaces.indexOf(resourceId)] ||
                resource.database !== databases[second.workspaces.indexOf(resourceId)] || resource.namespace !== (second.workspaces.indexOf(resourceId) === 0 ? 'bookstore' : 'public')))) throw new Error('Parallel cleanup resource ownership drift.');
          await json(await context.delete(`${base}/${resourceId}?expectedRevision=${resource.revision}`), 'delete owned cleanup resource', 204);
        }
        if ((await context.get(`${base}/${resourceId}`)).status() !== 404) throw new Error('Parallel cleanup resource remains present.');
      }
    }
    for (const [path, collection] of [[WORKSPACES, 'workspaces'], [CONNECTIONS, 'connections'], ['/api/v1/schemoo/models', 'models'], ['/api/v1/schemer/dashboards', 'dashboards']]) {
      if ((await json(await context.get(path), 'inspect remaining owned work'))[collection].length) throw new Error('Unrecorded parallel account work remains; preserve the account and private ledger.');
    }
    const accounts = await json(await admin.get('/api/v1/admin/accounts'), 'verify account before deletion');
    const account = accounts.find(value => value.id === second.accountId);
    if (!account || account.username !== second.username || account.display_name !== `Browser process ${second.username}` || second.accountId === first.accountId) throw new Error('Parallel cleanup account ownership drift.');
    ledger.stage = 'deleting-account';
    await save(ledgerPath, ledger);
    await json(await admin.delete(`/api/v1/admin/accounts/${second.accountId}`), 'delete second account', 204);
    if ((await json(await admin.get('/api/v1/admin/accounts'), 'verify second account removed')).some(value => value.id === second.accountId)) throw new Error('Parallel cleanup account remains present.');
    await completed();
  } finally { await context?.dispose(); await admin.dispose(); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let receiptPath, operation, directory;
  try {
    const [requestedOperation, requestedDirectory, receiptArgument, ...extra] = process.argv.slice(2);
    operation = requestedOperation; directory = requestedDirectory;
    if (!['prepare', 'cleanup'].includes(operation) || !directory || !receiptArgument?.startsWith('--receipt=') || extra.length) {
      throw new Error('Use parallel-account.js prepare|cleanup ABSOLUTE_OWNED_DIRECTORY --receipt=ABSOLUTE_FILE_OUTSIDE_DIRECTORY.');
    }
    const candidate = receiptArgument.slice(10);
    if (!isAbsolute(candidate) || resolve(candidate) !== candidate || !isAbsolute(directory) || !relative(directory, candidate).startsWith(`..${sep}`)) {
      throw new Error('Operation receipts require an absolute path outside the disposable account directory.');
    }
    const parent = await lstat(dirname(candidate));
    if (!parent.isDirectory() || parent.isSymbolicLink() || parent.uid !== process.getuid()) throw new Error('Operation receipt parent must be an existing owned directory.');
    await lstat(candidate).then(() => { throw new Error('Operation receipt already exists; preserve the original attempt.'); }, error => { if (error.code !== 'ENOENT') throw error; });
    receiptPath = candidate;
    const { request } = await import('@playwright/test');
    if (operation === 'prepare') {
      const result = await prepareParallelAccounts({ directory, requestFactory: request });
      await save(receiptPath, { schema: 1, operation, outcome: 'passed', physicalTargets: 'shared', cleanup: 'pending' });
      console.log(JSON.stringify({ schema: 1, prepared: true, manifestPath: result.manifestPath, physicalTargets: 'shared' }));
    } else {
      await cleanupParallelAccounts({ directory, requestFactory: request });
      await save(receiptPath, { schema: 1, operation, outcome: 'passed', primaryPreserved: true, cleanup: 'completed' });
      console.log(JSON.stringify({ schema: 1, cleaned: true, primaryPreserved: true }));
    }
  } catch {
    if (receiptPath) {
      try { await save(receiptPath, { schema: 1, operation, outcome: 'failed', cleanup: 'unconfirmed' }); }
      catch { console.error('Parallel browser operation receipt could not be retained.'); }
    }
    console.error('Parallel browser account operation failed. Inspect the private ownership ledger and retained operation receipt.');
    process.exitCode = 1;
  }
}
