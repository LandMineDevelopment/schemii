const ICON_PATHS = Object.freeze({
  close: '<path d="m5 5 10 10M15 5 5 15"/>',
  sql: '<rect x="3" y="3.5" width="14" height="13" rx="2"/><path d="m6.5 8 2 2-2 2M10.5 12h3"/>',
  database: '<ellipse cx="10" cy="5" rx="6.5" ry="2.5"/><path d="M3.5 5v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5M3.5 10v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-5"/>',
  edit: '<path d="m4 14.5-.5 3 3-.5L16 7.5 12.5 4Z"/><path d="m11 5.5 3.5 3.5"/>',
  earlier: '<path d="m9 5-5 5 5 5M4 10h12"/>',
  later: '<path d="m11 5 5 5-5 5M4 10h12"/>',
  copy: '<rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-7A1.5 1.5 0 0 0 3 5.5v7A1.5 1.5 0 0 0 4.5 14H7"/>',
  link: '<path d="M8.2 11.8 11.8 8.2"/><path d="m6.7 13.3-1.2 1.2a2.8 2.8 0 0 1-4-4l2.7-2.7a2.8 2.8 0 0 1 4 0M13.3 6.7l1.2-1.2a2.8 2.8 0 0 1 4 4l-2.7 2.7a2.8 2.8 0 0 1-4 0"/>',
  check: '<path d="m4 10.5 3.5 3.5L16 5.5"/>',
  duplicate: '<rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-7A1.5 1.5 0 0 0 3 5.5v7A1.5 1.5 0 0 0 4.5 14H7"/>',
  delete: '<path d="M4 6h12M8 3h4l1 3H7l1-3ZM6 6l1 11h6l1-11M9 9v5M11 9v5"/>',
  add: '<path d="M10 4v12M4 10h12"/>',
  refresh: '<path d="M15.5 7A6 6 0 1 0 16 12"/><path d="M15.5 3.5V7H12"/>',
  calendar: '<rect x="3" y="4.5" width="14" height="12.5" rx="2"/><path d="M6 3v3M14 3v3M3 8h14M7 11h.01M10 11h.01M13 11h.01M7 14h.01M10 14h.01"/>',
  schemas: '<path d="m10 3 7 3.5-7 3.5-7-3.5L10 3Z"/><path d="m3 10 7 3.5 7-3.5M3 13.5 10 17l7-3.5"/>',
  search: '<circle cx="8.5" cy="8.5" r="5"/><path d="m12.2 12.2 4.3 4.3"/>',
  more: '<circle cx="5" cy="10" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="15" cy="10" r="1"/>',
  assistant: '<path d="M5 4.5h10v8.3H7.5L5 15.5v-11Z"/><path d="M7.5 7.5h5M7.5 10h3.5"/>',
  history: '<path d="M4.5 5.5h11M4.5 10h11M4.5 14.5h7"/>',
  settings: '<circle cx="10" cy="10" r="3"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4"/>',
  "new-chat": '<path d="M10 4v12M4 10h12"/>',
  workspaces: '<path d="M3 5.5h5l1.7 2H17v8.5H3z"/><path d="M6 11h8M10 8.5v5"/>',
  save: '<path d="M4 3.5h10l2 2v11H4zM7 3.5V8h6V3.5M7 16.5v-5h6v5"/>',
  upload: '<path d="M10 12V3M6.5 6.5 10 3l3.5 3.5M4 14v2h12v-2"/>',
  download: '<path d="M10 3v9M6.5 8.5 10 12l3.5-3.5M4 14v2h12v-2"/>',
  help: '<circle cx="10" cy="10" r="7"/><path d="M7.8 7.6A2.4 2.4 0 0 1 10 6.2c1.4 0 2.4.8 2.4 2 0 1.7-2.4 1.8-2.4 3.5M10 14.5h.01"/>',
  info: '<circle cx="10" cy="10" r="7"/><path d="M10 9v5M10 6h.01"/>',
  drag: '<circle cx="7" cy="5" r="1.25" fill="currentColor" stroke="none"/><circle cx="13" cy="5" r="1.25" fill="currentColor" stroke="none"/><circle cx="7" cy="10" r="1.25" fill="currentColor" stroke="none"/><circle cx="13" cy="10" r="1.25" fill="currentColor" stroke="none"/><circle cx="7" cy="15" r="1.25" fill="currentColor" stroke="none"/><circle cx="13" cy="15" r="1.25" fill="currentColor" stroke="none"/>',
  tables: '<rect x="3.5" y="4" width="13" height="12" rx="1.5"/><path d="M3.5 8h13M8 8v8M12.5 8v8"/>',
  views: '<path d="M5 3.5h7l3 3v10H5zM12 3.5v3h3M8 10h4M8 13h4"/>',
  relationship: '<circle cx="5.5" cy="6" r="2"/><circle cx="14.5" cy="14" r="2"/><path d="M7.5 6h2.2a3 3 0 0 1 3 3v2a3 3 0 0 0 1.8 3"/>',
  key: '<circle cx="6.5" cy="8" r="3"/><path d="m9 10 6 6M12 13l1.8-1.8M14 15l1.8-1.8"/>',
  index: '<path d="M4 4h12M4 8h8M4 12h12M4 16h8"/><path d="m14 14 2 2 3-4"/>',
  undo: '<path d="m8 5-4 4 4 4M4 9h6a5 5 0 0 1 5 5"/>',
  redo: '<path d="m12 5 4 4-4 4M16 9h-6a5 5 0 0 0-5 5"/>',
  fit: '<path d="M7 3H3v4M13 3h4v4M7 17H3v-4M13 17h4v-4"/>',
  "zoom-in": '<circle cx="8.5" cy="8.5" r="5"/><path d="m12.5 12.5 4 4M8.5 6v5M6 8.5h5"/>',
  "zoom-out": '<circle cx="8.5" cy="8.5" r="5"/><path d="m12.5 12.5 4 4M6 8.5h5"/>',
  routines: '<path d="M4 3.5h12v4H4zM4 12.5h12v4H4zM10 7.5v5"/>',
  objects: '<path d="M3.5 3.5h5v5h-5zM11.5 3.5h5v5h-5zM3.5 11.5h5v5h-5zM11.5 11.5h5v5h-5z"/>',
  collapse: '<path d="m12.5 5-5 5 5 5"/>',
  expand: '<path d="m7.5 5 5 5-5 5"/>',
  minimize: '<path d="M5 10h10"/>',
});

