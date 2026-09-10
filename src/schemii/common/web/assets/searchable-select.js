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
      disabled: Boolean(option.disabled),
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

export function matchingSearchableSelectOption(options, value, { matchLabel = false } = {}) {
  const target = normalized(value);
  const exactValue = options.find(option => normalized(option.value) === target);
  if (exactValue) return exactValue;
  const labels = matchLabel ? options.filter(option => normalized(option.label) === target) : [];
  return labels.length === 1 ? labels[0] : null;
}

export function searchableSelectPlacement(rect, viewport, contentHeight) {
  const margin = Math.min(8, viewport.width / 2, viewport.height / 2);
  const gap = 5;
  const width = Math.max(0, Math.min(Math.max(rect.width, 280), viewport.width - margin * 2));
  const topBound = viewport.top + margin;
  const bottomBound = viewport.top + viewport.height - margin;
  const below = Math.max(0, bottomBound - rect.bottom - gap);
  const above = Math.max(0, rect.top - topBound - gap);
  const placeAbove = below < 190 && above > below;
  const maxHeight = Math.max(0, Math.min(310, viewport.height - margin * 2, placeAbove ? above : below));
  const height = Math.min(contentHeight, maxHeight);
  return {
    width,
    maxHeight,
    left: Math.max(viewport.left + margin, Math.min(rect.left, viewport.left + viewport.width - width - margin)),
    top: Math.max(topBound, Math.min(placeAbove ? rect.top - height - gap : rect.bottom + gap, bottomBound - height)),
  };
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
  displayLabel = false,
  onOpen = null,
  documentRef = document,
} = {}) {
  searchableSelectSequence += 1;
  const listId = `ui-searchable-select-${searchableSelectSequence}`;
  let available = normalizeSearchableSelectOptions(options);
  let committed = matchingSearchableSelectOption(available, value)?.value ?? "";
  let filtered = [];
  let activeIndex = -1;
  let open = false;
  let destroyed = false;
  let dispatchingCommit = false;
  let refreshPromise = null;
  let refreshError = "";
  const displayValue = () => displayLabel
    ? matchingSearchableSelectOption(available, committed)?.label ?? ""
    : committed;
  const matchingInput = () => matchingSearchableSelectOption(available, input.value, { matchLabel: displayLabel });

  const root = documentRef.createElement("div");
  root.className = "ui-searchable-select";
  const input = documentRef.createElement("input");
  input.type = "text";
  input.value = displayValue();
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
    const exact = matchingInput();
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
    const { width, left, top, maxHeight } = searchableSelectPlacement(rect, {
      top: viewportTop, left: viewportLeft, width: viewportWidth, height: viewportHeight,
    }, list.scrollHeight);
    list.style.width = `${width}px`;
    list.style.maxHeight = `${maxHeight}px`;
    list.style.left = `${left}px`;
    list.style.top = `${top}px`;
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
    if (destroyed || !option || option.disabled || refreshPromise) return;
    const changed = committed !== option.value;
    committed = option.value;
    input.value = displayValue();
    input.setCustomValidity("");
    close();
    if (changed) {
      dispatchingCommit = true;
      try { input.dispatchEvent(new Event("change", { bubbles: true })); }
      finally { dispatchingCommit = false; }
    }
  };

  const render = (query, { showAll = false } = {}) => {
    filtered = filterSearchableSelectOptions(available, showAll ? "" : query);
    list.replaceChildren();
    list.setAttribute("aria-busy", String(Boolean(refreshPromise)));
    if (refreshPromise || refreshError) {
      const status = documentRef.createElement("p");
      status.className = "ui-searchable-select__empty";
      status.setAttribute("role", "status");
      status.textContent = refreshPromise ? "Checking available models…" : refreshError;
      list.append(status);
      if (refreshError) {
        const retry = documentRef.createElement("button");
        retry.type = "button";
        retry.className = "ui-searchable-select__option";
        retry.dataset.retry = "";
        retry.textContent = "Retry model check";
        retry.addEventListener("click", () => refresh());
        list.append(retry);
      }
      if (refreshPromise) { filtered = []; updateActive(-1); return; }
    }
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
      control.tabIndex = -1;
      control.className = "ui-searchable-select__option";
      control.setAttribute("role", "option");
      control.disabled = option.disabled;
      if (option.disabled) control.setAttribute("aria-disabled", "true");
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
        input.focus();
        commit(option);
      });
      list.append(control);
    }
    const selectedIndex = filtered.findIndex(option => normalized(option.value) === normalized(committed));
    updateActive(selectedIndex >= 0 ? selectedIndex : 0);
  };

  const onViewportChange = () => position();
  const onOutsidePointer = event => {
    if (root.contains(event.target) || list.contains(event.target)) return;
    const exact = matchingInput();
    if (exact && !exact.disabled && !refreshPromise) commit(exact);
    else {
      input.value = displayValue();
      input.setCustomValidity("");
      close();
    }
  };

  function close({ restore = false } = {}) {
    if (restore) {
      input.value = displayValue();
      input.setCustomValidity("");
    }
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
    if (destroyed || input.disabled || (!available.length && !onOpen)) return;
    render(input.value, { showAll });
    if (!open) {
      // A modal dialog makes the rest of the document inert. A top-layer
      // popover outside that dialog may be visible but cannot receive clicks.
      // Keep ownership inside the dialog while retaining viewport positioning.
      const portal = root.closest?.("dialog[open]") || documentRef.body;
      if (list.parentElement !== portal) portal.append(list);
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
      if (onOpen) refresh();
    }
    position();
  }

  function refresh() {
    if (refreshPromise) return refreshPromise;
    refreshError = "";
    refreshPromise = Promise.resolve().then(onOpen).catch(error => {
      refreshError = error?.message || "Could not check available models.";
    }).finally(() => {
      refreshPromise = null;
      if (open && !destroyed) { render(input.value, { showAll: true }); position(); }
    });
    render(input.value, { showAll: true });
    position();
    return refreshPromise;
  }

  input.addEventListener("focus", () => {
    input.select();
    show({ showAll: true });
  });
  input.addEventListener("click", () => { if (onOpen && !open) show({ showAll: true }); });
  input.addEventListener("input", event => {
    event.stopPropagation();
    setValidity();
    show();
    updateActive(0);
  });
  input.addEventListener("change", event => {
    if (destroyed) {
      event.stopImmediatePropagation();
      return;
    }
    if (dispatchingCommit) {
      // A consumer may open a modal synchronously, blurring the input while
      // our commit event is still dispatching. Suppress that native change:
      // consumers must receive exactly one event for this selection.
      if (event.isTrusted) event.stopImmediatePropagation();
      return;
    }
    event.stopImmediatePropagation();
    const exact = matchingInput();
    if (exact && !exact.disabled && !refreshPromise) commit(exact);
    else close({ restore: true });
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
      else if (refreshError && !filtered.length) refresh();
      else commit(filtered[activeIndex] ?? matchingInput());
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      close({ restore: true });
      return;
    }
    if (event.key === "Tab") {
      if (refreshError && open && !event.shiftKey) {
        event.preventDefault();
        list.querySelector("[data-retry]")?.focus();
        return;
      }
      const exact = matchingInput();
      if (exact && !exact.disabled && !refreshPromise) commit(exact);
      else close({ restore: true });
    }
  });
  input.addEventListener("invalid", () => show());
  list.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault(); event.stopPropagation();
      input.focus(); close({ restore: true });
    }
  });
  toggle.addEventListener("click", () => {
    if (open) close({ restore: true });
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
    getValue: () => committed,
    setDisabled(disabled) {
      input.disabled = Boolean(disabled);
      toggle.disabled = Boolean(disabled);
      if (disabled) close({ restore: true });
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      close();
      list.remove();
    },
    setOptions(nextOptions) {
      available = normalizeSearchableSelectOptions(nextOptions);
      const exact = matchingSearchableSelectOption(available, committed);
      committed = exact?.value ?? "";
      input.value = displayValue();
      setValidity();
      if (open) show({ showAll: true });
    },
    setValue(nextValue) {
      const exact = matchingSearchableSelectOption(available, nextValue);
      committed = exact?.value ?? "";
      input.value = displayValue();
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
