/** Require a usable model, not merely a successful AI status response. */
export function assertProviderAvailable(status, policy) {
  if (!policy || typeof policy.providerId !== 'string' || typeof policy.modelId !== 'string'
      || !policy.providerId || !policy.modelId) {
    throw new Error('Chat prerequisite needs a providerId and modelId.');
  }
  const provider = status?.providers?.find(item => item.id === policy.providerId);
  const model = provider?.models?.find(item => item.id === policy.modelId);
  if (!provider?.authenticated || !provider.available || model?.status !== 'active'
      || (policy.reasoningEffort && !model.reasoningLevels?.includes(policy.reasoningEffort))) {
    throw new Error(`Chat prerequisite unavailable: ${policy.providerId}/${policy.modelId}. Connect and grant the model before preparing this lane.`);
  }
}