export const ICONS = Object.freeze(Object.fromEntries(
  Object.entries(ICON_PATHS).map(([name, paths]) => [name, `<svg viewBox="0 0 20 20" aria-hidden="true">${paths}</svg>`]),
));

function iconMarkup(name) {
  const markup = ICONS[name];
  if (!markup) throw new TypeError(`Unknown icon: ${name}`);
  return markup;
}

export function createIconElement(name, documentRef = document) {
  const template = documentRef.createElement("template");
  template.innerHTML = iconMarkup(name);
  return template.content.firstElementChild;
}

export function decorateIconControl(control, {
  icon,
  label,
  tooltip = label,
  placement = "top",
  className = "",
} = {}) {
  if (!control || typeof label !== "string" || !label.trim()) {
    throw new TypeError("An icon control and accessible label are required");
  }
  control.classList.add("ui-icon-button");
  if (className) control.classList.add(...className.split(/\s+/).filter(Boolean));
  control.dataset.uiIcon = icon;
  control.setAttribute("aria-label", label);
  if (tooltip) control.dataset.uiTooltip = tooltip;
  if (placement) control.dataset.uiTooltipPlacement = placement;
  control.replaceChildren(createIconElement(icon, control.ownerDocument));
  return control;
}

export function createIconButton(options, documentRef = document) {
  const button = documentRef.createElement("button");
  button.type = "button";
  return decorateIconControl(button, options);
}

const STATE_VARIANTS = new Set(["loading", "error"]);

