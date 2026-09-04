import { CredentialVault, TurnRunner, TurnError, providers } from './runtime.js';

const identity = (owner, id) => JSON.stringify([owner, id]);
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 128;
const supported = ['openai-codex', 'openai', 'opencode'];
const maxima = { maxConcurrent: 1000, maxPerOwner: 1000, timeoutMs: 3600000,
  contextBytes: 8 * 1024 * 1024, responseBytes: 8 * 1024 * 1024 };

export function supportedModels() {
  return { models: supported.flatMap(providerId => providers[providerId]().getModels().map(model => ({
    providerId, id: model.id, name: model.name, contextWindow: model.contextWindow,
    maxOutputTokens: model.maxTokens,
  }))) };
}

export class TurnService {
  #turns = new Map();
  #identities = new Map();

  constructor({ vault = new CredentialVault(), runner, providerIds = supported } = {}) {
    this.vault = vault;
    this.runner = runner ?? new TurnRunner({ vault });
    this.providerIds = providerIds;
  }

  async run(input, { signal, emit }) {
    const { owner, turnId, credentialId, providerId, modelId, credential, generation, limits = {} } = input;
    if (![owner, turnId, credentialId, modelId].every(validId) || !this.providerIds.includes(providerId)
      || !credential || typeof credential !== 'object' || Array.isArray(credential)
      || !Number.isSafeInteger(generation) || generation < 1
      || !input.context || typeof input.context !== 'object' || Array.isArray(input.context)
      || !Array.isArray(input.context.messages) || !Array.isArray(input.context.tools ?? [])
      || !limits || typeof limits !== 'object' || Array.isArray(limits)
      || Object.entries(limits).some(([key, value]) => !Object.hasOwn(maxima, key)
        || !Number.isSafeInteger(value) || value < 1 || value > maxima[key])
      || Buffer.byteLength(JSON.stringify(credential)) > 64 * 1024) {
      throw new TurnError('invalid_request');
    }
    const key = identity(owner, turnId);
    const credentialKey = identity(owner, credentialId);
    if (this.#turns.has(key) || this.#identities.has(credentialKey)) throw new TurnError('busy');
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    this.#turns.set(key, controller);
    this.#identities.set(credentialKey, controller);
    let terminal;
    try {
      await this.vault.put(owner, credentialId, providerId, credential);
      const result = await this.runner.run({ ...input, signal: controller.signal,
        onText: text => emit({ type: 'text', text }) });
      terminal = { type: 'result', ...result };
    } catch (error) {
      const safe = error instanceof TurnError ? error : new TurnError('provider_failed');
      terminal = { type: 'error', code: safe.code, message: safe.message };
    } finally {
      // OAuth refresh can occur before a failed or cancelled inference. Persist its
      // replacement through the private terminal event, then forget the entire scope.
      const updated = await this.vault.read(owner, credentialId, providerId);
      this.vault.clearIdentity(owner, credentialId);
      this.#turns.delete(key);
      this.#identities.delete(credentialKey);
      signal?.removeEventListener('abort', abort);
      controller.abort();
      if (terminal) await emit({ ...terminal, generation, credential: updated });
    }
  }

  cancel({ owner, turnId }) {
    if (!validId(owner) || !validId(turnId)) throw new TurnError('invalid_request');
    const controller = this.#turns.get(identity(owner, turnId));
    if (!controller) throw new TurnError('not_found');
    controller.abort();
    return { status: 'cancelled' };
  }

  remove({ owner, credentialId }) {
    if (!validId(owner) || !validId(credentialId)) throw new TurnError('invalid_request');
    this.#identities.get(identity(owner, credentialId))?.abort();
    return { status: 'cancelled' };
  }
}
