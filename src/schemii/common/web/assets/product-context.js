/** Short-lived, same-tab task inputs. Tokens and payloads never grant access. */
export const CONTEXT_LIMITS = Object.freeze({ ttlMs: 5 * 60 * 1000, records: 8, recordBytes: 64 * 1024, totalBytes: 256 * 1024 });
const STORAGE_KEY = 'schemii:product-context:v1';
const destinations = { schema: 'schemoo', preview: 'schemer' };
const tokenPattern = /^ctx_[0-9a-f]{32}$/;
const profilePattern = /^pg_[0-9a-f]{32}$/;
const modelPattern = /^model_[0-9a-f]{32}$/;
const bytes = value => new TextEncoder().encode(JSON.stringify(value)).length;
const fail = message => { throw new Error(message); };
const invalid = () => fail('This saved context is invalid. Return to its source application and send it again.');
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const string = (value, max = 200, empty = false) => typeof value === 'string' && value.length <= max && (empty || value.length > 0);
const keys = (value, allowed) => object(value) && Object.keys(value).every(key => allowed.includes(key));
const list = (value, limit, check) => Array.isArray(value) && value.length <= limit && value.every(check);
const scalar = value => value === null || typeof value === 'boolean' || typeof value === 'number' && Number.isFinite(value) || string(value, 4000, true);
const filterValue = value => scalar(value) || list(value, 500, scalar);
const field = value => keys(value, ['table', 'column', 'aggregate']) && string(value.table) && string(value.column)
  && ['none', 'count', 'count_distinct', 'sum', 'avg', 'min', 'max'].includes(value.aggregate || 'none');
const domain = value => value === null || keys(value, ['namespace', 'nodeId', 'table', 'column', 'labelColumn'])
  && Object.values(value).every(item => item === null || string(item, 200, true));
const condition = value => keys(value, ['domain', 'table', 'column', 'operator', 'parameterId', 'compareColumn', 'value', 'allowNull'])
  && string(value.table, 200, true) && string(value.column, 200, true)
  && ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'range_contains_date', 'in', 'not_in', 'is_null', 'not_null'].includes(value.operator || 'eq')
  && (value.domain === undefined || domain(value.domain))
  && ['parameterId', 'compareColumn'].every(key => value[key] == null || string(value[key]))
  && (value.value === undefined || filterValue(value.value)) && (value.allowNull === undefined || typeof value.allowNull === 'boolean');

export function validatePreviewContext(payload) {
  if (!keys(payload, ['modelId', 'modelRevision', 'explore']) || !modelPattern.test(payload.modelId)
      || !Number.isSafeInteger(payload.modelRevision) || payload.modelRevision < 1) invalid();
  const value = payload.explore;
  if (!keys(value, ['root', 'fields', 'selections', 'reportFilters', 'limit']) || !string(value.root)
      || !list(value.fields, 64, field) || !value.fields.length || !Number.isSafeInteger(value.limit) || value.limit < 1 || value.limit > 100
      || !object(value.selections) || Object.keys(value.selections).length > 20
      || !Object.entries(value.selections).every(([id, selection]) => string(id) && keys(selection, ['active', 'alternativeId', 'values'])
        && (selection.active === undefined || typeof selection.active === 'boolean')
        && (selection.alternativeId == null || string(selection.alternativeId)) && object(selection.values)
        && Object.keys(selection.values).length <= 32 && Object.entries(selection.values).every(([id, value]) => string(id) && filterValue(value)))
      || !list(value.reportFilters, 32, filter => keys(filter, ['id', 'label', 'mode', 'conditions'])
        && (filter.id === undefined || string(filter.id, 200, true)) && (filter.label === undefined || string(filter.label, 200, true))
        && ['rows', 'exists', 'not_exists'].includes(filter.mode || 'rows') && list(filter.conditions, 32, condition))) invalid();
  return JSON.parse(JSON.stringify(payload));
}

