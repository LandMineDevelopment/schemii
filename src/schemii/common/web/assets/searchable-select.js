let searchableSelectSequence = 0;

function normalized(value) {
  return String(value ?? "").trim().toLocaleLowerCase();
}

export function normalizeSearchableSelectOptions(options) {
  const seen = new Set();
  const normalizedOptions = [];
  for (const source of options || []) {
    const option = typeof source === "string" ? { value: source, label: source } : source;
    const value = String(option?.value ?? "").trim();
    if (!value || seen.has(normalized(value))) continue;
    seen.add(normalized(value));
    normalizedOptions.push(Object.freeze({
      value,
      label: String(option.label ?? value),
      group: String(option.group ?? "Options"),
      description: option.description ? String(option.description) : "",
      keywords: String(option.keywords ?? ""),
    }));
  }
  return normalizedOptions;
}

export function filterSearchableSelectOptions(options, query) {
  const needle = normalized(query);
  if (!needle) return [...options];
  return options.filter(option => normalized([
    option.value,
    option.label,
    option.group,
    option.description,
    option.keywords,
  ].join(" ")).includes(needle));
}

export function matchingSearchableSelectOption(options, value) {
  const target = normalized(value);
  return options.find(option => normalized(option.value) === target) ?? null;
}

function assignDataset(node, dataset) {
  for (const [name, value] of Object.entries(dataset || {})) node.dataset[name] = String(value);
}

