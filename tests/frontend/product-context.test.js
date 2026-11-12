import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { CONTEXT_LIMITS, createProductContext, consumeProductContext, assertProductContext, requireContextAccount } from '../../src/schemii/common/web/assets/product-context.js';

const account = { user: { id: 'person-a' }, capabilities: ['schemii:access', 'schemoo:access', 'schemer:access', 'schemer:author'] };
const source = { connectionId: `pg_${'a'.repeat(32)}`, connectionOwnerId: 'person-a', connectionRevision: 3, namespace: 'sales' };
const modelId = `model_${'b'.repeat(32)}`;
const preview = { modelId, modelRevision: 4, explore: { root: 'sales_alias', fields: [{ table: 'sales_alias', column: 'amount', aggregate: 'sum' }],
  selections: { region: { active: true, alternativeId: 'chosen', values: { region: 'private-filter-value' } } },
  reportFilters: [{ id: 'rf1', label: 'East', mode: 'rows', conditions: [{ table: 'sales_alias', column: 'region', operator: 'eq', value: 'East' }] }], limit: 50 } };
function storage() { const values = new Map(); return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }; }
const token = n => `ctx_${String(n).padStart(32, '0')}`;
const extract = path => new URL(path, 'https://localhost:8001').searchParams.get('handoff');

for (const [kind, payload, destination] of [['schema', source, 'schemoo'], ['preview', preview, 'schemer']]) {
  test(`${kind} handoff uses an opaque URL and consumes a frozen transport copy once`, () => {
    const state = storage(), input = structuredClone(payload);
    const path = createProductContext(kind, account, input, { storage: state, token: token(1), now: 1000 });
    assert.equal(new URL(path, 'https://localhost:8001').searchParams.size, 1);
    assert.ok(!path.includes(payload.connectionId || modelId)); assert.ok(!path.includes('private-filter-value'));
    if (kind === 'preview') input.explore.selections.region.values.region = 'later'; else input.namespace = 'different';
    const context = consumeProductContext(extract(path), account, destination, { storage: state, now: 1001 });
    assert.deepEqual(context.payload, payload);
    assert.throws(() => consumeProductContext(extract(path), account, destination, { storage: state, now: 1002 }), /already used/);
  });
}

test('expired, future, wrong-account and wrong-destination records cannot be reused', () => {
  for (const variant of ['expired', 'future', 'account', 'destination']) {
    const state = storage(), path = createProductContext('schema', account, source, { storage: state, token: token(2), now: 1000 });
    const actor = variant === 'account' ? { ...account, user: { id: 'other-person' } } : account;
    const now = variant === 'expired' ? 1000 + CONTEXT_LIMITS.ttlMs : variant === 'future' ? 999 : 1001;
    assert.throws(() => consumeProductContext(extract(path), actor, variant === 'destination' ? 'schemer' : 'schemoo', { storage: state, now }));
    assert.throws(() => consumeProductContext(extract(path), account, 'schemoo', { storage: state, now: 1002 }), /already used/);
  }
});

test('destination author access is required independently of the source app and token', () => {
  const state = storage(), noAuthor = { ...account, capabilities: ['schemoo:access', 'schemer:access'] };
  assert.throws(() => createProductContext('preview', noAuthor, preview, { storage: state }), /cannot create in Schemer/);
  const path = createProductContext('preview', account, preview, { storage: state, token: token(3) });
  assert.throws(() => consumeProductContext(extract(path), noAuthor, 'schemer', { storage: state }), /cannot create in Schemer/);
});

test('envelopes exclude SQL, results and credential fields rather than silently stripping them', () => {
  for (const illegal of [{ ...source, sql: 'SELECT private' }, { ...source, password: 'private' }, { ...source, rows: [[1]] }]) {
    assert.throws(() => createProductContext('schema', account, illegal, { storage: storage() }), /invalid/);
  }
  for (const extra of [{ rows: [[1]] }, { sql: 'SELECT 1' }, { credentials: {} }]) {
    assert.throws(() => createProductContext('preview', account, { ...preview, explore: { ...preview.explore, ...extra } }, { storage: storage() }), /invalid/);
  }
});

