import { element } from './dom.js';

const labels = { default: 'Model default', off: 'Off', minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Maximum' };
export const reasoningLabel = level => labels[level] || level;
export function reasoningLevels(model) {
  return ['default', ...Object.keys(labels).filter(level => level !== 'default' && model?.reasoningLevels?.includes(level))];
}
export function reasoningForModel(model, value = 'default') {
  return reasoningLevels(model).includes(value) ? value : 'default';
}
export function populateReasoningOptions(select, model, value = 'default', busy = false) {
  const levels = reasoningLevels(model);
  select.replaceChildren(...levels.map(level => element('option', { text: labels[level], attrs: { value: level } })));
  if (!levels.includes(value)) select.prepend(element('option', { text: `${labels[value] || value} · unavailable`, attrs: { value, disabled: '' } }));
  select.value = value;
  select.disabled = busy || (levels.length === 1 && value === 'default');
  select.title = levels.length > 1 ? 'Higher reasoning levels can take longer. Applies to your next message.' : 'This model does not advertise adjustable reasoning levels.';
}

export function managedProviderForModel(status, model) {
  return status?.providers?.find(provider => provider.id === model?.providerId && provider.adminManaged);
}

export function reasoningForSelection(status, model, value = 'default') {
  const policy = managedProviderForModel(status, model);
  if (!policy) return reasoningForModel(model, value);
  const allowed = Object.keys(labels).filter(level => model?.reasoningLevels?.includes(level));
  if (policy.selectedReasoningEffort && (!policy.selectedModelId || policy.selectedModelId === model?.id)
      && allowed.includes(policy.selectedReasoningEffort)) return policy.selectedReasoningEffort;
  return allowed.includes(value) ? value : (allowed[0] || 'default');
}

export function populateScopedReasoningOptions(select, status, model, value = 'default', busy = false) {
  const policy = managedProviderForModel(status, model);
  if (!policy) return populateReasoningOptions(select, model, value, busy);
  const levels = Object.keys(labels).filter(level => model?.reasoningLevels?.includes(level));
  select.replaceChildren(...levels.map(level => element('option', { text: labels[level], attrs: { value: level } })));
  select.value = reasoningForSelection(status, model, value);
  select.disabled = busy || levels.length <= 1;
  select.title = levels.length > 1
    ? 'Choose an administrator-approved reasoning level.'
    : 'An administrator manages this model’s reasoning level.';
}
