import { searchableSelectPlacement } from "./searchable-select.js";

let sequence = 0;

// Domain values are data, not identifiers: preserve case, whitespace and type.
export function uniqueValueOptions(options) {
  const seen = new Set();
  return (options || []).filter(option => {
    if (!option || option.value == null || !["string", "number", "boolean"].includes(typeof option.value)) return false;
    if (seen.has(option.value)) return false;
    seen.add(option.value);
    return true;
  }).map(option => ({ ...option, label: String(option.label ?? option.value) }));
}

export function createAsyncValueSelect({
  label = "Value", value = null, multiple = false, loadOptions, onChange,
  placeholder = "Search values", documentRef = document,
} = {}) {
  const doc = documentRef, win = doc.defaultView;
  const id = `ui-async-value-select-${++sequence}`;
  let selected = new Set(multiple ? (Array.isArray(value) ? value : value == null ? [] : [value]) : value == null ? [] : [value]);
  const labels = new Map();
  let options = [], active = -1, open = false, destroyed = false, request = 0, timer;
  const root = doc.createElement("div");
  root.className = "ui-async-value-select";
  const chips = doc.createElement("div");
  chips.className = "ui-async-value-select__chips";
  const control = doc.createElement("div");
  control.className = "ui-searchable-select";
  const input = doc.createElement("input");
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = placeholder;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-label", label);
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", id);
  input.setAttribute("aria-expanded", "false");
  const toggle = doc.createElement("button");
  toggle.type = "button";
  toggle.className = "ui-searchable-select__toggle";
  toggle.textContent = "⌄";
  toggle.setAttribute("aria-label", `Show ${label.toLocaleLowerCase()} options`);
  toggle.setAttribute("aria-controls", id);
  const list = doc.createElement("div");
  list.id = id;
  list.className = "ui-searchable-select__list ui-async-value-select__list";
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", `${label} options`);
  if (multiple) list.setAttribute("aria-multiselectable", "true");
  list.setAttribute("popover", "manual");
  list.hidden = true;
  control.append(input, toggle);
  root.append(chips, control);

  const display = item => labels.get(item) ?? String(item);
  const restoreInput = () => { input.value = multiple ? "" : selected.size ? display([...selected][0]) : ""; };
  function renderChips() {
    chips.replaceChildren();
    chips.hidden = !multiple || !selected.size;
    if (!multiple) return;
    for (const item of selected) {
      const chip = doc.createElement("button");
      chip.type = "button";
      chip.className = "ui-async-value-select__chip";
      chip.textContent = `${display(item)} ×`;
      chip.setAttribute("aria-label", `Remove ${display(item)}`);
      chip.addEventListener("click", () => {
        if (destroyed) return;
        selected.delete(item);
        renderChips();
        if (open) renderOptions();
        onChange?.([...selected]);
      });
      chips.append(chip);
    }
  }
  function position() {
    if (!open) return;
    const v = win.visualViewport;
    const placement = searchableSelectPlacement(control.getBoundingClientRect(), {
      top: v?.offsetTop ?? 0, left: v?.offsetLeft ?? 0,
      width: v?.width ?? win.innerWidth, height: v?.height ?? win.innerHeight,
    }, list.scrollHeight);
    for (const key of ["width", "maxHeight", "left", "top"]) list.style[key] = `${placement[key]}px`;
  }
  function updateActive(index) {
    active = options.length ? Math.max(0, Math.min(index, options.length - 1)) : -1;
    [...list.querySelectorAll("[role='option']")].forEach((node, i) => {
      node.classList.toggle("active", i === active);
      if (i === active) {
        input.setAttribute("aria-activedescendant", node.id);
        node.scrollIntoView({ block: "nearest" });
      }
    });
    if (active < 0) input.removeAttribute("aria-activedescendant");
  }
  function commit(option) {
    if (destroyed || !option) return;
    if (multiple) {
      if (selected.has(option.value)) selected.delete(option.value);
      else selected.add(option.value);
      renderChips();
      renderOptions();
      onChange?.([...selected]);
    } else {
      selected = new Set([option.value]);
      close();
      onChange?.(option.value);
    }
  }
  function renderOptions() {
    list.replaceChildren();
    for (const [index, option] of options.entries()) {
      const button = doc.createElement("button");
      button.type = "button";
      button.tabIndex = -1;
      button.id = `${id}-option-${index}`;
      button.className = "ui-searchable-select__option";
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(selected.has(option.value)));
      const copy = doc.createElement("span"), title = doc.createElement("strong");
      title.textContent = option.label;
      copy.append(title);
      if (option.description) {
        const description = doc.createElement("small");
        description.textContent = option.description;
        copy.append(description);
      }
      button.append(copy);
      if (selected.has(option.value)) {
        const mark = doc.createElement("b"); mark.textContent = "✓"; mark.setAttribute("aria-hidden", "true"); button.append(mark);
      }
      button.addEventListener("pointerdown", event => event.preventDefault());
      button.addEventListener("click", () => commit(option));
      list.append(button);
    }
    if (!options.length) status("No matching values. Try another search.");
    updateActive(active < 0 ? 0 : active);
    position();
  }
  function status(message) {
    list.replaceChildren();
    const text = doc.createElement("p");
    text.className = "ui-searchable-select__empty";
    text.setAttribute("role", "status");
    text.textContent = message;
    list.append(text);
    position();
  }
  async function load(search) {
    const token = ++request;
    options = []; active = -1; input.removeAttribute("aria-activedescendant");
    list.setAttribute("aria-busy", "true");
    status("Loading values…");
    try {
      const result = await loadOptions(search);
      if (destroyed || !open || token !== request) return;
      options = uniqueValueOptions(result);
      options.forEach(option => labels.set(option.value, option.label));
      renderChips(); renderOptions();
    } catch (error) {
      if (!destroyed && open && token === request) status(error?.message || "Could not load values. Search again to retry.");
    } finally {
      if (token === request) list.setAttribute("aria-busy", "false");
    }
  }
  const outside = event => { if (!root.contains(event.target) && !list.contains(event.target)) close(); };
  function listen(add) {
    const method = add ? "addEventListener" : "removeEventListener";
    doc[method]("pointerdown", outside, true);
    doc[method]("scroll", position, true);
    win[method]("resize", position);
    win.visualViewport?.[method]("resize", position);
    win.visualViewport?.[method]("scroll", position);
  }
  function close() {
    clearTimeout(timer); request += 1;
    if (open) {
      open = false; listen(false);
      if (typeof list.hidePopover === "function" && list.matches(":popover-open")) list.hidePopover();
    }
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    restoreInput();
  }
  function show() {
    if (destroyed || open) return;
    (root.closest("dialog[open]") || doc.body).append(list);
    open = true;
    input.value = "";
    input.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-expanded", "true");
    list.hidden = false;
    if (typeof list.showPopover === "function") list.showPopover();
    listen(true); load(""); position();
  }
  // Focus alone deliberately does not reopen after selecting a value.
  input.addEventListener("click", show);
  input.addEventListener("input", event => {
    event.stopPropagation();
    if (destroyed) return;
    const query = input.value;
    if (!open) show();
    input.value = query;
    clearTimeout(timer); request += 1;
    options = []; active = -1; input.removeAttribute("aria-activedescendant");
    status("Loading values…");
    timer = setTimeout(() => load(query), 200);
  });
  input.addEventListener("change", event => event.stopPropagation());
  input.addEventListener("keydown", event => {
    if (["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) {
      event.preventDefault();
      if (!open) show();
      else if (event.key === "Enter") commit(options[active]);
      else updateActive(active + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Escape" && open) {
      event.preventDefault(); event.stopPropagation(); close();
    } else if (event.key === "Tab") close();
  });
  toggle.addEventListener("click", () => { input.focus(); if (open) close(); else show(); });
  restoreInput(); renderChips();
  return { root, destroy() { if (destroyed) return; destroyed = true; close(); list.remove(); } };
}