export function renderStatePanel(panel, {
  mark,
  title,
  message,
  variant = null,
  action = null,
} = {}) {
  if (!panel) throw new TypeError("A state panel is required");
  if (typeof title !== "string" || !title.trim()) throw new TypeError("A state title is required");
  if (typeof message !== "string" || !message.trim()) throw new TypeError("A state message is required");
  if (variant !== null && !STATE_VARIANTS.has(variant)) throw new TypeError(`Unsupported state variant: ${variant}`);

  panel.classList.add("ui-state");
  for (const name of STATE_VARIANTS) panel.classList.toggle(name, variant === name);
  const documentRef = panel.ownerDocument;
  const markNode = documentRef.createElement("span");
  markNode.classList.add("ui-state__mark");
  markNode.setAttribute("aria-hidden", "true");
  markNode.textContent = mark ?? "";
  const titleNode = documentRef.createElement("strong");
  titleNode.textContent = title;
  const messageNode = documentRef.createElement("p");
  messageNode.textContent = message;
  panel.replaceChildren(markNode, titleNode, messageNode);
  if (action) panel.append(action);
  return panel;
}

export function createStatePanel({ surface = false, className = "", ...content } = {}, documentRef = document) {
  const panel = documentRef.createElement("div");
  panel.classList.add("ui-state");
  if (surface) panel.classList.add("surface");
  if (className) panel.classList.add(...className.split(/\s+/).filter(Boolean));
  return renderStatePanel(panel, content);
}

export function hydrateIconControls(root = document) {
  // TODO(ui-icon-fallback): Preserve usable icon-control content before JavaScript runs. The HTML controls are currently empty until this hydrator replaces their children; move the essential icon or text fallback into markup and enhance it here without regressing accessible names.
  // TODO(ui-tooltip-placement): Add an inherited placement contract so a vertical control group can declare `right` once. The control-only `top` default currently makes tool-rail tooltips overlap neighboring controls.
  const controls = [...root.querySelectorAll("[data-ui-icon]")];
  for (const control of controls) {
    const label = control.getAttribute("aria-label");
    if (!label) throw new TypeError("Every data-ui-icon control requires an aria-label");
    const tooltip = control.dataset.uiTooltip || control.getAttribute("title") || label;
    const usesNativeTooltip = Boolean(control.closest("dialog"));
    if (usesNativeTooltip) {
      control.setAttribute("title", tooltip);
      delete control.dataset.uiTooltip;
    }
    else control.removeAttribute("title");
    decorateIconControl(control, {
      icon: control.dataset.uiIcon,
      label,
      tooltip: usesNativeTooltip ? null : tooltip,
      placement: control.dataset.uiTooltipPlacement || "top",
    });
  }
  for (const control of root.querySelectorAll("[data-ui-icon-leading]")) {
    control.querySelector(":scope > .ui-leading-icon")?.remove();
    const icon = createIconElement(control.dataset.uiIconLeading, control.ownerDocument);
    icon.classList.add("ui-leading-icon");
    control.prepend(icon);
  }
  return controls;
}

export function setControlLoading(control, loading, { loadingLabel = "Working…" } = {}) {
  if (!control) return;
  if (loading) {
    if (!control.__uiLoadingState) {
      control.__uiLoadingState = {
        disabled: control.disabled,
        ariaDisabled: control.getAttribute("aria-disabled"),
        label: control.getAttribute("aria-label"),
        tooltip: control.dataset.uiTooltip,
        activationGuard: event => {
          event.preventDefault();
          event.stopImmediatePropagation();
        },
      };
      control.addEventListener("click", control.__uiLoadingState.activationGuard, true);
    }
    control.setAttribute("aria-disabled", "true");
    control.setAttribute("aria-busy", "true");
    control.classList.add("ui-control-loading");
    if (loadingLabel) {
      control.setAttribute("aria-label", loadingLabel);
      control.dataset.uiTooltip = loadingLabel;
    }
    return;
  }
  const previous = control.__uiLoadingState;
  control.removeAttribute("aria-busy");
  control.classList.remove("ui-control-loading");
  if (!previous) return;
  control.removeEventListener("click", previous.activationGuard, true);
  control.disabled = previous.disabled;
  if (previous.ariaDisabled === null) control.removeAttribute("aria-disabled");
  else control.setAttribute("aria-disabled", previous.ariaDisabled);
  if (previous.label) control.setAttribute("aria-label", previous.label);
  else control.removeAttribute("aria-label");
  if (previous.tooltip) control.dataset.uiTooltip = previous.tooltip;
  else delete control.dataset.uiTooltip;
  delete control.__uiLoadingState;
}

