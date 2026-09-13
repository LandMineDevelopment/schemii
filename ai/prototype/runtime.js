import { reasoningLevels } from './reasoning.js';
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
  constructor(code, limit) {
    const messages = {
      provider_failed: 'The AI provider request failed. Retry or reconnect your account.',
      provider_request_rejected: 'The provider rejected the AI request format. Reconnecting will not fix a request-format error.',
      credentials_required: 'Connect your own provider account before running this request.',
      reasoning_unsupported: 'The selected model does not support this reasoning effort.',
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
    this.limit = limit;
  }
}

// Provider messages may contain private input. Retain only known categories,
// never their text, parameter values, response bodies or credentials.
export function rejectionCategory(message) {
  if (typeof message !== 'string') return 'unknown';
  if (/verbosity/i.test(message) && /support|invalid/i.test(message)) return 'verbosity';
  if (/schema|function.*parameters|tools\[/i.test(message)) return 'tool_schema';
  if (/model/i.test(message) && /not supported|not found|does not exist|do not have access/i.test(message)) return 'model_unavailable';
  if (/reasoning/i.test(message) && /support|invalid/i.test(message)) return 'reasoning';
  if (/call_id|function_call_output|tool.*call/i.test(message)) return 'tool_history';
  return 'unknown';
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

function enforceSize(value, maximum, name, source) {
  const observed = typeof value === 'number' ? value : Buffer.byteLength(JSON.stringify(value));
  if (observed > maximum) throw new TurnError(name === 'context_bytes' ? 'context_too_large' : 'response_too_large',
    { name, configured: maximum, observed, source });
}

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
    onText = () => {}, isAuthorized = async () => true, limits = {}, reasoningEffort = 'default' }) {
    const policy = { ...this.limits, ...limits };
    for (const value of Object.values(policy)) {
      if (!Number.isSafeInteger(value) || value < 1) throw new TurnError('invalid_request');
    }
    const ownerActive = this.#owners.get(owner) ?? 0;
    if (this.#active >= policy.maxConcurrent) {
      throw new TurnError('busy', { name: 'maximum_concurrent_turns',
        configured: policy.maxConcurrent, observed: this.#active });
    }
    if (ownerActive >= policy.maxPerOwner) {
      throw new TurnError('busy', { name: 'maximum_concurrent_turns_per_user',
        configured: policy.maxPerOwner, observed: ownerActive });
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
      enforceSize(snapshot, policy.contextBytes, 'context_bytes', 'request_context');
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
      if (!reasoningLevels(model).includes(reasoningEffort)) throw new TurnError('reasoning_unsupported');
      let providerStatus;
      const codexOff = reasoningEffort === 'off' && model.api === 'openai-codex-responses';
      const stream = (reasoningEffort === 'default' || codexOff ? models.stream.bind(models) : models.streamSimple.bind(models))(model, snapshot, {
        ...(codexOff ? { reasoningEffort: 'none' } : reasoningEffort === 'default' ? {} : { reasoning: reasoningEffort === 'off' ? undefined : reasoningEffort }),
        signal: controller.signal,
        transport: 'sse',
        cacheRetention: 'none',
        // Codex's SDK turns HTTP failures into friendly text and drops the
        // status from errorMessage. Capture only the numeric status at the
        // transport boundary; never log headers, bodies, tokens or chat text.
        onResponse: response => { providerStatus = response.status; },
      });
      let bytes = 0;
      let thinkingBytes = 0;
      for await (const event of stream) {
        controller.signal.throwIfAborted();
        // Never forward SDK error/thinking/raw events or diagnostic payloads.
        if (event.type === 'text_delta' || event.type === 'toolcall_delta') {
          bytes += Buffer.byteLength(event.delta ?? '');
          enforceSize(bytes, policy.responseBytes, 'response_bytes', 'response_stream');
        }
        // Reasoning and encrypted replay signatures are private provider context,
        // not the user's answer/tool arguments. Keep them bounded without charging
        // them against the response budget or discarding required signatures.
        if (event.type === 'thinking_delta') {
          thinkingBytes += Buffer.byteLength(event.delta ?? '');
          enforceSize(thinkingBytes, policy.contextBytes, 'context_bytes', 'native_response');
        }
        if (event.type.endsWith('_end') && event.partial) {
          enforceSize(event.partial, policy.contextBytes, 'context_bytes', 'native_response');
        }
        if (event.type === 'text_delta') await onText(event.delta);
      }
      const reply = await stream.result();
      controller.signal.throwIfAborted();
      if (reply.stopReason === 'error' || reply.stopReason === 'aborted') {
        // Only classify a leading HTTP status; never expose provider text, which
        // may contain private diagnostics or credentials.
        const status = providerStatus ?? (typeof reply.errorMessage === 'string'
          ? /^(?:OpenAI API error \()?(401|402|429)(?:\):|\b)/.exec(reply.errorMessage)?.[1]
          : undefined);
        const category = Number(status) === 400 ? rejectionCategory(reply.errorMessage) : undefined;
        console.warn(JSON.stringify({ event: 'ai_provider_failure', provider: providerId,
          httpStatus: status === undefined ? null : Number(status), ...(category ? { category } : {}) }));
        throw new TurnError({ '401': 'credentials_required', '402': 'billing_required',
          '400': category === 'model_unavailable' ? 'model_unavailable' : 'provider_request_rejected',
          '429': 'rate_limited' }[status] ?? 'provider_failed');
      }
      enforceSize(reply, policy.contextBytes, 'context_bytes', 'native_response');
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
      const text = reply.content.filter(part => part.type === 'text').map(part => part.text).join('');
      const calls = toolCalls.map(({ id, name, arguments: args }) => ({ id, name, arguments: args }));
      enforceSize(Buffer.byteLength(text) + Buffer.byteLength(JSON.stringify(calls)),
        policy.responseBytes, 'response_bytes', 'response_content');
      return {
        text,
        toolCalls: calls,
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