test('record count, bytes and TTL stay bounded while unrelated session data is preserved', () => {
  const state = storage(); state.setItem('unrelated', 'keep');
  for (let i = 1; i <= 12; i++) createProductContext('schema', account, source, { storage: state, token: token(i), now: i });
  assert.equal(state.getItem('unrelated'), 'keep');
  const records = JSON.parse([...state.values.values()].find(value => value !== 'keep'));
  assert.equal(Object.keys(records).length, CONTEXT_LIMITS.records);
  assert.ok(new TextEncoder().encode(JSON.stringify(records)).length <= CONTEXT_LIMITS.totalBytes);
  assert.throws(() => consumeProductContext(token(1), account, 'schemoo', { storage: state, now: 20 }), /missing/);
  const large = structuredClone(preview); large.explore.selections.region.values.region = Array.from({ length: 500 }, () => 'x'.repeat(4000));
  assert.throws(() => createProductContext('preview', account, large, { storage: state, token: token(20), now: 20 }), /too large/);
});

test('malformed storage and invalid filter primitives fail without exposing their content', () => {
  const state = storage(); state.setItem('schemii:product-context:v1', '{ private-broken-json');
  assert.throws(() => consumeProductContext(token(1), account, 'schemer', { storage: state }), error => !error.message.includes('private-broken-json'));
  const invalid = structuredClone(preview); invalid.explore.selections.region.values.region = { secret: 'not-a-filter-primitive' };
  assert.throws(() => createProductContext('preview', account, invalid, { storage: state }), /invalid/);
});

test('confirmation rechecks consumed context expiry, identity and envelope shape', () => {
  const state = storage(), path = createProductContext('preview', account, preview, { storage: state, token: token(7), now: 1000 });
  const context = consumeProductContext(extract(path), account, 'schemer', { storage: state, now: 1001 });
  assert.throws(() => assertProductContext(context, account, 'schemer', 1000 + CONTEXT_LIMITS.ttlMs), /expired/);
  assert.throws(() => assertProductContext({ ...context, rows: [[1]] }, account, 'schemer', 1001), /invalid/);
  assert.throws(() => assertProductContext(context, { ...account, user: { id: 'other' } }, 'schemer', 1001), /account changed/);
});

test('Schemoo source resolution checks exact identity, revision and accessible namespace through product-owned reads', async () => {
  const module = readFileSync(new URL('../../src/schemii/schemoo/web/model-library.js', import.meta.url), 'utf8');
  const implementation = module.slice(module.indexOf('export async function resolveSchemaContext('), module.indexOf('export function sourceSelectionControl('));
  const scope = { assertProductContext };
  runInNewContext(implementation.replace('export async function', 'async function') + '\nthis.resolveSchemaContext = resolveSchemaContext;', scope);
  for (const variant of ['valid', 'missing', 'revision', 'owner', 'namespace', 'account-race']) {
    const state = storage(), path = createProductContext('schema', account, source, { storage: state });
    const context = consumeProductContext(extract(path), account, 'schemoo', { storage: state });
    const calls = [];
    const request = async path => {
      calls.push(path);
      if (path.endsWith('/auth/me')) return variant === 'account-race' && calls.length > 1 ? { ...account, user: { id: 'other-person' } } : account;
      if (path.includes('/namespaces')) return { namespaces: [{ name: variant === 'namespace' ? 'other' : 'sales', system: false }] };
      return { connections: variant === 'missing' ? [{ id: `pg_${'f'.repeat(32)}`, name: 'Same name', revision: 3 }]
        : [{ id: source.connectionId, ownerId: variant === 'owner' ? 'different-owner' : 'person-a', revision: variant === 'revision' ? 4 : 3 }] };
    };
    if (variant === 'valid') {
      assert.equal((await scope.resolveSchemaContext(context, request)).id, source.connectionId);
      assert.deepEqual(calls, ['/api/v1/auth/me', '/api/v1/connections?product=schemoo', `/api/v1/connections/${source.connectionId}/namespaces?product=schemoo`, '/api/v1/auth/me']);
    } else await assert.rejects(scope.resolveSchemaContext(context, request));
    assert.ok(calls.every(path => !path.endsWith('/models')));
  }
});

