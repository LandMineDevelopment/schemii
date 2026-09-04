import { createModels, InMemoryCredentialStore } from '@earendil-works/pi-ai';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';

// Experimental library boundary, not a public HTTP service. Owner/credentialId
// must come from authenticated Schemii code, never from model arguments.
export class CredentialVault {
  #stores = new Map();

  scope(owner, credentialId) {
    if (!owner || !credentialId) throw new Error('An owner and credential identity are required');
    const key = JSON.stringify([owner, credentialId]);
    if (!this.#stores.has(key)) this.#stores.set(key, new InMemoryCredentialStore());
    return this.#stores.get(key);
  }

  async put(owner, credentialId, providerId, credential) {
    await this.scope(owner, credentialId).modify(providerId, async () => structuredClone(credential));
  }

  async remove(owner, credentialId, providerId) {
    await this.scope(owner, credentialId).delete(providerId);
  }
}

export class TurnError extends Error {
  constructor(code) {
    const messages = {
      provider_failed: 'The AI provider request failed. Retry or reconnect your account.',
      credentials_required: 'Connect your own provider account before running this request.',
      model_unavailable: 'The requested provider or model is unavailable.',
      cancelled: 'The AI request was cancelled.',
      timeout: 'The AI request exceeded its time limit.',
      busy: 'AI capacity is in use. Try again when a running request finishes.',
      context_too_large: 'The supplied AI context exceeds the configured limit.',
      response_too_large: 'The AI response exceeds the configured limit.',
      permission_changed: 'Assistant permissions changed. Request a new response.',
      tool_denied: 'The model requested a tool that is not enabled for this turn.',
    };
    super(messages[code]);
    this.name = 'TurnError';
    this.code = code;
  }
}

const providers = {
  openai: openaiProvider,
  'openai-codex': openaiCodexProvider,
};
const noAmbientAuth = Object.freeze({
  env: async () => undefined,
  fileExists: async () => false,
});

export class TurnRunner {
  #active = 0;
  #owners = new Map();

  constructor({ vault, providerFactories = providers, maxConcurrent = 4,
    maxPerOwner = 2, timeoutMs = 30_000, contextBytes = 512 * 1024,
    responseBytes = 256 * 1024 } = {}) {
    this.vault = vault;
    this.providerFactories = providerFactories;
    this.limits = { maxConcurrent, maxPerOwner, timeoutMs, contextBytes, responseBytes };
    for (const value of Object.values(this.limits)) {
      if (!Number.isSafeInteger(value) || value < 1) throw new Error('Limits must be positive integers');
    }
  }

  async run({ owner, credentialId, providerId, modelId, context, signal,
    onText = () => {}, isAuthorized = async () => true }) {
    const ownerActive = this.#owners.get(owner) ?? 0;
    if (this.#active >= this.limits.maxConcurrent || ownerActive >= this.limits.maxPerOwner) {
      throw new TurnError('busy');
    }
    this.#active++;
    this.#owners.set(owner, ownerActive + 1);
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, this.limits.timeoutMs);
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    try {
      controller.signal.throwIfAborted();
      if (!await isAuthorized()) throw new TurnError('permission_changed');
      const snapshot = structuredClone(context);
      if (Buffer.byteLength(JSON.stringify(snapshot)) > this.limits.contextBytes) {
        throw new TurnError('context_too_large');
      }
      const factory = Object.hasOwn(this.providerFactories, providerId) && this.providerFactories[providerId];
      if (!factory) throw new TurnError('model_unavailable');
      const store = this.vault.scope(owner, credentialId);
      // Fail closed before Pi can consider ambient credentials. Each collection
      // and provider instance belongs to this turn; only the scoped store is shared.
      if (!await store.read(providerId)) throw new TurnError('credentials_required');
      const models = createModels({ credentials: store, authContext: noAmbientAuth });
      models.setProvider(factory());
      const model = models.getModel(providerId, modelId);
      if (!model) throw new TurnError('model_unavailable');
      const stream = models.stream(model, snapshot, {
        signal: controller.signal,
        transport: 'sse',
        cacheRetention: 'none',
      });
      let bytes = 0;
      for await (const event of stream) {
        controller.signal.throwIfAborted();
        // Never forward SDK error/thinking/raw events or diagnostic payloads.
        if (event.type === 'text_delta' || event.type === 'toolcall_delta' || event.type === 'thinking_delta') {
          bytes += Buffer.byteLength(event.delta ?? '');
          if (bytes > this.limits.responseBytes) throw new TurnError('response_too_large');
        }
        if (event.type === 'text_delta') onText(event.delta);
      }
      const reply = await stream.result();
      controller.signal.throwIfAborted();
      if (reply.stopReason === 'error' || reply.stopReason === 'aborted') throw new TurnError('provider_failed');
      if (Buffer.byteLength(JSON.stringify(reply)) > this.limits.responseBytes) throw new TurnError('response_too_large');
      // This callback represents the existing Schemii revision/permission check.
      // The real application still validates argument schemas and every apply.
      if (!await isAuthorized()) throw new TurnError('permission_changed');
      const allowed = new Set((snapshot.tools ?? []).map(tool => tool.name));
      const toolCalls = reply.content.filter(part => part.type === 'toolCall');
      if (toolCalls.some(call => !allowed.has(call.name))) throw new TurnError('tool_denied');
      return {
        text: reply.content.filter(part => part.type === 'text').map(part => part.text).join(''),
        toolCalls: toolCalls.map(({ id, name, arguments: args }) => ({ id, name, arguments: args })),
      };
    } catch (error) {
      // Do not attach causes: Pi/provider exceptions can contain OAuth tokens.
      if (timedOut) throw new TurnError('timeout');
      if (signal?.aborted) throw new TurnError('cancelled');
      if (error instanceof TurnError) throw error;
      throw new TurnError('provider_failed');
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      controller.abort();
      this.#active--;
      const remaining = this.#owners.get(owner) - 1;
      if (remaining) this.#owners.set(owner, remaining);
      else this.#owners.delete(owner);
    }
  }
}
