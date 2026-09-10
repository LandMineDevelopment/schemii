import { createSearchableSelect } from "./searchable-select.js";

/** Keep model persistence on the original select; refresh its popup on each opening. */
export function enhanceModelPicker(select, { refresh, label = select.getAttribute("aria-label") || "AI model" }) {
  const picker = createSearchableSelect({
    label, displayLabel: true, placeholder: "Choose a model", required: select.required,
    noResultsText: "No available models. Connect a provider in Settings.",
    onOpen: async () => {
      try { await refresh(); }
      catch (error) { throw new Error(`${error.message || "Could not check available models."} Showing the last available model list.`); }
      finally { sync(); }
    },
  });
  picker.root.classList.add("ai-model-picker");
  select.before(picker.root);
  select.hidden = true;
  select.required = false;
  select.setAttribute("aria-hidden", "true");
  select.tabIndex = -1;
  picker.input.addEventListener("change", () => {
    select.value = picker.getValue();
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  function sync() {
    picker.setOptions([...select.options].filter(option => option.value).map(option => ({
      value: option.value, label: option.textContent, group: "Models", disabled: option.disabled,
    })));
    picker.setValue(select.value);
    picker.setDisabled(select.disabled);
  }
  const observer = new MutationObserver(sync);
  observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
  select.addEventListener("change", sync);
  sync();
  return Object.freeze({ ...picker, sync, destroy() { observer.disconnect(); picker.destroy(); } });
}
