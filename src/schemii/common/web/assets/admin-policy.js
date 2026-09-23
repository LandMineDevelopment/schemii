/** Identity is exact: provider, app, profile owner/id, model and reasoning. */
export const policyIdentity = policy => JSON.stringify([policy.path, policy.product, policy.connectionOwnerId, policy.connectionId, policy.modelId || '', policy.reasoningEffort || '']);
export const scopeIdentity = scope => JSON.stringify([scope.product, scope.connectionOwnerId, scope.connectionId]);
export function policyChanges(persisted, draft) {
  const saved = new Set(persisted.map(policyIdentity)), pending = new Set(draft.map(policyIdentity));
  return { removals: persisted.filter(policy => !pending.has(policyIdentity(policy))), additions: draft.filter(policy => !saved.has(policyIdentity(policy))) };
}
export function policyRequest(policy, kind, id, remove = false) {
  const role = kind === 'role';
  const route = role ? 'role-grants' : remove && policy.path === 'shared-codex' ? 'model-grants' : 'grants';
  return { url: `/api/v1/admin/ai/${policy.path}/${route}`, options: {
    method: remove ? 'DELETE' : !role && policy.path === 'shared-codex' ? 'POST' : 'PUT',
    body: { [role ? 'roleId' : 'userId']: id, product: policy.product, connectionOwnerId: policy.connectionOwnerId, connectionId: policy.connectionId,
      ...(policy.modelId ? { modelId: policy.modelId, reasoningEffort: policy.reasoningEffort } : {}) },
  } };
}
