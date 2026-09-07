import { createModels, InMemoryCredentialStore } from '@earendil-works/pi-ai';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';
import { opencodeProvider } from '@earendil-works/pi-ai/providers/opencode';

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

  async read(owner, credentialId, providerId) {
    return this.scope(owner, credentialId).read(providerId);
  }

  clearIdentity(owner, credentialId) {
    this.#stores.delete(JSON.stringify([owner, credentialId]));
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
      invalid_request: 'The AI request is invalid.',
      not_found: 'The AI request was not found.',
      rate_limited: 'The AI provider usage or capacity limit was reached. Wait and try again, or select another available model.',
      billing_required: 'The AI provider requires billing or credits for this request. Check your provider account or select another model.',
    };
    super(messages[code]);
    this.name = 'TurnError';
    this.code = code;
  }
}

export const providers = {
  openai: openaiProvider,
  'openai-codex': openaiCodexProvider,
  opencode: opencodeProvider,
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
    onText = () => {}, isAuthorized = async () => true, limits = {} }) {
    const policy = { ...this.limits, ...limits };
    for (const value of Object.values(policy)) {
      if (!Number.isSafeInteger(value) || value < 1) throw new TurnError('invalid_request');
    }
    const ownerActive = this.#owners.get(owner) ?? 0;
    if (this.#active >= policy.maxConcurrent || ownerActive >= policy.maxPerOwner) {
      throw new TurnError('busy');
    }
    this.#active++;
    this.#owners.set(owner, ownerActive + 1);
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, policy.timeoutMs);
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    try {
      controller.signal.throwIfAborted();
      if (!await isAuthorized()) throw new TurnError('permission_changed');
      const snapshot = structuredClone(context);
      if (Buffer.byteLength(JSON.stringify(snapshot)) > policy.contextBytes) {
        throw new TurnError('context_too_large');
      }
      if (!Array.isArray(snapshot.messages) || !snapshot.messages.length
        || snapshot.messages.some(message => !message || !['user', 'assistant', 'toolResult'].includes(message.role))) {
        throw new TurnError('invalid_request');
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
          if (bytes > policy.responseBytes) throw new TurnError('response_too_large');
        }
        if (event.type === 'text_delta') await onText(event.delta);
      }
      const reply = await stream.result();
      controller.signal.throwIfAborted();
      if (reply.stopReason === 'error' || reply.stopReason === 'aborted') {
        // Only classify a leading HTTP status; never expose provider text, which
        // may contain private diagnostics or credentials.
        const status = typeof reply.errorMessage === 'string'
          ? /^(?:OpenAI API error \()?(401|402|429)(?:\):|\b)/.exec(reply.errorMessage)?.[1]
          : undefined;
        throw new TurnError({ '401': 'credentials_required', '402': 'billing_required',
          '429': 'rate_limited' }[status] ?? 'provider_failed');
      }
      if (Buffer.byteLength(JSON.stringify(reply)) > policy.responseBytes) throw new TurnError('response_too_large');
      // This callback represents the existing Schemii revision/permission check.
      // The real application still validates argument schemas and every apply.
      if (!await isAuthorized()) throw new TurnError('permission_changed');
      const toolCalls = reply.content.filter(part => part.type === 'toolCall');
      // Tool calls are untrusted inference output, never executable sidecar
      // actions. Return even unadvertised names so the server's dispatcher can
      // supply a permission-specific denial as a native tool result.
      if (toolCalls.some(call => typeof call.id !== 'string' || !call.id
        || typeof call.name !== 'string' || !call.name.trim()
        || !call.arguments || typeof call.arguments !== 'object' || Array.isArray(call.arguments))
        || new Set(toolCalls.map(call => call.id)).size !== toolCalls.length) {
        throw new TurnError('provider_failed');
      }
      return {
        text: reply.content.filter(part => part.type === 'text').map(part => part.text).join(''),
        toolCalls: toolCalls.map(({ id, name, arguments: args }) => ({ id, name, arguments: args })),
        // Keep provider signatures and message metadata for the next inference
        // step. This private payload is transient, never a public UI event.
        assistantMessage: reply,
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
