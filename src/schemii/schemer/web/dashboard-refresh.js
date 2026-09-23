// Preserve personal viewer choices only while their filter contract is unchanged.
export function refreshedSelections(previous, previousModel, next, nextModel) {
  const selections = structuredClone(next.selections || {});
  if (!previousModel || previous.modelId !== next.modelId) return selections;
  const previousScopes = new Map(previousModel.definition.scopes.map(scope => [scope.id, scope]));
  const offered = new Set(next.optionalFilters || []);
  for (const scope of nextModel.definition.scopes) {
    const available = scope.kind === 'required' && scope.requirement !== 'optional' || offered.has(scope.id);
    if (available && Object.hasOwn(previous.selections || {}, scope.id)
      && JSON.stringify(previousScopes.get(scope.id)) === JSON.stringify(scope)) {
      selections[scope.id] = structuredClone(previous.selections[scope.id]);
    }
  }
  return selections;
}
export function staleModelMessage(canEdit) {
  return canEdit
    ? 'The source model changed. Update this dashboard after reviewing the model in Schemoo.'
    : 'This report needs an update from its owner. Ask them to update the dashboard, then refresh it here.';
}