export async function withLoadingControl(control, options, operation) {
  setControlLoading(control, true, options);
  try {
    return await operation();
  } finally {
    setControlLoading(control, false, options);
  }
}

export function closeDetailsMenus(root = document, { except = null } = {}) {
  for (const menu of root.querySelectorAll(".ui-menu[open]")) {
    if (menu !== except) menu.removeAttribute("open");
  }
}

export function installDetailsMenu(menu) {
  const close = () => menu.removeAttribute("open");
  const onToggle = () => {
    if (menu.open) closeDetailsMenus(menu.ownerDocument, { except: menu });
  };
  const onMenuClick = event => {
    if (!event.target.closest?.("button, a, [role='menuitem']")) return;
    const restoreFocus = menu.contains(menu.ownerDocument.activeElement);
    close();
    queueMicrotask(() => {
      if (restoreFocus) menu.querySelector("summary")?.focus();
    });
  };
  const onDocumentClick = event => {
    if (menu.open && !menu.contains(event.target)) close();
  };
  const onKeydown = event => {
    if (event.key !== "Escape" || !menu.open) return;
    close();
    menu.querySelector("summary")?.focus();
  };
  menu.addEventListener("toggle", onToggle);
  menu.addEventListener("click", onMenuClick);
  menu.ownerDocument.addEventListener("click", onDocumentClick);
  menu.ownerDocument.addEventListener("keydown", onKeydown);
  return Object.freeze({
    destroy() {
      menu.removeEventListener("toggle", onToggle);
      menu.removeEventListener("click", onMenuClick);
      menu.ownerDocument.removeEventListener("click", onDocumentClick);
      menu.ownerDocument.removeEventListener("keydown", onKeydown);
    },
  });
}

function createTooltipController(documentRef) {
  const tooltip = documentRef.createElement("div");
  tooltip.className = "ui-tooltip";
  tooltip.hidden = true;
  tooltip.setAttribute("role", "tooltip");
  documentRef.body.append(tooltip);
  let activeTarget = null;
  let hideTimer = null;

  const position = target => {
    const targetRect = target.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const viewport = documentRef.defaultView;
    const gap = 9;
    const margin = 8;
    let placement = target.dataset.uiTooltipPlacement || "top";
    if (placement === "right" && targetRect.right + gap + tooltipRect.width > viewport.innerWidth - margin) placement = "left";
    if (placement === "left" && targetRect.left - gap - tooltipRect.width < margin) placement = "right";
    if (placement === "top" && targetRect.top - gap - tooltipRect.height < margin) placement = "bottom";
    if (placement === "bottom" && targetRect.bottom + gap + tooltipRect.height > viewport.innerHeight - margin) placement = "top";
    const horizontal = placement === "left" || placement === "right";
    const left = horizontal
      ? (placement === "right" ? targetRect.right + gap : targetRect.left - tooltipRect.width - gap)
      : targetRect.left + (targetRect.width - tooltipRect.width) / 2;
    const top = horizontal
      ? targetRect.top + (targetRect.height - tooltipRect.height) / 2
      : (placement === "bottom" ? targetRect.bottom + gap : targetRect.top - tooltipRect.height - gap);
    tooltip.dataset.placement = placement;
    tooltip.style.left = `${Math.max(margin, Math.min(left, viewport.innerWidth - tooltipRect.width - margin))}px`;
    tooltip.style.top = `${Math.max(margin, Math.min(top, viewport.innerHeight - tooltipRect.height - margin))}px`;
  };
  const show = (target, content = target?.dataset.uiTooltip) => {
    if (!target || !content) return;
    clearTimeout(hideTimer);
    activeTarget = target;
    tooltip.textContent = content;
    tooltip.hidden = false;
    tooltip.classList.remove("visible");
    position(target);
    documentRef.defaultView.requestAnimationFrame(() => {
      if (activeTarget === target) tooltip.classList.add("visible");
    });
  };
  const hide = () => {
    activeTarget = null;
    tooltip.classList.remove("visible");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { tooltip.hidden = true; }, 150);
  };
  return { tooltip, show, hide, get activeTarget() { return activeTarget; } };
}

export function isOverflowingText(target) {
  if (!target) return false;
  return target.scrollWidth > target.clientWidth || target.scrollHeight > target.clientHeight;
}

