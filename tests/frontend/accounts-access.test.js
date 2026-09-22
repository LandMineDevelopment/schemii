import test from 'node:test';
import assert from 'node:assert/strict';
import { canAuthor } from '../../src/schemii/common/web/assets/accounts-session.js';

test('report viewers cannot enter authoring without an explicit capability', () => {
  for (const account of [undefined, {}, { capabilities: [] }, { capabilities: ['reports'] }, { user: { is_admin: true }, capabilities: [] }]) {
    assert.equal(canAuthor(account), false);
  }
});

test('administrators and explicitly authorized authors can enter authoring', () => {
  assert.equal(canAuthor({ is_admin: true, capabilities: [] }), true);
  assert.equal(canAuthor({ is_admin: false, capabilities: ['author'] }), true);
});
