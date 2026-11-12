import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

class Node {
  constructor(tag, options = {}, children = []) {
    Object.assign(this, options, { tag, children: [], listeners: {}, value: '' });
    this.attributes = options.attrs || {}; this.value = this.attributes.value || '';
    if (options.text) this.textContent = options.text;
    this.append(...children);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  async fire(name) { return this.listeners[name]?.(); }
  showModal() { this.open = true; }
  close() { this.open = false; this.listeners.close?.(); }
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const source = readFileSync(new URL('../../src/schemii/schemii/web/assets/design-deployment.js', import.meta.url), 'utf8');
const module = runInNewContext(`${source.replace(/^import .*;\n/gm, '').replaceAll('export function ', 'function ')}\n({ createDesignDeploymentController, detachedDesignEligible })`, {
  AbortController,
  element: (tag, options, children) => new Node(tag, options, children),
  replace: (node, ...children) => node.replaceChildren(...children), errorPanel: error => new Node('error', { text: error.message }),
});
function fixture(overrides = {}) {
  const calls = [], opened = [], notices = [], elements = Object.fromEntries(['dialog', 'connection', 'namespace', 'body', 'status', 'review', 'copy'].map(key => [key, new Node(key)]));
  let context = { workspace: { id: 'source' }, design: { workspaceId: 'source' } };
  const preview = { ready: true, targetWorkspaceId: 'target', sourceRevision: 1, targetRevision: 2, targetLabel: 'data · Quoted schema',
    summary: { title: '1 added table', changeCount: 1, changes: [{ operation: 'added', kind: 'table', name: 'Incoming' }] },
    collisions: [], warnings: [], reviewDigest: 'review' };
  const api = {
    listConnections: async () => [{ id: 'connection', name: 'Target', database: 'data', username: 'author' }],
    listConnectionNamespaces: async () => ({ namespaces: [{ name: 'Quoted schema' }] }),
    openPostgresWorkspace: async body => { calls.push(['open', body]); return { workspace: { id: 'target' } }; },
    reviewDesignDeployment: async (id, body) => { calls.push(['review', id, body]); return preview; },
    copyDesignDeployment: async (id, body) => { calls.push(['copy', id, body]); return {}; }, ...overrides,
  };
  const controller = module.createDesignDeploymentController({ api, elements, getContext: () => context,
    openTarget: async value => opened.push(value), notify: value => notices.push(value) });
  return { controller, elements, calls, opened, notices, preview,
    navigate: () => { context = { workspace: { id: 'other' }, design: { workspaceId: 'other' } }; },
    async choose() { await controller.open(); elements.connection.value = 'connection'; elements.namespace.value = 'Quoted schema'; },
  };
}
const texts = node => [node.textContent || '', ...node.children.map(texts)].join(' ');

test('copy requires a detached design and follows explicit target review before migration', async () => {
  assert.equal(module.detachedDesignEligible({ id: 'one', connectionId: 'pg' }, { workspaceId: 'one' }), false);
  assert.equal(module.detachedDesignEligible({ id: 'one' }, { workspaceId: 'other' }), false);
  const f = fixture(); await f.choose();
  assert.equal(f.elements.copy.disabled, true); await f.controller.review();
  assert.equal(f.elements.copy.disabled, false); await f.controller.copy();
  assert.deepEqual(f.calls.map(call => call[0]), ['open', 'review', 'copy']);
  assert.equal(f.calls[0][1].namespace, 'Quoted schema'); assert.equal(f.calls[2][1], 'source');
  assert.equal(f.calls[2][2].targetWorkspaceId, 'target'); assert.equal(f.calls[2][2].reviewDigest, 'review');
  assert.equal(f.opened[0].id, 'target'); assert.equal(f.elements.dialog.open, false);
});

test('navigation ignores late target and review responses without copying or changing workspace', async () => {
  const gate = deferred(), f = fixture({ openPostgresWorkspace: () => gate.promise });
  await f.choose(); const pending = f.controller.review(); f.navigate();
  gate.resolve({ workspace: { id: 'late-target' } }); await pending;
  assert.equal(f.calls.length, 0); assert.equal(f.opened.length, 0); assert.equal(f.elements.copy.disabled, true);
  f.controller.close();
});

test('copy error retains both workspace contexts and requires a new review rather than retrying a digest', async () => {
  const f = fixture({ copyDesignDeployment: async () => { throw new Error('Revision changed'); } });
  await f.choose(); await f.controller.review(); await f.controller.copy();
  assert.equal(f.elements.copy.disabled, true); assert.equal(f.opened.length, 0); assert.equal(f.elements.dialog.open, true);
  assert.match(texts(f.elements.status), /Revision changed/); assert.match(texts(f.elements.status), /Both workspaces remain available/);
  await f.controller.copy(); assert.equal(f.opened.length, 0); f.controller.close();
});

test('connection loading errors and empty connections have recoverable visible states', async () => {
  for (const [api, expected] of [[{ listConnections: async () => [] }, /No PostgreSQL connections/],
    [{ listConnections: async () => { throw new Error('Offline'); } }, /Offline/]]) {
    const f = fixture(api); await f.controller.open();
    assert.match(texts(f.elements.status), expected); assert.equal(f.elements.copy.disabled, true);
    f.controller.close();
  }
});

test('unresolved collisions disable copy; changed decisions require server review', async () => {
  const f = fixture(); f.preview.ready = false;
  f.preview.collisions = [{ key: 'column', label: 'tables / T / columns / id', target: { dataType: 'integer' }, incoming: { dataType: 'bigint' } }];
  await f.choose(); await f.controller.review();
  assert.equal(f.elements.copy.disabled, true);
  const collision = f.elements.body.children.find(node => node.className === 'deployment-collision');
  const choice = collision.children.find(node => node.tag === 'select'); choice.value = 'incoming'; choice.onchange();
  assert.match(texts(f.elements.status), /Review the copy again/); assert.equal(f.elements.copy.disabled, true);
  await f.controller.review(); assert.equal(f.calls.at(-1)[2].resolutions.column, 'incoming'); f.controller.close();
});