function validatePayload(kind, value) {
  if (kind === 'preview') return validatePreviewContext(value);
  if (kind !== 'schema' || !keys(value, ['connectionId', 'connectionRevision', 'namespace'])
      || !profilePattern.test(value.connectionId) || !string(value.namespace, 63)
      || !Number.isSafeInteger(value.connectionRevision) || value.connectionRevision < 1) invalid();
  return JSON.parse(JSON.stringify(value));
}

export function requireContextAccount(account, destination, ownerId = account?.user?.id) {
  if (!string(ownerId) || account?.user?.id !== ownerId) fail('The signed-in account changed. Return to the source application and send the context again.');
  if (!account.capabilities?.includes(`${destination}:access`) || destination === 'schemer' && !account.capabilities.includes('schemer:author')) {
    fail(`Your account cannot create in ${destination === 'schemer' ? 'Schemer' : 'Schemoo'}. Ask an administrator for access, then send a new context.`);
  }
  return ownerId;
}

export function assertProductContext(context, account, destination, now = Date.now()) {
  if (!keys(context, ['version', 'kind', 'destination', 'ownerId', 'createdAt', 'expiresAt', 'payload']) || context.version !== 1 || context.destination !== destination || destinations[context.kind] !== destination
      || !Number.isFinite(context.createdAt) || !Number.isFinite(context.expiresAt) || context.expiresAt - context.createdAt !== CONTEXT_LIMITS.ttlMs) invalid();
  requireContextAccount(account, destination, context.ownerId);
  if (context.createdAt > now || context.expiresAt <= now) fail('This saved context expired. Return to the source application and send it again.');
  validatePayload(context.kind, context.payload);
  return context;
}

function load(storage) {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (raw === null) return {};
    if (new TextEncoder().encode(raw).length > CONTEXT_LIMITS.totalBytes) invalid();
    const records = JSON.parse(raw);
    if (!object(records) || Object.keys(records).length > CONTEXT_LIMITS.records) invalid();
    return records;
  } catch {
    storage.removeItem(STORAGE_KEY);
    fail('Saved task context is unavailable in this tab. Return to the source application and send it again.');
  }
}

export function createProductContext(kind, account, payload, { storage = sessionStorage, now = Date.now(), token = `ctx_${crypto.randomUUID().replaceAll('-', '')}` } = {}) {
  const destination = destinations[kind], ownerId = requireContextAccount(account, destination);
  if (!tokenPattern.test(token)) invalid();
  const context = { version: 1, kind, destination, ownerId, createdAt: now, expiresAt: now + CONTEXT_LIMITS.ttlMs,
    payload: validatePayload(kind, payload) };
  if (bytes(context) > CONTEXT_LIMITS.recordBytes) fail('This preview context is too large to transfer. Reduce its selected fields or filter values before sending it again.');
  const records = Object.fromEntries(Object.entries(load(storage)).filter(([, value]) => value?.ownerId === ownerId && value.expiresAt > now));
  if (records[token]) invalid();
  records[token] = context;
  while (Object.keys(records).length > CONTEXT_LIMITS.records || bytes(records) > CONTEXT_LIMITS.totalBytes) delete records[Object.keys(records)[0]];
  try { storage.setItem(STORAGE_KEY, JSON.stringify(records)); }
  catch { fail('This tab cannot retain task context. Allow session storage, then try again.'); }
  return `/${destination}?handoff=${token}`;
}

export function consumeProductContext(token, account, destination, { storage = sessionStorage, now = Date.now() } = {}) {
  if (!tokenPattern.test(token || '')) invalid();
  const records = load(storage), context = records[token];
  delete records[token];
  try { storage.setItem(STORAGE_KEY, JSON.stringify(records)); }
  catch { fail('This tab cannot consume its task context safely. Allow session storage, then send a new context from its source.'); }
  if (!context) fail('This task context is missing, already used, or belongs to another tab. Return to its source and send it again.');
  assertProductContext(context, account, destination, now);
  return JSON.parse(JSON.stringify(context));
}
