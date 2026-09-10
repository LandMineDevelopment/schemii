export const PERMISSION_MODES = Object.freeze(["disabled", "ask", "automatic"]);

export function permissionMode(capabilities, actionId) {
  return actionMode(capabilities?.actionModes, actionId);
}

export function actionMode(modes, actionId) {
  const mode = modes?.[actionId];
  return PERMISSION_MODES.includes(mode) ? mode : "disabled";
}

// Only server-advertised actions can be saved. Missing controls fail closed.
export function permissionValues(modes, actions) {
  return { actionModes: Object.fromEntries(actions.map(({ id }) => [
    id, PERMISSION_MODES.includes(modes[id]) ? modes[id] : "disabled",
  ])) };
}

export function permissionSummary(capabilities, actions) {
  const enabled = actions.filter(({ id }) => permissionMode(capabilities, id) !== "disabled").length;
  return enabled ? `${enabled} of ${actions.length} actions` : "Explain only";
}
