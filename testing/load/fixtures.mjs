import { randomBytes, createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { isDeepStrictEqual as same } from 'node:util';
import { HTTPClient } from './http.mjs';
import { oracleFor, ProtocolFailure } from './protocol.mjs';
import { privateJSON, writeJSON, root } from '../harness/store.mjs';
import { readPrivateJSON, processIdentity } from './runtime.mjs';
import { readSnapshot, writeSnapshot, readFixtureSecret, journalRecords } from './journal.mjs';
import { sampleSource } from './ownership.mjs';

const bad = code => { throw new ProtocolFailure(code); };
const readConfig = path => readSnapshot(path, 1024 * 1024);
export const fixtureSpec = JSON.parse(readFileSync(new URL('./fixture-spec.json', import.meta.url), 'utf8'));
export const explore = fixtureSpec.explore;
const fields = explore.fields;
export function rowOracles(columns) {
  return { rows: oracleFor(columns, Array.from({ length: 513 }, (_, i) => [i + 1])),
    csv: oracleFor(columns, Array.from({ length: 513 }, (_, i) => [String(i + 1)])) };
}
export async function compiledColumns(sql, { launch = spawn } = {}) {
  const python = existsSync(join(root, '.venv/bin/python')) ? join(root, '.venv/bin/python') : 'python';
  return new Promise((resolve, reject) => {
    const child = launch(python, [join(root, 'testing/load/compiled_columns.py')], { stdio: ['pipe', 'pipe', 'ignore'] });
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
export async function prepareFixtures({ dir, accounts, credentialMap, stateDir, kind = 'cheap', origin, Client = HTTPClient,
  readColumns = compiledColumns }) {
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
          definition: fixtureSpec.definition }, 201);
        if (model.ownerId !== account.userId || model.connectionId !== slot.connectionId || model.namespace !== slot.schema) bad('model_ownership_changed');
        account.model = { id: model.id, revision: model.revision, name: modelName };
        account.owned.push({ kind: 'model', ...account.model }); account.pending = null; await save();
      }
      if (!account.oracles) {
        const compiled = await client.json('POST', `/api/v1/schemoo/models/${account.model.id}/plan`, {
          expectedRevision: account.model.revision, explore });
        const columns = await readColumns(compiled.sql);
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
  let receipt; try { receipt = await readSnapshot(file); } catch (error) { if (error.code === 'ENOENT') return { objectsRemaining: 0 }; throw error; }
  if (receipt.kind === 'disposable') return cleanupOwnedFixtures({ dir, receipt, origin, Client });
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

const POOL = 'user_schemii_connection_pool';
const CAPS = ['schemii:access', 'schemoo:access', 'schemer:access', 'schemer:author'];
const contains = (actual, expected) => expected !== null && typeof expected === 'object' ?
  actual !== null && typeof actual === 'object' && Object.entries(expected).every(([key, value]) => contains(actual[key], value)) : actual === expected;
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export function populationSpec(spec) {
  if (spec.fixtureMode !== 'disposable' || !Number.isSafeInteger(spec.registeredIdentities) || spec.registeredIdentities < spec.activeIdentities || spec.registeredIdentities > 1000 ||
      spec.profilesPerOwner < 1 || spec.profilesPerOwner > 100 || spec.profileMode === 'managed-distinct' && spec.activeIdentities > 100) bad('invalid_population_spec');
  const lanes = spec.workload === 'mix' ? spec.mix.map(item => item.workload) : [spec.workload];
  return { registered: spec.registeredIdentities, active: spec.activeIdentities, mode: spec.profileMode, profiles: spec.profilesPerOwner,
    wide: lanes.includes('wide-rows'), shared: lanes.includes('shared-report'), viewersOnly: spec.workload === 'shared-report' };
}
// Fixed launcher profile, never caller-selected tuning. The effective runtime
// receipt still needs independent qualification; this only binds preparation.
export const LOAD_FIXTURE_PROFILE = {
  name: 'representative-load-v1', version: 1, purpose: 'representative-one-api-load-fixture',
  allocations: {
    schemii: { memoryBytes: 2147483648, nanoCpus: 2000000000, pids: 256 },
    metadata: { memoryBytes: 1073741824, nanoCpus: 1000000000, pids: 256 },
    source: { memoryBytes: 2147483648, nanoCpus: 2000000000, pids: 256 },
    ingress: { memoryBytes: 134217728, nanoCpus: 500000000, pids: 64 },
    'observer-init': { memoryBytes: 67108864, nanoCpus: 250000000, pids: 16 },
  },
  metadata: { normalMaximum: 16, readinessMaximum: 2, statementTimeoutMs: 5000, lockTimeoutMs: 1000, idleTransactionTimeoutMs: 5000, shutdownTimeoutSeconds: 15 },
  sourceAdmission: { ordinaryTotal: 20, ordinaryPerIdentity: 4, retainedTotal: 19, retainedPerIdentity: 3 },
  database: { maximumConnections: 100, sharedBuffers: '128MB' }, apiProcesses: 1,
  instrumentation: 'fixture_only_stock_asgi_lifespan_and_owner_sampler', instrumentationOverheadMeasured: false, stockCapacityEligible: false,
};
export function qualifiedFixtureRuntime(value) {
  const runtime = value.runtimeReceipt, profile = LOAD_FIXTURE_PROFILE;
  if (runtime?.version !== 1 || runtime.run !== value.run || runtime.scope !== 'startup-effective-configuration' ||
      !same(runtime.profile, profile) || runtime.apiProcesses !== 1 || runtime.capacityAcceptance !== false ||
      runtime.instrumentation !== profile.instrumentation || runtime.instrumentationOverheadMeasured !== false || runtime.stockCapacityEligible !== false ||
      !same(runtime.metadata, { normalMaximum: 16, readinessMaximum: 2 }) || runtime.sourceOrdinaryMaximum !== 20 || runtime.sourcePerIdentityMaximum !== 4 ||
      !same(runtime.databaseSettings, { metadata: profile.database, source: profile.database }) ||
      Object.keys(runtime.effectiveAllocations || {}).sort().join() !== Object.keys(profile.allocations).sort().join()) bad('fixture_runtime_unqualified');
  for (const [service, allocation] of Object.entries(profile.allocations)) {
    const actual = runtime.effectiveAllocations[service];
    if (!same(Object.fromEntries(Object.keys(allocation).map(key => [key, actual[key]])), allocation) || !actual.containerId || !actual.created || !actual.image) bad('fixture_runtime_budget_changed');
    const identity = service === 'schemii' ? value.application : service === 'source' ? value.source : null;
    if (identity && (actual.containerId !== identity.id || actual.created !== identity.created || actual.image !== identity.image)) bad('fixture_runtime_owner_changed');
  }
  return runtime;
}
export async function heldFixture(path, { readIdentity = processIdentity, now = Date.now() } = {}) {
  const value = await readConfig(path);
  if (value.version !== 1 || value.kind !== 'launcher_owned_disposable_pressure' || value.status !== 'held' || !/^pressure_[a-f0-9]{32}$/.test(value.run || '') ||
      !/^[a-f0-9]{64}$/.test(value.nonce || '') || !value.directory || !path.startsWith(value.directory + '/') ||
      value.canonicalOrigin !== 'https://localhost:8001' || value.stockCapacityEligible !== false || !same(value.profile, LOAD_FIXTURE_PROFILE) ||
      !Number.isFinite(Date.parse(value.expiresAt)) || Date.parse(value.expiresAt) <= now ||
      !Number.isFinite(Date.parse(value.heartbeatAt)) || now - Date.parse(value.heartbeatAt) > 15000 || Date.parse(value.heartbeatAt) > now + 5000 ||
      value.principal?.username !== value.run || value.principal?.run !== value.run || !value.principal?.userId || !value.launcherPath) bad('held_fixture_unqualified');
  qualifiedFixtureRuntime(value);
  if (value.osRuntimeReceiptFile !== `${value.directory}/os-runtime.json`) bad('fixture_os_receipt_required');
  const osDocument = await readSnapshot(value.osRuntimeReceiptFile, 1024 * 1024, true), osReceipt = osDocument.value;
  if (value.osRuntimeReceiptSha256 !== osDocument.sha256 || osReceipt.version !== 1 || osReceipt.origin !== value.canonicalOrigin || !Array.isArray(osReceipt.containers) ||
      osReceipt.containers.map(item => item.service).sort().join() !== 'ingress,metadata,schemii,source' ||
      osReceipt.fixture?.version !== 1 || osReceipt.fixture.run !== value.run || osReceipt.fixture.nonce !== value.nonce ||
      osReceipt.fixture.project !== `schemii-pressure-${value.run.slice(9)}` || !same(osReceipt.fixture.profile, value.profile) ||
      !same(osReceipt.fixture.owner, value.owner) || !/^[a-f0-9]{64}$/.test(osReceipt.fixture.sourceAssetsSha256 || '') || osReceipt.fixture.stockCapacityEligible !== false) bad('fixture_os_receipt_unqualified');
  for (const [service, identity] of [['schemii', value.application], ['source', value.source]]) {
    const matches = osReceipt.containers.filter(item => item.service === service);
    if (matches.length !== 1 || matches[0].id !== identity?.id || matches[0].image !== identity?.image) bad('fixture_os_receipt_owner_changed');
  }
  if ((await readIdentity(value.owner?.pid)).birthTick !== value.owner.start) bad('held_fixture_owner_changed');
  const credentials = await readConfig(value.credentialsFile), witness = await readConfig(value.sourceWitnessConfig);
  if (credentials.version !== 1 || credentials.run !== value.run || witness.version !== 2 || witness.runId !== `load_${value.run.slice(-16)}` ||
      credentials.admin.username !== value.principal.username || credentials.admin.userId !== value.principal.userId) bad('fixture_credentials_changed');
  const admin = { ...credentials.admin, password: await readFixtureSecret(credentials.admin.passwordFile, value.directory) };
  const sourceAdminConnection = { ...credentials.sourceAdminConnection }, appSourceConnection = { ...credentials.appSourceConnection };
  sourceAdminConnection.password = await readFixtureSecret(sourceAdminConnection.passwordFile, value.directory); delete sourceAdminConnection.passwordFile;
  appSourceConnection.password = await readFixtureSecret(appSourceConnection.passwordFile, value.directory); delete appSourceConnection.passwordFile;
  if (sourceAdminConnection.dbname !== witness.authority.database || sourceAdminConnection.host !== witness.authority.endpoint.host || sourceAdminConnection.port !== witness.authority.endpoint.port ||
      sourceAdminConnection.sslmode !== witness.authority.endpoint.sslmode || appSourceConnection.database !== witness.authority.database || appSourceConnection.username !== witness.authority.role) bad('fixture_source_mapping_changed');
  return { ...value, rawKind: value.kind, kind: 'schemii-pressure-load-hold', path, osRuntimeReceiptObjectSha256: digest(osReceipt), tag: witness.runId, origin: value.canonicalOrigin, admin,
    sourceAdminConnection, appSourceConnection, sourceAdminRole: credentials.sourceAdminRole };
}
export async function fixtureCommand(authority, action, attachment, { launch = spawn } = {}) {
  if (!['attach', 'heartbeat', 'detach'].includes(action) || action !== 'attach' && attachment !== `${authority.directory}/attachment.json`) bad('fixture_control_invalid');
  const args = action === 'attach' ? ['--metadata-pressure-attach', authority.directory, String(process.pid)] :
    [`--metadata-pressure-${action}`, authority.directory, attachment];
  return new Promise((resolve, reject) => {
    const child = launch(authority.launcherPath, args, { stdio: ['ignore', 'pipe', 'ignore'] }); let output = '';
    child.stdout.on('data', part => { output += part; if (output.length > 65536) child.kill('SIGKILL'); });
    const timer = setTimeout(() => child.kill('SIGKILL'), 15000);
    child.once('error', () => { clearTimeout(timer); reject(new ProtocolFailure('fixture_control_unavailable')); });
    child.once('close', code => {
      clearTimeout(timer);
      try { if (code !== 0) bad('fixture_control_unavailable'); const result = JSON.parse(output); if (result.status !== ({ attach: 'attached', heartbeat: 'attached', detach: 'detaching' })[action] || action === 'attach' && result.attachmentFile !== `${authority.directory}/attachment.json`) bad('fixture_control_invalid'); resolve(result); }
      catch { reject(new ProtocolFailure('fixture_control_unavailable')); }
    });
  });
}
export async function disposedFixtureConsumer(binding, owner, { read = readConfig } = {}) {
  const authority = await read(binding.authorityFile), journal = await read(`${authority.directory}/journal.json`), attachment = await read(binding.attachmentFile);
  if (!same(authority.profile, LOAD_FIXTURE_PROFILE) || !same(journal.profile, authority.profile) || authority.run !== binding.run || authority.nonce !== binding.nonce || journal.run !== binding.run || journal.hold?.nonce !== binding.nonce ||
      journal.cleanup !== true || journal.restored !== true || journal.stage !== 'restored' || journal.hold.attachment !== null ||
      attachment.status !== 'joined' || attachment.run !== binding.run || attachment.nonce !== binding.nonce ||
      attachment.consumer?.pid !== owner?.pid || attachment.consumer.start !== owner?.birthTick ||
      JSON.stringify(attachment.consumer) !== JSON.stringify(binding.consumer)) bad('fixture_disposal_not_proven');
  return { fixtureDisposed: true, sourceAndApplicationFixtureCleanup: 'launcher_verified', retainedApplicationRestored: true };
}
export async function sourceFixture(action, config, receipt, { launch = spawn } = {}) {
  const python = join(root, '.venv/bin/python');
  await new Promise((resolve, reject) => {
    const child = launch(python, ['-m', 'testing.load.source_fixture', action, '--config', config, '--receipt', receipt],
      { cwd: root, stdio: 'ignore', env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('PG'))) });
    const timer = setTimeout(() => child.kill('SIGKILL'), 300000);
    child.once('error', () => { clearTimeout(timer); reject(new ProtocolFailure('source_fixture_unavailable')); });
    child.once('close', code => { clearTimeout(timer); code === 0 ? resolve() : reject(new ProtocolFailure('source_fixture_unavailable')); });
  });
  return readConfig(receipt);
}

// Mutation receipts bind a secret run nonce, exact request, owner, and pre-create
// IDs. An uncertain send is never replayed based only on an empty name lookup.
export async function createOwnedResource({ receipt, save, client, key, kind, collection, listKey, body, ownerId, verifyAdoption }) {
  receipt.resources ||= [];
  const list = async () => { const value = await client.json('GET', collection); return listKey ? value[listKey] : value; };
  const label = body.name || body.username;
  if (!label?.startsWith(receipt.tag + '_')) bad('resource_nonce_required');
  let item = receipt.resources.find(value => value.key === key);
  if (item && (item.digest !== digest(body) || item.ownerId !== ownerId || item.collection !== collection)) bad('resource_intent_changed');
  const listing = await list();
  if (!Array.isArray(listing)) bad('resource_listing_invalid');
  if (!item) {
    if (listing.some(value => (value.name || value.username) === label)) bad('unowned_resource_collision');
    item = { key, kind, collection, listKey, ownerId, label, expected: body, digest: digest(body),
      beforeIds: [], beforeListingDigest: digest(listing.map(value => value.id).sort()), phase: 'prepared' };
    receipt.resources.push(item); await save();
  }
  if (item.phase === 'deleted' || item.phase === 'delete_pending') bad('resource_already_cleaning');
  if (!item.value) {
    const candidates = listing.filter(value => (value.name || value.username) === label && !item.beforeIds.includes(value.id));
    if (candidates.length > 1) bad('ambiguous_owned_create');
    if (candidates.length === 1) {
      if (item.phase !== 'sent') bad('unowned_resource_collision');
      item.value = candidates[0];
      if (kind === 'account') {
        if (!verifyAdoption) bad('account_secret_proof_required');
        await verifyAdoption(item.value);
      }
    } else {
      if (item.phase === 'sent') bad('uncertain_create_requires_reconciliation');
      item.phase = 'sent'; await save();
      item.value = await client.json('POST', collection, body, 201);
    }
  }
  const expected = { ...body }; delete expected.password; delete expected.role_ids; delete expected.direct_access;
  if (kind === 'account') {
    delete expected.display_name; // public_user uses display_name, checked below along with login secret.
    if (item.value.username !== body.username || item.value.display_name !== body.display_name || item.value.is_admin !== false) bad('account_ownership_changed');
  } else if (kind !== 'role' && item.value.ownerId !== ownerId) bad('resource_owner_changed');
  if (!item.value.id || !contains(item.value, expected)) bad('resource_response_changed');
  item.phase = 'ready'; await save(); return item.value;
}

export async function prepareOwnedFixtures({ dir, spec, authority, Client = HTTPClient, provisionSource = sourceFixture, readColumns = compiledColumns }) {
  const shape = populationSpec(spec), file = join(dir, 'fixtures-private.json'), populationFile = join(dir, 'population-private.json');
  if (authority.kind !== 'schemii-pressure-load-hold' || authority.status !== 'held' || !same(authority.profile, LOAD_FIXTURE_PROFILE) || !/^load_[a-f0-9]{16}$/.test(authority.tag || '') || !authority.admin?.username || !authority.admin?.password || !authority.admin?.userId) bad('explicit_held_fixture_required');
  let receipt;
  try { receipt = await readSnapshot(file); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!receipt) receipt = { version: 1, kind: 'disposable', tag: authority.tag, profile: authority.profile, population: shape, authorityFile: authority.path, resources: [], accounts: [], executions: [], intents: [] };
  if (receipt.kind !== 'disposable' || receipt.tag !== authority.tag || !same(receipt.profile, authority.profile) || !contains(receipt.population, shape) || receipt.authorityFile !== authority.path) bad('population_scope_changed');
  const save = () => writeSnapshot(file, receipt);
  await save();
  const admin = new Client({ origin: authority.origin, maxBytes: 16 * 1024 * 1024 }), clients = new Map();
  const owned = (client, key, kind, collection, listKey, body, ownerId, verifyAdoption) => createOwnedResource({ receipt, save, client, key, kind, collection, listKey, body, ownerId, verifyAdoption });
  let population;
  try { population = await readSnapshot(populationFile); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!population) {
    population = { version: 1, tag: receipt.tag, shape, accounts: Array.from({ length: shape.registered }, (_, index) => ({
      username: `${receipt.tag}_account_${String(index + 1).padStart(4, '0')}`, password: randomBytes(24).toString('base64url'), index: index + 1 })) };
    await writeJSON(populationFile, population);
  }
  if (population.tag !== receipt.tag || !contains(population.shape, shape)) bad('population_journal_changed');
  try {
    const identity = await admin.login({ username: authority.admin.username, password: authority.admin.password }, authority.admin.userId);
    receipt.adminCookie = admin.cookie; await save();
    if (identity.is_admin !== true) bad('fixture_admin_required');
    for (const entry of population.accounts) {
      const user = await owned(admin, `account-${entry.index}`, 'account', '/api/v1/admin/accounts', null, {
        username: entry.username, display_name: `${receipt.tag}_${entry.index}`, password: entry.password, is_admin: false,
        direct_access: { capabilities: shape.viewersOnly ? ['schemer:access'] : CAPS, connections: [], dashboards: [] },
      }, null, async candidate => {
        const proof = new Client({ origin: authority.origin });
        try { await proof.login({ username: entry.username, password: entry.password }, candidate.id); }
        finally { try { await proof.logout(); } finally { proof.close(); } }
      });
      if (entry.userId && entry.userId !== user.id) bad('population_identity_changed');
      entry.userId = user.id; await writeJSON(populationFile, population);
      if (entry.index > shape.active) continue;
      const client = new Client({ origin: authority.origin, maxBytes: spec.maxBodyBytes }); clients.set(entry.username, client);
      await client.login({ username: entry.username, password: entry.password }, user.id); // Secret-bound ownership, not name adoption.
      let account = receipt.accounts.find(value => value.username === entry.username);
      if (!account) { account = { username: entry.username, userId: user.id, password: entry.password, fixtureTag: receipt.tag, index: entry.index }; receipt.accounts.push(account); }
      account.cookie = client.cookie; account.identityVerifiedAt = new Date().toISOString(); await save();
    }
    const sourceConfig = join(dir, 'source-fixture-config-private.json'), sourceReceipt = join(dir, 'source-fixture-private.json');
    await writeJSON(sourceConfig, { version: 1, tag: receipt.tag, active: shape.active, wide: shape.wide, rows: 513, wideRows: 129, wideCharacters: 8192,
      maximumBytes: 1024 * 1024 * 1024, sourceWitnessConfig: authority.sourceWitnessConfig,
      sourceAdminRole: authority.sourceAdminRole, adminConnection: authority.sourceAdminConnection });
    const dataset = await provisionSource('prepare', sourceConfig, sourceReceipt);
    const managed = shape.mode.startsWith('managed-'), profiles = [];
    if (managed) {
      const count = shape.mode === 'managed-shared' ? 1 : shape.active;
      const current = await admin.json('GET', '/api/v1/admin/schemii-connections');
      const ownReady = receipt.resources.filter(item => item.kind === 'managed-profile' && item.phase === 'ready').length;
      if (current.connections.length + count - ownReady > 100) bad('managed_owner_profile_limit');
      for (let index = 0; index < count; index++) profiles.push(await owned(admin, `managed-${index}`, 'managed-profile', '/api/v1/admin/schemii-connections', 'connections',
        { ...authority.appSourceConnection, name: `${receipt.tag}_managed_${index}` }, POOL));
      await owned(admin, 'managed-fixture-role', 'role', '/api/v1/admin/roles', null, { name: `${receipt.tag}_fixture_author`,
        user_ids: [authority.admin.userId], capabilities: CAPS,
        connections: profiles.map(value => ({ connection_id: value.id, owner_id: POOL, allow_authoring: true })), dashboards: [] }, null);
      if (!shape.viewersOnly) await owned(admin, 'managed-author-role', 'role', '/api/v1/admin/roles', null, { name: `${receipt.tag}_authors`,
        user_ids: receipt.accounts.map(value => value.userId), capabilities: CAPS,
        connections: profiles.map(value => ({ connection_id: value.id, owner_id: POOL, allow_authoring: true })), dashboards: [] }, null);
    }
    for (const account of receipt.accounts) {
      const source = dataset.schemas.find(value => value.index === account.index);
      if (!source || source.phase !== 'ready') bad('dataset_not_ready');
      const client = clients.get(account.username);
      account.source = { schema: source.name, database: authority.appSourceConnection.database, nonce: source.nonce };
      account.oracles = { sql: oracleFor(['id', 'owner_nonce'], Array.from({ length: 513 }, (_, i) => [i + 1, source.nonce])) };
      if (shape.wide) account.oracles.wide = oracleFor(['id', 'owner_nonce', 'c1', 'c2', 'c3', 'c4'], Array.from({ length: 129 }, (_, i) => [i + 1, source.nonce, ...['W', 'X', 'Y', 'Z'].map(value => value.repeat(8192))]));
      if (shape.viewersOnly) continue;
      account.profiles = managed ? [profiles[shape.mode === 'managed-shared' ? 0 : account.index - 1]] : [];
      if (!managed) {
        const current = await client.json('GET', '/api/v1/connections');
        const ownReady = receipt.resources.filter(item => item.ownerId === account.userId && item.kind === 'private-profile' && item.phase === 'ready').length;
        if (current.connections.length + shape.profiles - ownReady > 100) bad('private_owner_profile_limit');
        for (let index = 0; index < shape.profiles; index++) account.profiles.push(await owned(client, `profile-${account.index}-${index}`, 'private-profile', '/api/v1/connections', 'connections',
          { ...authority.appSourceConnection, name: `${receipt.tag}_private_${account.index}_${index}` }, account.userId));
      }
      account.variants = [];
      for (const [profileIndex, selectedProfile] of account.profiles.entries()) {
      account.source = { ...account.source, connectionId: selectedProfile.id };
      const before = await client.json('GET', '/api/v1/schemii/workspaces');
      let workspaceItem = receipt.resources.find(value => value.key === `workspace-${account.index}-${profileIndex}`);
      if (!workspaceItem) {
        if (before.workspaces.some(value => value.connectionId === account.source.connectionId && value.namespace === source.name)) bad('unowned_workspace_target');
        workspaceItem = { key: `workspace-${account.index}-${profileIndex}`, kind: 'workspace', collection: '/api/v1/schemii/workspaces', listKey: 'workspaces', ownerId: account.userId,
          expected: { connectionId: account.source.connectionId, namespace: source.name }, phase: 'sent' }; receipt.resources.push(workspaceItem); await save();
        const opened = await client.json('POST', '/api/v1/schemii/workspaces/postgres', { connectionId: account.source.connectionId, namespace: source.name }, 201);
        if (opened.created !== true) bad('unowned_workspace_target');
        workspaceItem.value = opened.workspace; workspaceItem.phase = 'ready'; await save();
      }
      if (!workspaceItem.value || workspaceItem.value.ownerId !== account.userId || !contains(workspaceItem.value, workspaceItem.expected)) bad('workspace_authority_changed');
      account.workspace = workspaceItem.value;
      account.settings = await client.json('GET', '/api/v1/schemii/console/settings');
      account.model = await owned(client, `model-${account.index}-${profileIndex}`, 'model', '/api/v1/schemoo/models', 'models', {
        name: `${receipt.tag}_model_${account.index}_${profileIndex}`, connectionId: account.source.connectionId, database: account.source.database, namespace: source.name, definition: fixtureSpec.definition }, account.userId);
      const compiled = await client.json('POST', `/api/v1/schemoo/models/${account.model.id}/plan`, { expectedRevision: account.model.revision, explore });
      Object.assign(account.oracles, rowOracles(await readColumns(compiled.sql)));
      account.dashboards = {};
      for (const count of [1, 5, 20]) account.dashboards[count] = await owned(client, `dashboard-${account.index}-${profileIndex}-${count}`, 'dashboard', '/api/v1/schemer/dashboards', 'dashboards', {
        name: `${receipt.tag}_dashboard_${account.index}_${profileIndex}_${count}`, modelId: account.model.id, modelRevision: account.model.revision,
        tiles: Array.from({ length: count }, (_, i) => ({ id: `tile-${i + 1}`, title: `Rows ${i + 1}`, kind: 'detail', detailFields: fields })) }, account.userId);
      account.variants.push({ source: account.source, workspace: account.workspace, model: account.model, dashboards: account.dashboards, oracles: { ...account.oracles } });
      await save();
      }
      Object.assign(account, account.variants[0]);
    }
    if (shape.shared) {
      const first = dataset.schemas[0], profile = profiles[0];
      const model = await owned(admin, 'shared-model', 'model', '/api/v1/schemoo/models', 'models', {
        name: `${receipt.tag}_shared_model`, connectionId: profile.id, database: authority.appSourceConnection.database, namespace: first.name, definition: fixtureSpec.definition }, authority.admin.userId);
      const compiled = await admin.json('POST', `/api/v1/schemoo/models/${model.id}/plan`, { expectedRevision: model.revision, explore });
      const rows = rowOracles(await readColumns(compiled.sql)).rows;
      const dashboard = await owned(admin, 'shared-dashboard', 'dashboard', '/api/v1/schemer/dashboards', 'dashboards', {
        name: `${receipt.tag}_shared_dashboard`, modelId: model.id, modelRevision: model.revision,
        tiles: [{ id: 'shared-rows', title: 'Shared rows', kind: 'detail', detailFields: fields }] }, authority.admin.userId);
      await owned(admin, 'viewer-role', 'role', '/api/v1/admin/roles', null, { name: `${receipt.tag}_viewers`, user_ids: receipt.accounts.map(value => value.userId),
        capabilities: ['schemer:access'], connections: [{ connection_id: profile.id, owner_id: POOL, allow_authoring: false }],
        dashboards: [{ dashboard_id: dashboard.id, owner_id: authority.admin.userId, connection_id: profile.id, connection_owner_id: POOL, can_export: true, can_drill: true }] }, null);
      for (const account of receipt.accounts) {
        account.sharedDashboard = { id: dashboard.id, revision: dashboard.revision, tiles: { 'shared-rows': rows } };
        const client = clients.get(account.username);
        if ((await client.json('GET', `/api/v1/schemer/dashboards/${dashboard.id}`)).id !== dashboard.id) bad('shared_grant_missing');
        const raw = await client.request({ path: `/api/v1/schemoo/models/${model.id}` });
        if (![403, 404].includes(raw.status)) bad('shared_model_disclosure');
      }
      if (shape.viewersOnly) {
        const proof = await admin.json('GET', '/api/v1/auth/me');
        if (!CAPS.every(capability => proof.capabilities.includes(capability))) bad('fixture_author_grant_missing');
        let recovery = receipt.resources.find(value => value.key === 'recovery-workspace');
        if (!recovery) {
          const before = await admin.json('GET', '/api/v1/schemii/workspaces');
          if (before.workspaces.some(value => value.connectionId === profile.id && value.namespace === first.name)) bad('unowned_recovery_workspace');
          recovery = { key: 'recovery-workspace', kind: 'workspace', collection: '/api/v1/schemii/workspaces', listKey: 'workspaces', ownerId: authority.admin.userId,
            expected: { connectionId: profile.id, namespace: first.name }, phase: 'sent' }; receipt.resources.push(recovery); await save();
          recovery.value = (await admin.json('POST', '/api/v1/schemii/workspaces/postgres', { connectionId: profile.id, namespace: first.name }, 201)).workspace;
          recovery.phase = 'ready'; await save();
        }
        if (!recovery.value || recovery.value.ownerId !== authority.admin.userId || !contains(recovery.value, recovery.expected)) bad('recovery_workspace_changed');
        receipt.recoveryAccount = { username: authority.admin.username, userId: authority.admin.userId, password: authority.admin.password,
          workspace: recovery.value, settings: await admin.json('GET', '/api/v1/schemii/console/settings'), instrumentationOnly: true };
      }
    }
    if (!shape.viewersOnly && receipt.accounts.length > 1) for (let i = 0; i < receipt.accounts.length; i++) {
      const current = receipt.accounts[i], peer = receipt.accounts[(i + 1) % receipt.accounts.length], client = clients.get(current.username);
      for (const path of [`/api/v1/schemoo/models/${peer.model.id}`, `/api/v1/schemii/workspaces/${peer.workspace.id}`,
        ...(!managed ? [`/api/v1/connections/${peer.source.connectionId}`] : [])]) {
        if (![403, 404].includes((await client.request({ path })).status)) bad('cross_owner_access');
      }
    }
    if (receipt.recoveryAccount) {
      const dedicated = new Client({ origin: authority.origin });
      try { await dedicated.login({ username: authority.admin.username, password: authority.admin.password }, authority.admin.userId); receipt.recoveryAccount.cookie = dedicated.cookie; await save(); }
      finally { dedicated.close(); } // Cookie retained in the private cleanup receipt.
    }
    for (const account of receipt.accounts) {
      const current = await clients.get(account.username).json('GET', '/api/v1/auth/me');
      if (current.user?.id !== account.userId || current.user.username !== account.username) bad('prepared_owner_changed');
      account.identityVerifiedAt = new Date().toISOString();
    }
    if (receipt.recoveryAccount) receipt.recoveryAccount.identityVerifiedAt = new Date().toISOString();
    receipt.crossOwnerVerified = receipt.accounts.length > 1; receipt.ready = true; await save(); return receipt;
  } finally {
    for (const client of clients.values()) client.close();
    try { await admin.logout(); delete receipt.adminCookie; await save(); } finally { admin.close(); }
  }
}

export async function cleanupOwnedFixtures({ dir, receipt, origin, Client = HTTPClient, provisionSource = sourceFixture, authority: supplied,
  witness = sampleSource }) {
  const authority = supplied || await heldFixture(receipt.authorityFile);
  if (authority.tag !== receipt.tag || authority.kind !== 'schemii-pressure-load-hold' || authority.status !== 'held' || !same(authority.profile, receipt.profile)) bad('cleanup_fixture_authority_changed');
  const save = () => writeSnapshot(join(dir, 'fixtures-private.json'), receipt);
  const population = await readSnapshot(join(dir, 'population-private.json'));
  const admin = new Client({ origin: origin || authority.origin, maxBytes: 16 * 1024 * 1024 }), clients = new Map();
  for (const field of ['adminCookie', 'cleanupAdminCookie']) if (receipt[field]) {
    const old = new Client({ origin: origin || authority.origin, cookie: receipt[field] });
    try {
      const identity = await old.request({ path: '/api/v1/auth/me' });
      if (identity.status === 200) { if (identity.data.user?.id !== authority.admin.userId || identity.data.user.username !== authority.admin.username) bad('cleanup_admin_cookie_changed'); await old.logout(); }
      else if (identity.status !== 401) bad('cleanup_admin_cookie_unavailable');
      delete receipt[field]; await save();
    } finally { old.close(); }
  }
  await admin.login({ username: authority.admin.username, password: authority.admin.password }, authority.admin.userId);
  receipt.cleanupAdminCookie = admin.cookie; await save();
  try {
    clients.set(authority.admin.userId, admin);
    const survivingAccounts = await admin.json('GET', '/api/v1/admin/accounts');
    for (const entry of population.accounts.filter(value => value.index <= receipt.population.active)) {
      const surviving = survivingAccounts.find(value => value.id === entry.userId);
      if (!surviving) {
        if (!receipt.executionCleanupComplete || receipt.resources.some(item => item.ownerId === entry.userId && item.phase !== 'deleted')) bad('cleanup_account_missing');
        continue;
      }
      if (surviving.username !== entry.username || surviving.is_admin || surviving.disabled) bad('cleanup_account_changed');
      const client = new Client({ origin: origin || authority.origin, cookie: receipt.accounts.find(value => value.username === entry.username)?.cookie });
      clients.set(entry.userId, client);
      const current = await client.request({ path: '/api/v1/auth/me' });
      if (current.status === 401) {
        receipt.cleanupLoginIntent = { username: entry.username, userId: entry.userId, status: 'pending' }; await save();
        await client.login({ username: entry.username, password: entry.password }, entry.userId);
        const account = receipt.accounts.find(value => value.username === entry.username); account.cookie = client.cookie;
        receipt.cleanupLoginIntent = { username: entry.username, userId: entry.userId, status: 'acquired', cookie: client.cookie }; await save();
      } else if (current.status !== 200 || current.data.user?.id !== entry.userId || current.data.user.username !== entry.username) bad('cleanup_owner_cookie_changed');
    }
    const pending = new Map(), executions = new Map((receipt.executions || []).map(entry => [entry.id, entry]));
    if (receipt.executionJournal) for await (const record of journalRecords(receipt.executionJournal)) {
      const event = record.value;
      if (event.type === 'intent') pending.set(record.sequence, event);
      else if (event.type === 'intent-update') {
        const entry = pending.get(event.intentSequence); if (!entry) bad('journey_intent_unknown'); Object.assign(entry, event);
      } else if (event.type === 'execution') {
        const entry = executions.get(event.id);
        if (entry && entry.username !== event.username) bad('execution_owner_changed');
        executions.set(event.id, { ...entry, ...event });
      }
      if (pending.size > 1000000 || executions.size > 1000000) bad('cleanup_journal_memory_budget');
    }
    if (!receipt.executionCleanupComplete) for (const intent of [...(receipt.intents || []), ...pending.values()]) {
      if (intent.kind === 'session' && intent.cookie) {
        const session = new Client({ origin: origin || authority.origin, cookie: intent.cookie });
        try { await session.logout(); intent.cookie = null; intent.status = 'closed'; await save(); } finally { session.close(); }
      } else if (intent.kind !== 'session' && (intent.status === 'pending' || intent.kind === 'execution' && !intent.executionId)) bad('unresolved_journey_mutation');
    }
    let sharedPending = false;
    if (!receipt.executionCleanupComplete) for (const entry of executions.values()) {
      if (entry.closed) continue;
      const account = [...receipt.accounts, ...(receipt.recoveryAccount ? [receipt.recoveryAccount] : [])].find(value => value.username === entry.username), client = clients.get(account?.userId);
      if (!client) bad('unknown_execution_owner');
      if (['report-stream', 'shared-report'].includes(entry.kind)) {
        if (!entry.terminalAt) {
          const cancelled = await client.json('DELETE', `/api/v1/common/query-executions/${entry.id}`);
          if (cancelled.id !== entry.id || !['succeeded', 'failed', 'cancelled', 'partial_committed'].includes(cancelled.status)) bad('unfinished_report_execution');
          entry.terminalAt = new Date().toISOString(); entry.terminal = cancelled.status;
        }
        sharedPending = true; continue; // No raw receipt or invented result IDs.
      }
      const inspected = await client.request({ path: `/api/v1/common/query-executions/${entry.id}` });
      if (inspected.status === 404 && inspected.data?.error?.code === 'console_execution_not_found') { entry.closed = true; continue; }
      if (inspected.status !== 200) bad('execution_cleanup_unavailable');
      const value = inspected.data;
      if (value.id !== entry.id) bad('execution_identity_changed');
      await client.json('DELETE', `/api/v1/common/query-executions/${entry.id}`);
      for (const result of value.results) {
        const response = await client.request({ method: 'DELETE', path: `/api/v1/common/query-executions/${entry.id}/results/${result.id}` });
        if (![204, 404, 410].includes(response.status)) bad('result_cleanup_failed');
      }
      entry.closed = true; await save();
    }
    // Ended/404 application receipts are never used as physical release proof.
    if (executions.size || sharedPending || [...pending.values()].some(intent => intent.kind === 'stream')) {
      const observation = await readPrivateJSON(join(dir, 'ownership-private.json'));
      if (observation.runId !== receipt.tag || observation.sourceConfig !== authority.sourceWitnessConfig) bad('cleanup_source_authority_changed');
      const source = await witness(observation.sourceConfig, observation.sourceOutput);
      if (!source.releaseVerified || source.live.length) bad('cleanup_source_release_unverified');
    }
    receipt.executionCleanupComplete = true; await save();
    if (receipt.recoveryAccount?.cookie) {
      const session = new Client({ origin: origin || authority.origin, cookie: receipt.recoveryAccount.cookie });
      try {
        const identity = await session.request({ path: '/api/v1/auth/me' });
        if (identity.status === 200) { if (identity.data.user?.id !== authority.admin.userId || identity.data.user.username !== authority.admin.username) bad('recovery_cookie_changed'); await session.logout(); }
        else if (identity.status !== 401) bad('recovery_cookie_unavailable');
        delete receipt.recoveryAccount.cookie; await save();
      } finally { session.close(); }
    }
    for (const item of [...receipt.resources].reverse()) {
      if (item.phase === 'deleted') continue;
      if (!item.value?.id) bad('uncertain_create_requires_reconciliation');
      const client = item.ownerId && item.ownerId !== POOL && item.ownerId !== authority.admin.userId ? clients.get(item.ownerId) : admin;
      if (!client) bad('resource_cleanup_owner_unknown');
      const listing = await client.json('GET', item.collection), current = (item.listKey ? listing[item.listKey] : listing).find(value => value.id === item.value.id);
      if (current) {
        if (item.kind === 'account') {
          if (current.username !== item.value.username || current.display_name !== item.value.display_name || current.is_admin) bad('cleanup_account_changed');
        } else if (item.kind === 'role') {
          if (!same(Object.fromEntries(Object.keys(item.expected).map(key => [key, current[key]])), item.expected)) bad('cleanup_role_changed');
        } else {
          if (current.ownerId !== item.ownerId || item.kind !== 'workspace' && current.name !== item.value.name ||
              item.kind === 'workspace' && !contains(current, item.expected)) bad('cleanup_resource_changed');
        }
        item.phase = 'delete_pending'; await save();
        const revision = ['model', 'private-profile'].includes(item.kind) ? `?expected_revision=${current.revision}` :
          ['workspace', 'dashboard', 'managed-profile'].includes(item.kind) ? `?expectedRevision=${current.revision}` : '';
        await client.json('DELETE', `${item.collection}/${current.id}${revision}`, undefined, 204);
      }
      item.phase = 'deleted'; await save();
    }
    await provisionSource('cleanup', join(dir, 'source-fixture-config-private.json'), join(dir, 'source-fixture-private.json'));
    receipt.ready = false; receipt.cleaned = true; await save();
    return { objectsRemaining: 0, registeredAccountsDeleted: population.accounts.length, sessionsRevoked: true, sourceSchemasDeleted: receipt.population.active };
  } finally {
    for (const client of clients.values()) if (client !== admin) client.close();
    try { await admin.logout(); delete receipt.cleanupAdminCookie; await save(); } finally { admin.close(); }
  }
}
