import { getSupportedThinkingLevels } from '@earendil-works/pi-ai';

export const reasoningEfforts = Object.freeze(['default', 'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);

export function reasoningLevels(model) {
  const supported = getSupportedThinkingLevels(model);
  // Some compatible completion providers omit the field for SDK 'off', which
  // can leave provider-default reasoning enabled. Offer off only with an
  // explicit native disable mapping; never equate omission with disabling.
  const explicitOff = !model.reasoning || model.api !== 'openai-completions'
    || typeof model.thinkingLevelMap?.off === 'string'
    || ['deepseek', 'zai', 'qwen', 'together', 'string-thinking'].includes(model.compat?.thinkingFormat);
  return reasoningEfforts.filter(level => level === 'default' || (supported.includes(level) && (level !== 'off' || explicitOff)));
}
