import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { join } from 'node:path';
import os from 'node:os';
import { prepareFixtures, cleanupFixtures } from './fixtures.mjs';
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
  const state = { crossOwner: false, failAfterCreate: false, drift: false, logouts: 0, intents: 0, serial: 0 };
  class FakeClient {
    constructor({ cookie } = {}) { this.cookie = cookie || ''; }
    async login(credential, expected) {
      this.username = credential.username; this.userId = slots.find(slot => slot.username === this.username).accountId;
      assert.equal(this.userId, expected); this.cookie = `private=${this.username}`;
      return { capabilities: ['schemoo:access', 'schemer:access', 'schemer:author'] };
    }
    async json(method, path, body, expected = 200) {
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
      const store = path.includes('/models') ? modelStore : dashStore;
      const id = path.split('/').at(-1), owned = store.get(this.username).find(value => value.id === id);
      if (!owned) return { status: state.crossOwner ? 200 : 404, data: {} };
      return { status: 200, data: state.drift ? { ...owned, ownerId: 'peer' } : owned };
    }
    async logout() { state.logouts++; this.cookie = ''; }
    close() {}
  }
  const credentialMap = new Map(names.map(username => [username, { username, password: 'private-secret' }]));
  return { dir, names, state, modelStore, dashStore, prepare: options => prepareFixtures({ dir, accounts: names,
    credentialMap, stateDir: dir, kind: 'reports', Client: FakeClient, ...options }),
    cleanup: () => cleanupFixtures({ dir, credentialMap, Client: FakeClient }) };
}
test('owned fixture lifecycle journals each write, checks cross-owner denial and preserves retained objects', async t => {
  const f = await fixture(t), receipt = await f.prepare();
  assert.equal(receipt.crossOwnerVerified, true);
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
  await assert.rejects(f.cleanup(), { code: 'cleanup_ownership_changed' });
  assert.equal(f.dashStore.get(f.names[0]).length, 4);
});
