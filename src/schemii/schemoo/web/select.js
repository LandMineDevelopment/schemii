import { createSearchableSelect } from "/assets/common/searchable-select.js";
import { createAsyncValueSelect } from "/assets/common/async-value-select.js";

const controls = new WeakMap();

export function domainSelect(options) {
  const control = createAsyncValueSelect(options);
  control.root.dataset.modelValueSelect = "";
  controls.set(control.root, control);
  return control.root;
}

// Model values may be JSON field references or empty-string literal choices.
// Keep them out of the visible input while reusing the shared menu and keyboard behavior.
export function modelSelect(label, choices, value, onChange) {
  const selected = choices.findIndex(([key]) => key === value);
  const control = createSearchableSelect({
    label, displayLabel: true, placeholder: `Search ${label.toLowerCase()}`,
    options: choices.map(([key, text], index) => ({ value: `choice_${index}`, label: text })),
    value: selected < 0 ? "" : `choice_${selected}`,
  });
  control.input.addEventListener("change", () => {
    const choice = choices[Number(control.getValue().slice("choice_".length))];
    if (control.getValue() !== "" && choice) onChange(choice[0]);
  });
  controls.set(control.root, control);
  return control.root;
}

// Menus are portaled outside their editor. Destroy them before replacing an editor.
export function disposeSelects(host) {
  for (const root of [host, ...host.querySelectorAll(".ui-searchable-select, [data-model-value-select]")]) {
    controls.get(root)?.destroy();
    controls.delete(root);
  }
}
