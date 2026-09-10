import { createModels } from '@earendil-works/pi-ai';
import { providers, TurnError } from './runtime.js';

// Codex's ModelsClient uses GET /models?client_version=… and models[].slug.
// Protocol reference: openai/codex codex-rs/codex-api/src/endpoint/models.rs.
// This compatibility version matches the locally verified Codex catalog format.
const endpoints = {
  'openai-codex': 'https://chatgpt.com/backend-api/codex/models?client_version=0.153.4',
  openai: 'https://api.openai.com/v1/models',
};
const noAmbientAuth = Object.freeze({ env: async () => undefined, fileExists: async () => false });
const maximumBytes = 4 * 1024 * 1024;

async function boundedJson(response, signal) {
  if (!response.body) throw new TurnError('provider_failed');
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximumBytes) throw new TurnError('response_too_large');
      chunks.push(Buffer.from(value));
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function accountId(access) {
  try {
    const value = JSON.parse(Buffer.from(access.split('.')[1], 'base64url').toString('utf8'));
    const id = value['https://api.openai.com/auth']?.chatgpt_account_id;
    if (typeof id === 'string' && id.length > 0 && id.length <= 256) return id;
  } catch { /* Invalid credentials are reported without token contents. */ }
  throw new TurnError('credentials_required');
}

export async function discoverModels({ vault, owner, credentialId, providerId, signal,
  fetcher = globalThis.fetch, timeoutMs = 8000 }) {
  if (!Object.hasOwn(endpoints, providerId)) throw new TurnError('invalid_request');
  const deadline = AbortSignal.timeout(timeoutMs);
  const abort = signal ? AbortSignal.any([signal, deadline]) : deadline;
  try {
    abort.throwIfAborted();
    const store = vault.scope(owner, credentialId);
    const credential = await store.read(providerId);
    if (!credential || credential.type !== (providerId === 'openai' ? 'api_key' : 'oauth')) {
      throw new TurnError('credentials_required');
    }
    const models = createModels({ credentials: store, authContext: noAmbientAuth });
    models.setProvider(providers[providerId]());
    const resolved = await models.getAuth(providerId, { signal: abort });
    const key = resolved?.auth?.apiKey;
    if (typeof key !== 'string' || !key) throw new TurnError('credentials_required');
    const headers = { Authorization: `Bearer ${key}`, Accept: 'application/json' };
    if (providerId === 'openai-codex') {
      headers['ChatGPT-Account-Id'] = accountId(key);
      headers.originator = 'pi';
    }
    const response = await fetcher(endpoints[providerId], {
      method: 'GET', headers, signal: abort, redirect: 'error',
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new TurnError({ 401: 'credentials_required', 403: 'credentials_required',
        402: 'billing_required', 429: 'rate_limited' }[response.status] ?? 'provider_failed');
    }
    const body = await boundedJson(response, abort);
    abort.throwIfAborted();
    const entries = providerId === 'openai-codex' ? body?.models : body?.data;
    const idKey = providerId === 'openai-codex' ? 'slug' : 'id';
    if (!Array.isArray(entries) || entries.length > 10000 || entries.some(entry =>
      !entry || typeof entry !== 'object' || typeof entry[idKey] !== 'string'
      || !entry[idKey] || entry[idKey].length > 256)) throw new TurnError('provider_failed');
    const ids = new Set(entries.filter(entry => providerId !== 'openai-codex'
      || entry.visibility === 'list').map(entry => entry[idKey]));
    // Never return provider instructions, arbitrary display text, URLs or unknown
    // models. Inference capability is owned by the installed SDK registry.
    return models.getModels(providerId).filter(model => ids.has(model.id)).map(model => ({
      providerId, id: model.id, name: model.name, contextWindow: model.contextWindow,
      maxOutputTokens: model.maxTokens,
    }));
  } catch (error) {
    if (signal?.aborted) throw new TurnError('cancelled');
    if (deadline.aborted) throw new TurnError('timeout');
    if (error instanceof TurnError) throw error;
    throw new TurnError('provider_failed');
  }
}
