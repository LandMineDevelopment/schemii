const empty = value => value == null || value === '' || Array.isArray(value) && !value.length;

// Summarize saved selections, using the same defaults and activation rules as execution.
export function appliedFilterSummary(scopes, selections) {
  return scopes.flatMap(scope => {
    const selection = selections[scope.id];
    if (scope.requirement === 'optional' && selection?.active !== true) return [];
    const alternative = scope.alternatives.find(item => item.id === selection?.alternativeId) || scope.alternatives[0];
    if (!alternative?.conditions.length) return [];
    const inputs = alternative.inputs.filter(input => alternative.conditions.some(condition => condition.parameterId === input.id));
    const values = inputs.map(input => {
      const entered = selection?.values?.[input.id];
      const value = empty(entered) ? input.defaultValue : entered;
      const text = empty(value) ? 'Not set' : Array.isArray(value) ? value.join(', ') : String(value);
      return inputs.length > 1 ? `${input.label}: ${text}` : text;
    });
    return [`${scope.label || scope.id}: ${values.length ? values.join(' · ') : alternative.label || 'Fixed rule'}`];
  });
}
