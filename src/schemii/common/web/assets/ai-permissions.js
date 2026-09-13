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
  return enabled ? `${enabled} of ${actions.length} actions` : "No actions enabled";
}

export function permissionBundle(action) {
  if (action.group === "Design") {
    const collection = action.id.split(".")[0];
    return collection.charAt(0).toUpperCase() + collection.slice(1);
  }
  return action.group || "Actions";
}

// Selection is only an editing aid. Applying changes affects the draft, never persistence.
export function permissionDraft(actions, modes = {}) {
  const ids = new Set(actions.map(action => action.id));
  const selected = new Set();
  const values = Object.fromEntries([...ids].map(id => [id, actionMode(modes, id)]));
  return {
    values, selected,
    select(groupIds, checked) { for (const id of groupIds) if (ids.has(id)) checked ? selected.add(id) : selected.delete(id); },
    state(groupIds) { const count = groupIds.filter(id => selected.has(id)).length; return { checked: count > 0 && count === groupIds.length, indeterminate: count > 0 && count < groupIds.length }; },
    apply(mode) { if (PERMISSION_MODES.includes(mode)) for (const id of selected) values[id] = mode; },
  };
}

export function renderPermissionBundles(container, actions, modes, { attribute = "data-permission-action" } = {}) {
  const draft = permissionDraft(actions, modes), checks = [], selects = new Map();
  const create = (tag, text, className) => { const node = document.createElement(tag); if (text) node.textContent = text; if (className) node.className = className; return node; };
  const modeSelect = label => {
    const select = create("select"); select.setAttribute("aria-label", label);
    for (const mode of PERMISSION_MODES) { const option = create("option", { disabled: "Disabled", ask: "Ask per batch", automatic: "Automatic" }[mode]); option.value = mode; select.append(option); }
    return select;
  };
  const ids = actions.map(action => action.id);
  let busy = false;
  const apply = create("button", "Apply selected", "ui-button compact"); apply.type = "button";
  const count = create("span", "", "permission-selection-count"); count.setAttribute("role", "status");
  const refresh = () => {
    for (const { input, ids: groupIds } of checks) Object.assign(input, draft.state(groupIds));
    count.textContent = `${draft.selected.size} selected`;
    apply.disabled = busy || draft.selected.size === 0;
  };
  const selection = (label, groupIds) => {
    const wrapper = create("label", "", "permission-selection"); const input = create("input"); input.type = "checkbox"; input.setAttribute("aria-label", label);
    input.addEventListener("change", () => { draft.select(groupIds, input.checked); refresh(); });
    checks.push({ input, ids: groupIds }); wrapper.append(input, create("span", label)); return wrapper;
  };
  container.replaceChildren(); container.classList.add("permission-bundles");
  const bulk = create("div", "", "permission-bulk"); const bulkMode = modeSelect("Permission for selected actions"); bulkMode.value = "ask";
  apply.addEventListener("click", () => { draft.apply(bulkMode.value); for (const [id, select] of selects) select.value = draft.values[id]; count.textContent = `${draft.selected.size} actions updated · Save settings to apply`; });
  bulk.append(selection("Select all actions", ids), count, bulkMode, apply);
  container.append(bulk, create("p", "Select bundles or individual actions, then apply a permission. You can also change each action independently. Changes take effect when you save settings.", "permission-bundle-help"));
  const groups = new Map();
  for (const action of actions) { const name = permissionBundle(action); if (!groups.has(name)) groups.set(name, []); groups.get(name).push(action); }
  for (const [name, members] of groups) {
    const group = create("fieldset", "", "permission-bundle"); const legend = create("legend"); legend.append(selection(`Select ${name}`, members.map(action => action.id))); group.append(legend);
    for (const action of members) {
      const row = create("div", "", `permission-row${action.destructive ? " danger" : ""}`);
      const check = selection(`Select ${action.label}`, [action.id]); check.classList.add("permission-row-check");
      const copy = create("label", "", "permission-copy"); const select = modeSelect(action.label); select.name = action.id; select.setAttribute(attribute, action.id);
      select.value = draft.values[action.id]; select.addEventListener("change", () => { draft.values[action.id] = select.value; }); selects.set(action.id, select);
      const text = create("span"); text.append(create("strong", action.label)); if (action.description) text.append(create("small", action.description)); copy.append(text, select);
      row.append(check, copy); group.append(row);
    }
    container.append(group);
  }
  refresh();
  return { setBusy(value) { busy = value; container.querySelectorAll("input, select, button").forEach(control => { control.disabled = busy; }); refresh(); } };
}

export function bindContextSelection(fieldset) {
  const children = [...fieldset.querySelectorAll("input[name]")];
  const all = fieldset.querySelector("[data-context-select-all]");
  const refresh = () => { const count = children.filter(input => input.checked).length; all.checked = count === children.length; all.indeterminate = count > 0 && count < children.length; };
  all.addEventListener("change", () => { children.forEach(input => { input.checked = all.checked; }); refresh(); });
  children.forEach(input => input.addEventListener("change", refresh));
  return refresh;
}
