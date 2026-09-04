import { randomUUID } from 'node:crypto';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';

export class LoginError extends Error {
  constructor(code) { super(code); this.code = code; }
}

const validIdentity = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
export function createLoginService({ oauth = openaiCodexProvider().auth.oauth,
  now = Date.now, ttlMs = 600_000, maxTotal = 32, maxPerOwner = 4 } = {}) {
  const entries = new Map();
  const remove = entry => {
    entries.delete(entry.id);
    clearTimeout(entry.timer);
    entry.controller.abort();
    delete entry.credential;
  };
  const prune = () => {
    for (const entry of entries.values()) if (entry.expiresAt <= now()) remove(entry);
  };
  const snapshot = entry => ({
    id: entry.id, status: entry.status, credentialId: entry.credentialId,
    generation: entry.generation, expiresAt: entry.expiresAt,
    ...(entry.verificationUrl ? { verificationUrl: entry.verificationUrl, userCode: entry.userCode } : {}),
    ...(entry.status === 'succeeded' ? { credential: structuredClone(entry.credential) } : {}),
    ...(entry.status === 'failed' ? { error: 'provider_failed' } : {}),
  });
  const lookup = ({ owner, id } = {}) => {
    prune();
    if (!validIdentity(owner) || !validIdentity(id)) throw new LoginError('invalid_request');
    const entry = entries.get(id);
    if (!entry || entry.owner !== owner) throw new LoginError('not_found');
    return entry;
  };
  return {
    start(input = {}) {
      prune();
      const { owner, credentialId, generation } = input;
      if (Object.keys(input).some(key => !['owner', 'credentialId', 'generation'].includes(key)) ||
        !validIdentity(owner) || !validIdentity(credentialId) ||
        !Number.isSafeInteger(generation) || generation < 0) throw new LoginError('invalid_request');
      if (entries.size >= maxTotal || [...entries.values()].filter(e => e.owner === owner).length >= maxPerOwner) {
        throw new LoginError('busy');
      }
      const entry = { id: randomUUID(), owner, credentialId, generation, status: 'pending',
        expiresAt: now() + ttlMs, controller: new AbortController() };
      entry.timer = setTimeout(() => remove(entry), ttlMs);
      entry.timer.unref?.();
      entries.set(entry.id, entry);
      const active = () => entries.get(entry.id) === entry && !entry.controller.signal.aborted && entry.expiresAt > now();
      Promise.resolve().then(() => {
        if (!active()) throw new LoginError('not_found');
        return oauth.login({
        signal: entry.controller.signal,
        prompt: async prompt => {
          if (!active()) throw new LoginError('not_found');
          if (prompt.type === 'select' && prompt.options?.some(option => option.id === 'device_code')) return 'device_code';
          throw new LoginError('provider_failed');
        },
        notify: event => {
          if (!active()) return;
          if (event.type !== 'device_code' || event.verificationUri !== 'https://auth.openai.com/codex/device' ||
            typeof event.userCode !== 'string' || event.userCode.length > 128 || !event.userCode.length) {
            throw new LoginError('provider_failed');
          }
          entry.verificationUrl = event.verificationUri;
          entry.userCode = event.userCode;
          if (Number.isFinite(event.expiresInSeconds) && event.expiresInSeconds > 0) {
            entry.expiresAt = Math.min(entry.expiresAt, now() + event.expiresInSeconds * 1000);
            clearTimeout(entry.timer);
            entry.timer = setTimeout(() => remove(entry), Math.max(1, entry.expiresAt - now()));
            entry.timer.unref?.();
          }
        },
        });
      }).then(credential => {
        if (!active()) return;
        if (credential?.type !== 'oauth' || typeof credential.access !== 'string' || !credential.access ||
          typeof credential.refresh !== 'string' || !credential.refresh || !Number.isFinite(credential.expires)) {
          throw new LoginError('provider_failed');
        }
        entry.credential = structuredClone(credential);
        entry.status = 'succeeded';
      }).catch(() => {
        if (active()) entry.status = 'failed';
      });
      return snapshot(entry);
    },
    status(input) { return snapshot(lookup(input)); },
    cancel(input) { remove(lookup(input)); return { status: 'cancelled' }; },
    close() { for (const entry of entries.values()) remove(entry); },
  };
}
