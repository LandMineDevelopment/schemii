import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { accountCredentials } from '../e2e/helpers/account-auth.js';

test('browser account authentication never implicitly reads local credentials', async () => {
  assert.equal(await accountCredentials({}), null);
  await assert.rejects(accountCredentials({ SCHEMII_E2E_USERNAME: 'operator' }), /both/);
  assert.deepEqual(await accountCredentials({ SCHEMII_E2E_USERNAME: 'operator', SCHEMII_E2E_PASSWORD: 'test-password' }), { username: 'operator', password: 'test-password' });
});

test('explicit credential files must be private and accept nested admin credentials', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'schemii-account-auth-'));
  try {
    const privatePath = join(directory, 'private.json'), publicPath = join(directory, 'public.json');
    const credentials = { username: 'test-operator', password: 'test-password' };
    await writeFile(privatePath, JSON.stringify({ admin: credentials }), { mode: 0o600 });
    await writeFile(publicPath, JSON.stringify(credentials), { mode: 0o644 });
    assert.deepEqual(await accountCredentials({ SCHEMII_E2E_CREDENTIALS_FILE: privatePath }), credentials);
    await assert.rejects(accountCredentials({ SCHEMII_E2E_CREDENTIALS_FILE: publicPath }), /private/);
  } finally { await rm(directory, { recursive: true }); }
});