export function installVisualViewportSizing(documentRef = document) {
  const windowRef = documentRef.defaultView;
  const style = documentRef.documentElement?.style;
  if (!windowRef || !style) return Object.freeze({ destroy() {} });
  const visualViewport = windowRef.visualViewport;
  const update = () => {
    const height = visualViewport?.height || windowRef.innerHeight;
    const offsetTop = visualViewport?.offsetTop || 0;
    style.setProperty("--ui-visual-viewport-height", `${height}px`);
    style.setProperty("--ui-visual-viewport-center-y", `${offsetTop + height / 2}px`);
  };
  update();
  windowRef.addEventListener("resize", update);
  visualViewport?.addEventListener("resize", update);
  visualViewport?.addEventListener("scroll", update);
  return Object.freeze({
    destroy() {
      windowRef.removeEventListener("resize", update);
      visualViewport?.removeEventListener("resize", update);
      visualViewport?.removeEventListener("scroll", update);
      style.removeProperty("--ui-visual-viewport-height");
      style.removeProperty("--ui-visual-viewport-center-y");
    },
  });
}

export function initializeUi(root = document) {
  hydrateIconControls(root);
  const documentRef = root.ownerDocument || root;
  const viewportSizing = installVisualViewportSizing(documentRef);
  const tooltip = createTooltipController(documentRef);
  let touchHideTimer = null;
  const resolveTarget = (target, { touch = false } = {}) => {
    const candidate = target?.closest?.("[data-ui-tooltip], [data-ui-tooltip-overflow]");
    if (!candidate) return null;
    if (touch && candidate.dataset.uiTooltipTouch === undefined) return null;
    const content = candidate.dataset.uiTooltip
      || (isOverflowingText(candidate) ? candidate.dataset.uiTooltipOverflow : null);
    return content ? { content, target: candidate } : null;
  };
  const onPointerOver = event => {
    const resolved = resolveTarget(event.target);
    if (resolved && resolved.target !== tooltip.activeTarget) tooltip.show(resolved.target, resolved.content);
  };
  const onPointerOut = event => {
    if (tooltip.activeTarget && !tooltip.activeTarget.contains(event.relatedTarget)) tooltip.hide();
  };
  const onFocusIn = event => {
    const resolved = resolveTarget(event.target);
    if (resolved) tooltip.show(resolved.target, resolved.content);
  };
  const onFocusOut = event => {
    if (tooltip.activeTarget && !tooltip.activeTarget.contains(event.relatedTarget)) tooltip.hide();
  };
  const hideTooltip = () => {
    clearTimeout(touchHideTimer);
    touchHideTimer = null;
    tooltip.hide();
  };
  const onPointerUp = event => {
    if (event.pointerType === "mouse") return;
    const resolved = resolveTarget(event.target, { touch: true });
    if (!resolved) return;
    tooltip.show(resolved.target, resolved.content);
    clearTimeout(touchHideTimer);
    touchHideTimer = setTimeout(hideTooltip, 2_500);
  };
  const hideTooltipOnActivation = event => {
    if (event.key === "Enter" || event.key === " ") tooltip.hide();
  };
  documentRef.addEventListener("pointerover", onPointerOver);
  documentRef.addEventListener("pointerout", onPointerOut);
  documentRef.addEventListener("focusin", onFocusIn);
  documentRef.addEventListener("focusout", onFocusOut);
  documentRef.addEventListener("pointerdown", hideTooltip);
  documentRef.addEventListener("pointerup", onPointerUp);
  documentRef.addEventListener("keydown", hideTooltipOnActivation);
  documentRef.addEventListener("scroll", hideTooltip, true);
  const menus = [...root.querySelectorAll(".ui-menu")].map(installDetailsMenu);
  return Object.freeze({
    destroy() {
      viewportSizing.destroy();
      menus.forEach(menu => menu.destroy());
      documentRef.removeEventListener("pointerover", onPointerOver);
      documentRef.removeEventListener("pointerout", onPointerOut);
      documentRef.removeEventListener("focusin", onFocusIn);
      documentRef.removeEventListener("focusout", onFocusOut);
      documentRef.removeEventListener("pointerdown", hideTooltip);
      documentRef.removeEventListener("pointerup", onPointerUp);
      documentRef.removeEventListener("keydown", hideTooltipOnActivation);
      documentRef.removeEventListener("scroll", hideTooltip, true);
      clearTimeout(touchHideTimer);
      tooltip.tooltip.remove();
    },
  });
}

