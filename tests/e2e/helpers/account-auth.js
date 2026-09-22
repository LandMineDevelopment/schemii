import { readFile, stat } from 'node:fs/promises';

/** Existing accounts are only used when the test operator explicitly supplies credentials. */
export async function accountCredentials(env = process.env) {
  if (env.SCHEMII_E2E_USERNAME || env.SCHEMII_E2E_PASSWORD) {
    if (!env.SCHEMII_E2E_USERNAME || !env.SCHEMII_E2E_PASSWORD) {
      throw new Error('Set both SCHEMII_E2E_USERNAME and SCHEMII_E2E_PASSWORD for browser authentication.');
    }
    return { username: env.SCHEMII_E2E_USERNAME, password: env.SCHEMII_E2E_PASSWORD };
  }
  if (!env.SCHEMII_E2E_CREDENTIALS_FILE) return null;
  const path = env.SCHEMII_E2E_CREDENTIALS_FILE;
  const info = await stat(path);
  if (!info.isFile() || (info.mode & 0o077)) throw new Error('The browser credentials file must be private (chmod 600).');
  const document = JSON.parse(await readFile(path, 'utf8'));
  const credentials = document.admin || document;
  if (typeof credentials.username !== 'string' || !credentials.username || typeof credentials.password !== 'string' || !credentials.password) {
    throw new Error('The browser credentials file requires username and password, optionally nested under admin.');
  }
  return { username: credentials.username, password: credentials.password };
}
