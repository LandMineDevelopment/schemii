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

/** Apply the fixture's explicit viewport contract before handing it to a tester. */
export function expandFixtureScenario(scenario, product, viewportName) {
  const contract = scenario.viewportContracts?.[viewportName];
  if (scenario.viewportContracts && (!contract || !['write', 'readback'].includes(contract.mode)
      || typeof contract.instructions !== 'string' || !contract.instructions.trim())) {
    throw new Error(`Scenario ${scenario.id} lacks a valid ${viewportName} contract.`);
  }
  const id = `${scenario.id}-${product}-${viewportName}`;
  return {
    ...scenario, id, product, viewportName,
    title: `${scenario.title || scenario.id} · ${product} · ${viewportName}${contract?.mode === 'readback' ? ' · saved-state readback' : ''}`,
    instructions: contract?.instructions || scenario.instructions,
    readbackOnly: contract?.mode === 'readback',
    ...(contract?.dependsOnDesktop ? {dependsOn: `${scenario.id}-${product}-desktop`} : {}),
    viewport: viewportName === 'mobile' ? {width:390,height:844} : {width:1280,height:800},
    functional:'not-run', visual:'not-run', evidence:[],
  };
}

export function assertScenarioPrerequisite(lane, scenario) {
  if (!scenario.dependsOn) return;
  const dependency = lane.scenarios.find(item => item.id === scenario.dependsOn);
  if (dependency?.functional !== 'passed') {
    throw new Error(`Prerequisite ${scenario.dependsOn} must pass before saved-state readback; this is not independent creation coverage.`);
  }
}