test('saved-preview action blocks unsaved, root-mismatched and superseded work before transferring', async () => {
  const module = readFileSync(new URL('../../src/schemii/schemoo/web/prototype.js', import.meta.url), 'utf8');
  const start = '$("create-dashboard-from-preview").onclick = ';
  const handler = module.slice(module.indexOf(start) + start.length, module.indexOf('\nfunction openLibrary()'));
  for (const variant of ['valid', 'dirty', 'root', 'revision', 'generation', 'pagehide']) {
    const calls = [], transferred = [], errors = [];
    const origin = { id: modelId, revision: 4, layoutRevision: 2, exploreRevision: 3, definition: { root: variant === 'root' ? 'other_alias' : preview.explore.root } };
    const scope = { model: origin, version: 1, dirty: variant === 'dirty', busy: false, saving: false, contextHandoffPending: false, contextHandoffGeneration: 0,
      draft: { explore: preview.explore }, splitDraft: draft => draft, structuredClone, saveBlocked: () => false, save() {}, showError: value => value && errors.push(value),
      API: '/api/v1/schemoo', requireContextAccount, createProductContext: (kind, actor, payload) => { transferred.push({ kind, actor, payload }); return '/schemer?handoff=opaque'; },
      location: { assign: path => transferred.push(path) },
      requestJson: async (path, settings) => {
        calls.push({ path, settings });
        if (path.endsWith('/auth/me')) return account;
        if (path.endsWith('/plan')) { if (variant === 'generation') scope.version++; if (variant === 'pagehide') scope.contextHandoffGeneration++; return {}; }
        return { ...origin, revision: variant === 'revision' ? 5 : 4 };
      } };
    runInNewContext('this.send = ' + handler, scope);
    await scope.send();
    if (variant === 'valid') {
      assert.equal(transferred.length, 2); assert.deepEqual(transferred[0].payload.explore, preview.explore);
      assert.equal(calls.filter(call => call.settings?.method === 'POST').length, 1);
      assert.ok(calls.at(-1).path.endsWith('/plan'));
    } else { assert.equal(transferred.length, 0); assert.equal(errors.length, variant === 'pagehide' ? 0 : 1); }
    if (['dirty', 'root'].includes(variant)) assert.equal(calls.length, 0);
    assert.equal(scope.contextHandoffPending, false);
  }
});

test('Schemii handoff distinguishes detached designs and freezes only the current saved exact live source', async () => {
  const module = readFileSync(new URL('../../src/schemii/schemii/web/assets/app.js', import.meta.url), 'utf8');
  const start = 'byId("create-model-from-schema").addEventListener("click", ';
  const handler = module.slice(module.indexOf(start) + start.length, module.indexOf('\n  byId("deploy-design-button")', module.indexOf(start))).trim().slice(0, -2);
  for (const variant of ['valid', 'detached', 'cancelled-layout', 'navigation', 'source-changed', 'pagehide']) {
    const transferred = [], errors = [], calls = [], guidance = [];
    const origin = { id: 'workspace-live', connectionId: variant === 'detached' ? null : source.connectionId, namespace: source.namespace };
    const scope = { schemaHandoffPending: false, schemaHandoffGeneration: 0, state: { activeWorkspace: origin }, updateHeader() {},
      requireContextAccount, askConfirmation: options => guidance.push(options), designDeployment: { open() {} },
      flushLayoutBeforeTransition: async () => variant !== 'cancelled-layout',
      requestJson: async path => { calls.push(path); return account; },
      api: { getWorkspace: async () => { if (variant === 'navigation') scope.state.activeWorkspace = { ...origin, id: 'other-workspace' }; return { ...origin, namespace: variant === 'source-changed' ? 'other-schema' : source.namespace }; },
        getConnection: async () => { if (variant === 'pagehide') scope.schemaHandoffGeneration++; return { ownerId: 'shared-owner', revision: 3 }; } },
      createProductContext: (kind, actor, payload) => { transferred.push({ kind, actor, payload }); return '/schemoo?handoff=opaque'; },
      location: { assign: path => transferred.push(path) }, errorToast: error => errors.push(error.message) };
    runInNewContext('this.send = ' + handler + ';', scope);
    await scope.send();
    if (variant === 'valid') {
      assert.equal(transferred.length, 2);
      assert.deepEqual(JSON.parse(JSON.stringify(transferred[0].payload)), { ...source, connectionOwnerId: 'shared-owner' });
    } else assert.equal(transferred.length, 0);
    if (variant === 'detached') { assert.equal(guidance.length, 1); assert.match(guidance[0].message, /Undeployed desired DDL/); assert.equal(calls.length, 0); }
    if (variant === 'cancelled-layout') assert.equal(calls.length, 0);
    if (['navigation', 'source-changed'].includes(variant)) assert.equal(errors.length, 1);
    assert.equal(scope.schemaHandoffPending, false);
  }
});