export function createSearchableSelect({
  options,
  value = "",
  label = "Select an option",
  placeholder = "Search options",
  required = false,
  dataset = {},
  noResultsText = "No matching options",
  documentRef = document,
} = {}) {
  searchableSelectSequence += 1;
  const listId = `ui-searchable-select-${searchableSelectSequence}`;
  let available = normalizeSearchableSelectOptions(options);
  let committed = matchingSearchableSelectOption(available, value)?.value ?? "";
  let filtered = [];
  let activeIndex = -1;
  let open = false;

  const root = documentRef.createElement("div");
  root.className = "ui-searchable-select";
  const input = documentRef.createElement("input");
  input.type = "text";
  input.value = committed;
  input.placeholder = placeholder;
  input.autocomplete = "off";
  input.spellcheck = false;
  input.required = required;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-label", label);
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", listId);
  assignDataset(input, dataset);

  const toggle = documentRef.createElement("button");
  toggle.type = "button";
  toggle.className = "ui-searchable-select__toggle";
  toggle.setAttribute("aria-label", `Show ${label.toLocaleLowerCase()} options`);
  toggle.setAttribute("aria-controls", listId);
  toggle.textContent = "⌄";

  const list = documentRef.createElement("div");
  list.id = listId;
  list.className = "ui-searchable-select__list";
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", `${label} options`);
  list.setAttribute("popover", "manual");
  list.hidden = true;
  documentRef.body.append(list);
  root.append(input, toggle);

  const setValidity = () => {
    const exact = matchingSearchableSelectOption(available, input.value);
    input.setCustomValidity(exact ? "" : `Choose ${label.toLocaleLowerCase()} from the list.`);
    return exact;
  };

  const position = () => {
    if (!open) return;
    const rect = root.getBoundingClientRect();
    const viewport = documentRef.defaultView;
    const visualViewport = viewport.visualViewport;
    const viewportTop = visualViewport?.offsetTop ?? 0;
    const viewportLeft = visualViewport?.offsetLeft ?? 0;
    const viewportWidth = visualViewport?.width ?? viewport.innerWidth;
    const viewportHeight = visualViewport?.height ?? viewport.innerHeight;
    const margin = 8;
    const gap = 5;
    const width = Math.min(Math.max(rect.width, 280), viewportWidth - margin * 2);
    const left = Math.max(viewportLeft + margin, Math.min(rect.left, viewportLeft + viewportWidth - width - margin));
    const availableBelow = viewportTop + viewportHeight - rect.bottom - margin;
    const availableAbove = rect.top - viewportTop - margin;
    const placeAbove = availableBelow < 190 && availableAbove > availableBelow;
    const maxHeight = Math.max(110, Math.min(310, (placeAbove ? availableAbove : availableBelow) - gap));
    list.style.width = `${width}px`;
    list.style.maxHeight = `${maxHeight}px`;
    const renderedHeight = Math.min(list.scrollHeight, maxHeight);
    list.style.left = `${left}px`;
    list.style.top = placeAbove
      ? `${Math.max(viewportTop + margin, rect.top - renderedHeight - gap)}px`
      : `${rect.bottom + gap}px`;
  };

  const updateActive = nextIndex => {
    if (!filtered.length) activeIndex = -1;
    else activeIndex = Math.max(0, Math.min(nextIndex, filtered.length - 1));
    const optionNodes = [...list.querySelectorAll("[role='option']")];
    optionNodes.forEach((node, index) => {
      const active = index === activeIndex;
      node.classList.toggle("active", active);
      node.setAttribute("aria-selected", active ? "true" : "false");
      if (active) {
        input.setAttribute("aria-activedescendant", node.id);
        node.scrollIntoView({ block: "nearest" });
      }
    });
    if (activeIndex < 0) input.removeAttribute("aria-activedescendant");
  };

  const commit = option => {
    if (!option) return;
    const changed = committed !== option.value;
    committed = option.value;
    input.value = option.value;
    input.setCustomValidity("");
    close();
    if (changed) input.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const render = (query, { showAll = false } = {}) => {
    filtered = filterSearchableSelectOptions(available, showAll ? "" : query);
    list.replaceChildren();
    if (!filtered.length) {
      const empty = documentRef.createElement("p");
      empty.className = "ui-searchable-select__empty";
      empty.textContent = noResultsText;
      list.append(empty);
      updateActive(-1);
      return;
    }
    let previousGroup = null;
    for (const [index, option] of filtered.entries()) {
      if (option.group !== previousGroup) {
        const group = documentRef.createElement("span");
        group.className = "ui-searchable-select__group";
        group.textContent = option.group;
        list.append(group);
        previousGroup = option.group;
      }
      const control = documentRef.createElement("button");
      control.id = `${listId}-option-${index}`;
      control.type = "button";
      control.className = "ui-searchable-select__option";
      control.setAttribute("role", "option");
      const copy = documentRef.createElement("span");
      const title = documentRef.createElement("strong");
      title.textContent = option.label;
      copy.append(title);
      if (option.description) {
        const description = documentRef.createElement("small");
        description.textContent = option.description;
        copy.append(description);
      }
      control.append(copy);
      if (normalized(option.value) === normalized(committed)) {
        const mark = documentRef.createElement("b");
        mark.setAttribute("aria-hidden", "true");
        mark.textContent = "✓";
        control.append(mark);
      }
      control.addEventListener("pointerdown", event => event.preventDefault());
      control.addEventListener("click", () => {
        commit(option);
        input.focus();
      });
      list.append(control);
    }
    const selectedIndex = filtered.findIndex(option => normalized(option.value) === normalized(committed));
    updateActive(selectedIndex >= 0 ? selectedIndex : 0);
  };

  const onViewportChange = () => position();
  const onOutsidePointer = event => {
    if (root.contains(event.target) || list.contains(event.target)) return;
    const exact = matchingSearchableSelectOption(available, input.value);
    if (exact) commit(exact);
    else {
      input.value = committed;
      input.setCustomValidity("");
      close();
    }
  };

  function close({ restore = false } = {}) {
    if (restore) input.value = committed;
    if (!open) return;
    open = false;
    input.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    if (typeof list.hidePopover === "function" && list.matches(":popover-open")) list.hidePopover();
    list.hidden = true;
    documentRef.removeEventListener("pointerdown", onOutsidePointer, true);
    documentRef.removeEventListener("scroll", onViewportChange, true);
    documentRef.defaultView.removeEventListener("resize", onViewportChange);
    documentRef.defaultView.visualViewport?.removeEventListener("resize", onViewportChange);
    documentRef.defaultView.visualViewport?.removeEventListener("scroll", onViewportChange);
  }

  function show({ showAll = false } = {}) {
    if (!available.length) return;
    render(input.value, { showAll });
    if (!open) {
      open = true;
      input.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-expanded", "true");
      list.hidden = false;
      if (typeof list.showPopover === "function") list.showPopover();
      documentRef.addEventListener("pointerdown", onOutsidePointer, true);
      documentRef.addEventListener("scroll", onViewportChange, true);
      documentRef.defaultView.addEventListener("resize", onViewportChange);
      documentRef.defaultView.visualViewport?.addEventListener("resize", onViewportChange);
      documentRef.defaultView.visualViewport?.addEventListener("scroll", onViewportChange);
    }
    position();
  }

  input.addEventListener("focus", () => {
    input.select();
    show({ showAll: true });
  });
  input.addEventListener("input", event => {
    event.stopPropagation();
    setValidity();
    show();
    updateActive(0);
  });
  input.addEventListener("keydown", event => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) show({ showAll: true });
      else updateActive(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (!open) show({ showAll: true });
      else commit(filtered[activeIndex] ?? matchingSearchableSelectOption(available, input.value));
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      close({ restore: true });
      return;
    }
    if (event.key === "Tab") {
      const exact = matchingSearchableSelectOption(available, input.value);
      if (exact) commit(exact);
      else close({ restore: true });
    }
  });
  input.addEventListener("invalid", () => show());
  toggle.addEventListener("click", () => {
    if (open) close();
    else {
      input.focus();
      show({ showAll: true });
    }
  });
  setValidity();

  return Object.freeze({
    root,
    input,
    close,
    destroy() {
      close();
      list.remove();
    },
    setOptions(nextOptions) {
      available = normalizeSearchableSelectOptions(nextOptions);
      const exact = matchingSearchableSelectOption(available, input.value);
      if (exact) committed = exact.value;
      setValidity();
      if (open) show({ showAll: true });
    },
    setValue(nextValue) {
      const exact = matchingSearchableSelectOption(available, nextValue);
      committed = exact?.value ?? "";
      input.value = committed;
      setValidity();
    },
    setLabel(nextLabel) {
      const accessibleLabel = String(nextLabel || label);
      input.setAttribute("aria-label", accessibleLabel);
      list.setAttribute("aria-label", `${accessibleLabel} options`);
      toggle.setAttribute("aria-label", `Show ${accessibleLabel.toLocaleLowerCase()} options`);
    },
  });
}