const DOCK_STATES = new Set(["expanded", "minimized", "dismissed"]);

export class DockPane {
  constructor({
    container,
    pane,
    body,
    toggle,
    dismiss = null,
    side = "right",
    initialState = "expanded",
    expandedLabel = "Minimize panel",
    minimizedLabel = "Expand panel",
    getRestoreFocusTarget = () => null,
    onStateChange = null,
  }) {
    if (!container || !pane || !body || !toggle) throw new TypeError("DockPane requires a container, pane, body, and toggle");
    if (!DOCK_STATES.has(initialState)) throw new TypeError(`Unsupported dock pane state: ${initialState}`);
    this.container = container;
    this.pane = pane;
    this.body = body;
    this.toggle = toggle;
    this.dismissButton = dismiss;
    this.side = side;
    this.state = initialState;
    this.available = true;
    this.expandedLabel = expandedLabel;
    this.minimizedLabel = minimizedLabel;
    this.getRestoreFocusTarget = getRestoreFocusTarget;
    this.onStateChange = onStateChange;
    this.onToggle = () => this.toggleState();
    this.onDismiss = () => this.dismiss();
    toggle.addEventListener("click", this.onToggle);
    dismiss?.addEventListener("click", this.onDismiss);
    this.render();
  }

  containerState() {
    return this.available ? this.state : "unavailable";
  }

  render() {
    const state = this.containerState();
    this.pane.hidden = !this.available;
    this.pane.dataset.uiDockState = state;
    this.pane.dataset.uiDockSide = this.side;
    this.container.dataset[`${this.side}PaneState`] = state;
    const expanded = state === "expanded";
    this.toggle.setAttribute("aria-expanded", String(expanded));
    this.toggle.setAttribute("aria-label", expanded ? this.expandedLabel : this.minimizedLabel);
    this.toggle.dataset.uiTooltip = expanded ? this.expandedLabel : this.minimizedLabel;
    this.toggle.disabled = !this.available;
    this.body.setAttribute("aria-hidden", String(!expanded));
    this.body.inert = !expanded;
    if (typeof this.onStateChange === "function") this.onStateChange(state);
  }

  restoreFocus({ forceExternal = false } = {}) {
    const active = this.pane.ownerDocument?.activeElement;
    if (!active || !this.pane.contains(active)) return;
    if (!forceExternal && !this.body.contains(active)) return;
    const target = this.getRestoreFocusTarget();
    if (target && !this.pane.contains(target)) target.focus({ preventScroll: true });
    else if (!forceExternal) this.toggle.focus({ preventScroll: true });
    else active.blur?.();
  }

  setState(state) {
    if (!DOCK_STATES.has(state)) throw new TypeError(`Unsupported dock pane state: ${state}`);
    if (state === this.state && this.available) return;
    if (state === "dismissed") this.restoreFocus({ forceExternal: true });
    else if (state !== "expanded") this.restoreFocus();
    this.available = true;
    this.state = state;
    this.render();
  }

  setAvailable(available, { reset = false } = {}) {
    const next = Boolean(available);
    if (reset) this.state = "expanded";
    if (next === this.available && !reset) return;
    if (!next) this.restoreFocus({ forceExternal: true });
    this.available = next;
    this.render();
  }

  reveal({ expand = false } = {}) {
    this.available = true;
    if (expand || this.state === "dismissed") this.state = "expanded";
    this.render();
  }

  expand() { this.setState("expanded"); }
  minimize() { this.setState("minimized"); }
  dismiss() { this.setState("dismissed"); }

  toggleState() {
    if (!this.available) return;
    this.setState(this.state === "expanded" ? "minimized" : "expanded");
  }

  destroy() {
    this.toggle.removeEventListener("click", this.onToggle);
    this.dismissButton?.removeEventListener("click", this.onDismiss);
  }
}

export function downloadContent(content, filename, type = "application/octet-stream") {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  try {
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}
