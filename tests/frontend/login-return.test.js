import test from 'node:test';
import assert from 'node:assert/strict';
import { safeReturnPath, loginUrl } from '../../src/schemii/common/web/assets/login-return.js';
import { signInDestination } from '../../src/schemii/common/web/assets/accounts-session.js';

test('a signed-out report link preserves its query and fragment through login', () => {
  const url = loginUrl({ pathname: '/schemer', search: '?dashboard=report_123', hash: '#results' });
  assert.equal(signInDestination({ capabilities: ['schemer:access'] }, url.slice(url.indexOf('?'))), '/schemer?dashboard=report_123#results');
});

test('return paths reject external destinations, ambiguous paths, and non-product endpoints', () => {
  for (const value of [null, '', 'https://evil.example', '//evil.example/schemer', '/\\evil.example', '/schemer\n', '/login', '/api/v1/auth/logout', '/%2f%2fevil.example']) {
    assert.equal(safeReturnPath(value), null, String(value));
  }
  assert.equal(safeReturnPath('/schemoo?model=abc'), '/schemoo?model=abc');
});

test('return paths respect the signed-in account product permissions', () => {
  const viewer = { capabilities: ['schemer:access'] };
  assert.equal(signInDestination(viewer, '?next=%2Fschemoo%3Fmodel%3Dprivate'), '/schemer');
  assert.equal(signInDestination(viewer, '?next=%2Fadmin'), '/schemer');
  assert.equal(signInDestination(viewer, '?next=%2Faccount'), '/account');
  assert.equal(signInDestination({ is_admin: true, capabilities: [] }, '?next=%2Fadmin'), '/admin');
  assert.equal(signInDestination(viewer, '?next=https%3A%2F%2Fevil.example'), '/schemer');
});
