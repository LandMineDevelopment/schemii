import test from 'node:test';
import assert from 'node:assert/strict';
import { canAccessProduct, canAuthor, landingPath } from '../../src/schemii/common/web/assets/accounts-session.js';

test('provisioners and report viewers cannot enter authoring without explicit product rights', () => {
  for (const account of [undefined, {}, { capabilities: [] }, { capabilities: ['schemer:access'] }, { is_admin: true, capabilities: ['accounts:provision'] }]) {
    assert.equal(canAuthor(account), false);
  }
});

test('products are independent and Schemer editing needs its own capability', () => {
  const schema = { capabilities: ['schemii:access'] };
  assert.equal(canAccessProduct(schema, 'schemii'), true);
  assert.equal(canAccessProduct(schema, 'schemoo'), false);
  assert.equal(canAuthor(schema, 'schemii'), true);
  assert.equal(canAuthor(schema), false);
  const reports = { capabilities: ['schemer:access', 'schemer:author'] };
  assert.equal(canAuthor(reports), true);
  assert.equal(landingPath(reports), '/schemer');
  assert.equal(landingPath({ is_admin: true, capabilities: ['accounts:provision'] }), '/admin');
});
